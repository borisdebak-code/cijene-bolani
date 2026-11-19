"""Lidl – https://tvrtka.lidl.hr/cijene

Na stranici je jedan ZIP po danu (npr.
Popis_cijena_po_trgovinama_na_dan_19_11_2026.zip), unutra CSV po poslovnici:

    Supermarket 265_Ulica Franje Glada_13_40323_Prelog_1_19.11.2026_7.15h.csv
    <tip> <šifra>_<ulica>_<broj>_<pošt. broj>_<grad>_<...>_<datum>_<vrijeme>

Datoteke su obično u Windows-1250 encodingu.
"""
from __future__ import annotations

import html
import re
from datetime import date
from urllib.parse import urljoin

from .base import Adapter, RawFile, StoreInfo, StorePrices, split_address, title
from .csvutil import parse_price_csv

_ZIP = re.compile(r'href="([^"]+\.zip)"', re.I)
_DATE_TAIL = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")
_TIME_TAIL = re.compile(r"^\d{1,2}[.:]\d{2}h?$", re.I)


class LidlAdapter(Adapter):
    code = "lidl"
    name = "Lidl"
    index_url = "https://tvrtka.lidl.hr/cijene"

    def discover(self, day: date) -> list[str]:
        r = self.get(self.index_url)
        links = [urljoin(self.index_url, html.unescape(h)) for h in _ZIP.findall(r.text)]
        needle = day.strftime("%d_%m_%Y")
        return [u for u in links if needle in u][:1]

    @staticmethod
    def store_from_filename(filename: str) -> StoreInfo:
        stem = re.sub(r"\.csv$", "", filename.rsplit("/", 1)[-1], flags=re.I)
        parts = stem.split("_")
        head = parts[0]
        m = re.match(r"^(?P<kind>.*?)\s*(?P<code>\d+)$", head)
        kind, code = (m["kind"], m["code"]) if m else (head, head)
        # odbaci rep: ..._<n>_<dd.mm.yyyy>_<hh.mmh>
        rest = parts[1:]
        while rest and (_DATE_TAIL.match(rest[-1]) or _TIME_TAIL.match(rest[-1])
                        or (rest[-1].isdigit() and len(rest[-1]) < 5)):
            rest.pop()
        address, postcode, city = split_address(rest)
        return StoreInfo(code=code, name=title(kind) or None, address=title(address),
                         postcode=postcode, city=title(city))

    def parse(self, raw: RawFile):
        if not raw.name.lower().endswith(".csv"):
            return
        rows, warnings, enc = parse_price_csv(raw.data)
        yield StorePrices(
            chain=self.code,
            store=self.store_from_filename(raw.name),
            rows=rows,
            source_file=raw.name,
            encoding=enc,
            warnings=warnings,
        )
