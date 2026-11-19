"""Kaufland – https://www.kaufland.hr/akcije-novosti/popis-mpc.html

Stranica popis datoteka učitava iz JSON-a (asset lista) na koji pokazuje
HTML; CSV-ovi su po poslovnici, delimiter TAB, UTF-8:

    Hipermarket_Velika_Gorica_Zagrebacka_ulica_44_4630_19112026_7-30.csv
    <tip>_<grad>_<adresa..>_<šifra poslovnice>_<DDMMYYYY>_<HH-MM>

Zaglavlja su skraćena ("maloprod.cijena(EUR)", "akc.cijena, A=akcija",
"cijena jed.mj.(EUR)") – pokriva ih mapiranje u csvutil.
"""
from __future__ import annotations

import html
import json
import re
from datetime import date
from urllib.parse import unquote, urljoin

from ..geo import find_city
from .base import Adapter, RawFile, StoreInfo, StorePrices, title
from .csvutil import parse_price_csv

_JSON = re.compile(r"""["']([^"']+?\.json)["']""", re.I)
_CSV = re.compile(r"""["']([^"'\s]+?\.csv)["']""", re.I)


def _walk_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)


class KauflandAdapter(Adapter):
    code = "kaufland"
    name = "Kaufland"
    index_url = "https://www.kaufland.hr/akcije-novosti/popis-mpc.html"

    def discover(self, day: date) -> list[str]:
        page = html.unescape(self.get(self.index_url).text)
        found = set(_CSV.findall(page))
        for j in _JSON.findall(page):
            if "asset" not in j.lower():
                continue
            try:
                data = self.get(urljoin(self.index_url, j)).json()
            except (ValueError, Exception):  # noqa: BLE001 – loš JSON = nema linkova
                continue
            # samo putanje/URL-ovi; "label" polja su samo imena datoteka
            found.update(s for s in _walk_strings(data)
                         if s.lower().endswith(".csv") and "/" in s)
        needle = day.strftime("%d%m%Y")
        return sorted(urljoin(self.index_url, u) for u in found if needle in unquote(u))

    @staticmethod
    def store_from_filename(filename: str) -> StoreInfo:
        stem = re.sub(r"\.csv$", "", unquote(filename).rsplit("/", 1)[-1], flags=re.I)
        parts = [p for p in stem.split("_") if p]
        # rep: <šifra>_<DDMMYYYY>_<HH-MM>
        if parts and re.fullmatch(r"\d{1,2}[-.:]\d{2}", parts[-1]):
            parts.pop()
        if parts and re.fullmatch(r"\d{8}", parts[-1]):
            parts.pop()
        code = parts.pop() if parts and re.fullmatch(r"\d{3,6}", parts[-1]) else stem
        kind, body = (parts[0], parts[1:]) if parts else (None, [])
        hit = find_city(body)
        if hit:
            city, i, j = hit
            address = " ".join(body[:i] + body[j:])
        else:
            city, address = None, " ".join(body)
        return StoreInfo(code=code, name=title(kind), address=title(address) or None, city=city)

    def parse(self, raw: RawFile):
        if not raw.name.lower().endswith(".csv"):
            return
        rows, warnings, enc = parse_price_csv(raw.data)
        yield StorePrices(chain=self.code, store=self.store_from_filename(raw.name), rows=rows,
                          source_file=raw.name, encoding=enc, warnings=warnings)
