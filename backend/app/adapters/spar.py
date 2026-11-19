"""Spar i Interspar – https://www.spar.hr/usluge/cjenici

Spar Hrvatska objavljuje dnevni popis kao JSON:

    https://www.spar.hr/datoteke_cjenici/Cjenik{YYYYMMDD}.json
    {"files": [{"name": "...csv", "URL": "https://..."}]}

Jedna datoteka po poslovnici, delimiter `;`, obično Windows-1250:

    hipermarket_velika_gorica_zagrebacka_ulica_1_8740_interspar_velika_gorica_0034_20261119_0330.csv
    <tip>_<grad>_<adresa..>_<interno>_<spar|interspar>_<naziv..>_<šifra>_<YYYYMMDD>_<HHMM>

Interspar i Spar su isti izvor, ali se u aplikaciji prikazuju kao dva
trgovca – adapter za svaku poslovnicu bira `interspar` ili `spar`.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import unquote

from ..geo import find_city
from .base import Adapter, RawFile, StoreInfo, StorePrices, title
from .csvutil import parse_price_csv


def _walk(obj):
    if isinstance(obj, dict):
        name = obj.get("name") or obj.get("Name")
        url = obj.get("URL") or obj.get("url")
        if isinstance(url, str):
            yield url, name
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


class SparAdapter(Adapter):
    code = "spar"
    name = "Spar / Interspar"
    list_url = "https://www.spar.hr/datoteke_cjenici/Cjenik{ymd}.json"

    def discover(self, day: date) -> list[str]:
        r = self.client.get(self.list_url.format(ymd=day.strftime("%Y%m%d")))
        if r.status_code == 404:
            return []
        r.raise_for_status()
        return [u for u, _ in _walk(r.json()) if u.lower().split("?")[0].endswith(".csv")]

    @staticmethod
    def chain_for(filename: str) -> str:
        return "interspar" if "interspar" in filename.lower() else "spar"

    @staticmethod
    def store_from_filename(filename: str) -> StoreInfo:
        stem = re.sub(r"\.csv$", "", unquote(filename).rsplit("/", 1)[-1], flags=re.I)
        parts = [p for p in stem.split("_") if p]
        if parts and re.fullmatch(r"\d{4}", parts[-1]):      # HHMM
            parts.pop()
        if parts and re.fullmatch(r"\d{8}", parts[-1]):      # YYYYMMDD
            parts.pop()
        tail_code = parts.pop() if parts and parts[-1].isdigit() else None
        # odreži naziv poslovnice od "spar"/"interspar" nadalje
        cut = next((i for i, p in enumerate(parts) if p.lower() in {"spar", "interspar"}), len(parts))
        # šifra poslovnice je broj neposredno ispred "spar"/"interspar" (npr. 8701);
        # zadnji broj u imenu datoteke nije jedinstven po poslovnici
        if 0 < cut < len(parts) and parts[cut - 1].isdigit():
            code = parts[cut - 1]
        else:
            code = tail_code or stem
        kind, body = (parts[0], parts[1:cut]) if parts else (None, [])
        # interni broj (4+ znamenke) ispred naziva nije dio adrese
        while body and re.fullmatch(r"\d{4,}", body[-1]):
            body.pop()
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
        yield StorePrices(chain=self.chain_for(raw.name), store=self.store_from_filename(raw.name),
                          rows=rows, source_file=raw.name, encoding=enc, warnings=warnings)
