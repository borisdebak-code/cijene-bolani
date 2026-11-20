"""Sučelje za adaptere trgovaca.

Svaki trgovac = jedna datoteka u `app/adapters/` s jednom podklasom
`Adapter`. Registar ih pronalazi automatski (vidi `__init__.py`).

Tok rada:
    discover(day)        -> lista URL-ova službenih datoteka za taj dan
    download(url)        -> lista RawFile (ZIP se automatski raspakira)
    parse(raw_file)      -> StorePrices (poslovnica + redovi cijena)
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import ClassVar, Iterable

import httpx

log = logging.getLogger("cijene.adapters")


class AdapterError(Exception):
    """Greška u adapteru (npr. nema cjenika za taj dan)."""


class FormatChanged(AdapterError):
    """Datoteka postoji, ali joj se format promijenio (stupci, struktura)."""


@dataclass(slots=True)
class PriceRow:
    name: str
    regular_price: Decimal
    code: str | None = None
    brand: str | None = None
    quantity: str | None = None
    unit: str | None = None
    barcode: str | None = None
    category: str | None = None
    unit_price: Decimal | None = None
    special_price: Decimal | None = None
    lowest_30d: Decimal | None = None
    anchor_price: Decimal | None = None
    #: vrsta posebnog oblika prodaje ("Akcijska prodaja", "Rasprodaja"...)
    sale_type: str | None = None
    #: do kada vrijedi akcijska cijena, ako je trgovac objavi
    valid_until: date | None = None
    #: niža cijena uz karticu vjernosti (MultiPlus, Kaufland Card, Lidl Plus...)
    card_price: Decimal | None = None
    card_name: str | None = None


@dataclass(slots=True)
class StoreInfo:
    code: str
    name: str | None = None          # npr. "Supermarket"
    address: str | None = None
    postcode: str | None = None
    city: str | None = None


@dataclass(slots=True)
class StorePrices:
    chain: str                       # kod trgovca (adapter.code ili kod iz arhive)
    store: StoreInfo
    rows: list[PriceRow]
    source_file: str
    encoding: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RawFile:
    name: str
    data: bytes
    url: str | None = None


class Adapter(ABC):
    #: kratki kod trgovca (koristi se u bazi i URL-ovima)
    code: ClassVar[str]
    #: ime za prikaz
    name: ClassVar[str]
    #: False za "meta" adaptere (npr. cijene.dev) koji se ne pokreću po defaultu
    enabled_by_default: ClassVar[bool] = True
    #: raspakirati ZIP prije parse()? (False ako adapter treba cijelu arhivu)
    unpack_zip: ClassVar[bool] = True
    #: upozori ako je redaka < 50 % prošlog uspješnog preuzimanja (promjena formata?)
    row_drop_check: ClassVar[bool] = True

    user_agent = "cijene-hr/0.1 (+price comparison; official price lists)"
    timeout = httpx.Timeout(60.0, connect=15.0)

    def __init__(self, client: httpx.Client | None = None):
        self._client = client

    # ---- HTTP ---------------------------------------------------------- #
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client

    def get(self, url: str, **kw) -> httpx.Response:
        r = self.client.get(url, **kw)
        r.raise_for_status()
        return r

    # ---- koraci koje adapter implementira ------------------------------ #
    @abstractmethod
    def discover(self, day: date) -> list[str]:
        """Vrati URL-ove službenih datoteka cjenika za zadani dan."""

    def download(self, url: str) -> list[RawFile]:
        r = self.get(url)
        name = filename_from_response(r, url)
        return self.prepare(name, r.content, url)

    def prepare(self, name: str, data: bytes, url: str | None = None) -> list[RawFile]:
        """Preuzeti/lokalni bajtovi -> datoteke za parse()."""
        if self.unpack_zip:
            return unpack(name, data, url)
        return [RawFile(name=name, data=data, url=url)]

    @abstractmethod
    def parse(self, raw: RawFile) -> Iterable[StorePrices]:
        """Parsiraj jednu datoteku. Većina trgovaca: 1 datoteka = 1 poslovnica."""

    # ---- pomoćno ------------------------------------------------------- #
    def fetch(self, day: date) -> Iterable[RawFile]:
        urls = self.discover(day)
        if not urls:
            raise AdapterError(f"{self.code}: nema cjenika za {day.isoformat()}")
        for url in urls:
            yield from self.download(url)


# --------------------------------------------------------------------------- #

def filename_from_response(r: httpx.Response, url: str) -> str:
    cd = r.headers.get("content-disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
    if m:
        from urllib.parse import unquote

        return unquote(m.group(1))
    from urllib.parse import unquote, urlparse, parse_qs

    q = parse_qs(urlparse(url).query)
    if "title" in q:
        return q["title"][0]
    return unquote(urlparse(url).path.rsplit("/", 1)[-1]) or "cjenik"


def unpack(name: str, data: bytes, url: str | None = None) -> list[RawFile]:
    """Ako je ZIP, vrati sve datoteke iz njega; inače samu datoteku."""
    if data[:4] == b"PK\x03\x04":
        out = []
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                inner = info.filename
                # ZIP-ovi s Windowsa često imaju cp437/cp852 imena
                if not info.flag_bits & 0x800:
                    try:
                        inner = inner.encode("cp437").decode("cp852")
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass
                out.extend(unpack(inner, zf.read(info), url))
        return out
    return [RawFile(name=name, data=data, url=url)]


_POSTCODE = re.compile(r"^\d{5}$")


def split_address(parts: list[str]) -> tuple[str | None, str | None, str | None]:
    """Iz liste dijelova ['ULICA', 'BROJ', '10000', 'ZAGREB'] izvuci
    (adresa, poštanski broj, grad)."""
    for i, p in enumerate(parts):
        if _POSTCODE.match(p.strip()):
            address = " ".join(x.strip() for x in parts[:i]).strip() or None
            city = " ".join(x.strip() for x in parts[i + 1 :]).strip() or None
            return address, p.strip(), city
    return " ".join(parts).strip() or None, None, None


def title(s: str | None) -> str | None:
    if not s:
        return s
    small = {"i", "u", "na", "od", "do", "kod", "bb", "b.b."}
    words = s.lower().split()
    return " ".join(w if w in small and i else w[:1].upper() + w[1:] for i, w in enumerate(words))
