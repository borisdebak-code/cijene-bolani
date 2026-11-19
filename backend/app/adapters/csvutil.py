"""Zajednički alati za čitanje službenih cjenika (CSV).

Trgovci objavljuju datoteke u različitim encodinzima (UTF-8, UTF-8 s BOM-om,
Windows-1250), s različitim delimiterima (`,` `;` `|` TAB) i decimalnim
zarezom ili točkom. Ovdje je sve to na jednom mjestu, tako da adapteri
trebaju samo opisati gdje se nalaze datoteke i kako se iz imena datoteke
čita poslovnica.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from .base import FormatChanged, PriceRow

# --------------------------------------------------------------------------- #
# Encoding
# --------------------------------------------------------------------------- #

def decode_bytes(data: bytes) -> tuple[str, str]:
    """Vrati (tekst, encoding). Redoslijed: UTF-8 (s/bez BOM) -> Windows-1250.

    Windows-1250 može dekodirati gotovo svaki niz bajtova, pa ga probamo
    zadnjeg. UTF-16 prepoznajemo po BOM-u (neki ERP izvozi ga koriste).
    """
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    return data.decode("cp1250", errors="replace"), "cp1250"


# --------------------------------------------------------------------------- #
# Brojevi
# --------------------------------------------------------------------------- #

_NUM_CLEAN = re.compile(r"[^\d,.\-]")


def parse_decimal(value: str | None) -> Decimal | None:
    """'1,99' / '1.99' / '1.234,56' / '1,234.56' / '€ 2,49' / '' -> Decimal|None."""
    if value is None:
        return None
    s = _NUM_CLEAN.sub("", str(value).strip())
    if not s or s in {"-", ".", ","}:
        return None
    if "," in s and "." in s:
        # zadnji separator je decimalni
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # samo zarez: decimalni, osim ako izgleda kao tisućice (1,234)
        head, _, tail = s.rpartition(",")
        if s.count(",") > 1:
            s = s.replace(",", "")
        else:
            s = f"{head}.{tail}"
    elif s.count(".") > 1:
        head, _, tail = s.rpartition(".")
        s = head.replace(".", "") + "." + tail
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if d < 0:
        return None
    return d


# --------------------------------------------------------------------------- #
# Zaglavlja
# --------------------------------------------------------------------------- #

def strip_accents(s: str) -> str:
    s = s.replace("đ", "dj").replace("Đ", "Dj")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_header(h: str) -> str:
    h = strip_accents(h or "").lower()
    h = re.sub(r"[^a-z0-9]+", " ", h)
    return h.strip()


# Redoslijed je bitan: specifičnija pravila prije općenitijih.
# Svako pravilo: (polje, [riječi koje sve moraju biti prisutne], [riječi koje ne smiju])
_HEADER_RULES: list[tuple[str, list[str], list[str]]] = [
    ("special_price", ["posebn"], []),
    ("special_price", ["akcij"], []),
    ("lowest_30d", ["najniz"], []),
    ("anchor_price", ["sidren"], []),
    ("unit_price", ["cijena", "jedinic"], []),
    ("unit_price", ["cijena", "jm"], []),
    ("unit_price", ["cijena", "kg"], []),
    ("unit_price", ["cijena", "jed", "mj"], []),        # Kaufland: "cijena jed.mj.(EUR)"
    ("regular_price", ["maloprod"], []),                # "maloprodajna" i "maloprod."
    ("regular_price", ["mpc"], []),
    ("regular_price", ["cijena"], []),
    ("unit", ["jedinic", "mjer"], []),
    ("unit", ["jm"], []),
    ("quantity", ["neto"], []),
    ("quantity", ["kolicin"], []),
    ("quantity", ["gramaz"], []),
    ("barcode", ["barkod"], []),
    ("barcode", ["bar", "kod"], []),
    ("barcode", ["ean"], []),
    ("barcode", ["gtin"], []),
    ("category", ["kategorij"], []),
    ("category", ["grupa"], []),
    ("brand", ["marka"], []),
    ("brand", ["brand"], []),
    ("brand", ["proizvodjac"], []),
    ("code", ["sifra"], []),
    ("code", ["artikl", "id"], []),
    ("name", ["naziv"], []),
    ("name", ["opis"], []),
]

REQUIRED_FIELDS = {"name", "regular_price"}


def map_headers(headers: list[str]) -> dict[str, int]:
    """Mapira stupce na kanonska polja. Baca FormatChanged ako nedostaju
    obavezni stupci – to je signal da je trgovac promijenio format."""
    mapping: dict[str, int] = {}
    for idx, raw in enumerate(headers):
        words = norm_header(raw)
        tokens = words.split()
        for field, must, must_not in _HEADER_RULES:
            if field in mapping:
                continue
            if all(any(t.startswith(m) for t in tokens) for m in must) and not any(
                any(t.startswith(m) for t in tokens) for m in must_not
            ):
                mapping[field] = idx
                break
    missing = REQUIRED_FIELDS - mapping.keys()
    if missing:
        raise FormatChanged(
            f"Nedostaju obavezni stupci {sorted(missing)}; zaglavlje: {headers!r}"
        )
    return mapping


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #

def sniff_delimiter(header_line: str) -> str:
    counts = {d: header_line.count(d) for d in (";", ",", "\t", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def clean_cell(v: str | None) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", " ", v.replace("\xa0", " ")).strip()


def parse_price_csv(data: bytes) -> tuple[list[PriceRow], list[str], str]:
    """Parsira CSV cjenik u listu PriceRow.

    Vraća (redovi, upozorenja, encoding). Neispravni pojedinačni redovi ne
    ruše cijelu datoteku – zapisuju se u upozorenja.
    """
    text, encoding = decode_bytes(data)
    lines = text.splitlines()
    # preskoči prazne linije na početku
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        raise FormatChanged("Prazna datoteka")
    delimiter = sniff_delimiter(lines[0])
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter, quotechar='"')
    headers = [clean_cell(h) for h in next(reader)]
    mapping = map_headers(headers)

    rows: list[PriceRow] = []
    warnings: list[str] = []

    def get(cells: list[str], field: str) -> str:
        i = mapping.get(field)
        if i is None or i >= len(cells):
            return ""
        return clean_cell(cells[i])

    for lineno, cells in enumerate(reader, start=2):
        if not cells or not any(c.strip() for c in cells):
            continue
        name = get(cells, "name")
        regular = parse_decimal(get(cells, "regular_price"))
        special = parse_decimal(get(cells, "special_price"))
        if not name:
            warnings.append(f"red {lineno}: nema naziva")
            continue
        if regular is None and special is None:
            warnings.append(f"red {lineno}: nema cijene ({name!r})")
            continue
        rows.append(
            PriceRow(
                code=get(cells, "code") or None,
                name=name,
                brand=get(cells, "brand") or None,
                quantity=get(cells, "quantity") or None,
                unit=get(cells, "unit") or None,
                barcode=get(cells, "barcode") or None,
                category=get(cells, "category") or None,
                regular_price=regular if regular is not None else special,
                unit_price=parse_decimal(get(cells, "unit_price")),
                special_price=special if (special is not None and special != regular) else None,
                lowest_30d=parse_decimal(get(cells, "lowest_30d")),
                anchor_price=parse_decimal(get(cells, "anchor_price")),
            )
        )
    return rows, warnings, encoding
