"""Plodine – https://www.plodine.hr/info-o-cijenama

Jedan ZIP po danu (ime sadrži datum DD_MM_YYYY), unutra CSV po poslovnici,
delimiter `;`, decimalni zarez:

    SUPERMARKET_ULICA_FRANJE_TUDJMANA_4_10450_JASTREBARSKO_064_1_19112026020002.csv
    <tip>_<adresa..>_<pošt. broj>_<grad..>_<šifra>_<n>_<timestamp>
"""
from __future__ import annotations

import html
import re
from datetime import date
from urllib.parse import urljoin

from .base import Adapter, RawFile, StoreInfo, StorePrices, title
from .csvutil import parse_price_csv

_ZIP = re.compile(r'href="([^"]+\.zip)"', re.I)
_KINDS = ("HIPERMARKET", "SUPERMARKET", "MARKET", "TRGOVINA")


class PlodineAdapter(Adapter):
    code = "plodine"
    name = "Plodine"
    index_url = "https://www.plodine.hr/info-o-cijenama"

    def discover(self, day: date) -> list[str]:
        r = self.get(self.index_url)
        links = [urljoin(self.index_url, html.unescape(h)) for h in _ZIP.findall(r.text)]
        needle = day.strftime("%d_%m_%Y")
        return [u for u in links if needle in u][:1]

    @staticmethod
    def store_from_filename(filename: str) -> StoreInfo:
        stem = re.sub(r"\.csv$", "", filename.rsplit("/", 1)[-1], flags=re.I)
        parts = stem.split("_")
        kind = parts[0] if parts[0].upper() in _KINDS else None
        body = parts[1:] if kind else parts
        pc_idx = next((i for i, p in enumerate(body) if re.fullmatch(r"\d{5}", p)), None)
        if pc_idx is None:
            return StoreInfo(code=stem, name=title(kind))
        address = " ".join(body[:pc_idx])
        city_parts, code = [], stem
        for p in body[pc_idx + 1 :]:
            if p.isdigit():
                code = p
                break
            city_parts.append(p)
        return StoreInfo(code=code, name=title(kind), address=title(address),
                         postcode=body[pc_idx], city=title(" ".join(city_parts)))

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
