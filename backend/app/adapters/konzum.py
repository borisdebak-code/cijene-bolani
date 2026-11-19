"""Konzum – https://www.konzum.hr/cjenici

Stranica s popisom cjenika prima ?date=YYYY-MM-DD&page=N i sadrži linkove
oblika /cjenici/download?title=<IME DATOTEKE>. Svaka datoteka je CSV za
jednu poslovnicu, ime nosi podatke o poslovnici:

    SUPERMARKET,PUT GAJEVA 2 10000 ZAGREB,0204,4371,19.11.2026, 05-20.CSV
    <tip>,<adresa + pošt. broj + grad>,<šifra poslovnice>,<interno>,<datum>,<vrijeme>
"""
from __future__ import annotations

import html
import re
from datetime import date
from urllib.parse import urljoin

from .base import Adapter, RawFile, StoreInfo, StorePrices, title
from .csvutil import parse_price_csv

_LINK = re.compile(r'href="([^"]*?/cjenici/download\?[^"]+)"', re.I)
_ADDR = re.compile(r"^(?P<address>.*?)\s+(?P<postcode>\d{5})\s+(?P<city>.+)$")


class KonzumAdapter(Adapter):
    code = "konzum"
    name = "Konzum"
    base_url = "https://www.konzum.hr/cjenici"
    max_pages = 60

    def discover(self, day: date) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for page in range(1, self.max_pages + 1):
            r = self.get(self.base_url, params={"date": day.isoformat(), "page": page})
            new = [
                urljoin(self.base_url, html.unescape(h))
                for h in _LINK.findall(r.text)
            ]
            new = list(dict.fromkeys(u for u in new if u not in seen))
            if not new:
                break
            seen.update(new)
            urls.extend(new)
        return urls

    @staticmethod
    def store_from_filename(filename: str) -> StoreInfo:
        stem = re.sub(r"\.csv$", "", filename.rsplit("/", 1)[-1], flags=re.I)
        parts = [p.strip() for p in stem.split(",")]
        if len(parts) < 3:
            return StoreInfo(code=stem)
        kind, addr, store_code = parts[0], parts[1], parts[2]
        m = _ADDR.match(addr)
        if m:
            return StoreInfo(
                code=store_code,
                name=title(kind),
                address=title(m["address"]),
                postcode=m["postcode"],
                city=title(m["city"]),
            )
        return StoreInfo(code=store_code, name=title(kind), address=title(addr))

    def parse(self, raw: RawFile):
        rows, warnings, enc = parse_price_csv(raw.data)
        yield StorePrices(
            chain=self.code,
            store=self.store_from_filename(raw.name),
            rows=rows,
            source_file=raw.name,
            encoding=enc,
            warnings=warnings,
        )
