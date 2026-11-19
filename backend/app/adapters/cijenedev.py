"""cijene.dev dnevne arhive – brzi početak bez preuzimanja od svakog trgovca.

Arhiva je ZIP s jednim direktorijem po trgovcu:

    konzum/stores.csv    store_id,type,address,city,zipcode
    konzum/products.csv  product_id,barcode,name,brand,category,unit,quantity
    konzum/prices.csv    store_id,product_id,price,unit_price,best_price_30,
                         anchor_price,special_price

Popis arhiva: GET {base}/v0/list -> {"archives": [{"date": "...", "url": "..."}]}

Nije uključen u dnevni posao po defaultu (enabled_by_default=False);
pokreće se ručno: `python -m app.worker once --chains cijenedev`.
Kodovi trgovaca iz arhive (konzum, lidl, plodine, spar, ...) koriste se
izravno, tako da se podaci miješaju s podacima izravnih adaptera.
"""
from __future__ import annotations

import csv
import io
import os
from collections import defaultdict
from datetime import date

from .base import Adapter, FormatChanged, PriceRow, StoreInfo, StorePrices
from .csvutil import decode_bytes, parse_decimal


class CijeneDevAdapter(Adapter):
    code = "cijenedev"
    name = "cijene.dev (arhiva)"
    enabled_by_default = False
    unpack_zip = False
    base_url = os.environ.get("CIJENEDEV_BASE_URL", "https://api.cijene.dev")

    def discover(self, day: date) -> list[str]:
        r = self.get(f"{self.base_url}/v0/list")
        for a in r.json().get("archives", []):
            if a.get("date") == day.isoformat():
                return [a["url"]]
        return []

    def parse(self, raw: RawFile):
        import zipfile

        with zipfile.ZipFile(io.BytesIO(raw.data)) as zf:
            by_chain: dict[str, dict[str, str]] = defaultdict(dict)
            for n in zf.namelist():
                parts = n.strip("/").split("/")
                if len(parts) >= 2 and parts[-1] in {"stores.csv", "products.csv", "prices.csv"}:
                    by_chain[parts[-2]][parts[-1]] = n
            if not by_chain:
                raise FormatChanged("cijene.dev arhiva nema očekivanu strukturu <chain>/*.csv")
            for chain, files in sorted(by_chain.items()):
                if len(files) < 3:
                    raise FormatChanged(f"cijene.dev/{chain}: nedostaju datoteke, ima {sorted(files)}")
                yield from self._parse_chain(chain, {k: zf.read(v) for k, v in files.items()})

    @staticmethod
    def _rows(data: bytes) -> list[dict[str, str]]:
        text, _ = decode_bytes(data)
        return list(csv.DictReader(io.StringIO(text)))

    def _parse_chain(self, chain: str, files: dict[str, bytes]):
        stores = {s["store_id"]: s for s in self._rows(files["stores.csv"])}
        products = {p["product_id"]: p for p in self._rows(files["products.csv"])}
        per_store: dict[str, list[PriceRow]] = defaultdict(list)
        for p in self._rows(files["prices.csv"]):
            prod = products.get(p.get("product_id", ""))
            if not prod:
                continue
            regular = parse_decimal(p.get("price"))
            special = parse_decimal(p.get("special_price"))
            if regular is None and special is None:
                continue
            per_store[p["store_id"]].append(PriceRow(
                code=prod["product_id"],
                name=prod.get("name") or "",
                brand=prod.get("brand") or None,
                quantity=prod.get("quantity") or None,
                unit=prod.get("unit") or None,
                barcode=prod.get("barcode") or None,
                category=prod.get("category") or None,
                regular_price=regular if regular is not None else special,
                unit_price=parse_decimal(p.get("unit_price")),
                special_price=special if special != regular else None,
                lowest_30d=parse_decimal(p.get("best_price_30")),
                anchor_price=parse_decimal(p.get("anchor_price")),
            ))
        for store_id, rows in per_store.items():
            s = stores.get(store_id, {})
            yield StorePrices(
                chain=chain,
                store=StoreInfo(code=store_id, name=s.get("type") or None,
                                address=s.get("address") or None,
                                postcode=s.get("zipcode") or None, city=s.get("city") or None),
                rows=rows,
                source_file=f"cijene.dev/{chain}",
            )
