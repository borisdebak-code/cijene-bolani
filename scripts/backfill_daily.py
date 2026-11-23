"""Popuni povijest (tablica chain_daily) iz starijih cijene.dev arhiva.

    cd backend && python ../scripts/backfill_daily.py --days 30 [--chains konzum,lidl,kaufland,spar]

Ne dira tablicu `price` (intervali se pune samo kronološki, dnevnim poslom);
računa samo dnevni sažetak po proizvodu i trgovcu, za graf povijesti.
Postojeći dani se ne prepisuju. Proizvodi se povezuju preko šifre artikla
trgovca (chain_product), pa najprije treba unijeti barem jedan dan normalno.
"""
from __future__ import annotations

import argparse
import csv
import io
import statistics
import sys
import tempfile
import zipfile
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.adapters.cijenedev import CijeneDevAdapter  # noqa: E402
from app.adapters.csvutil import parse_decimal  # noqa: E402
from app.db import engine  # noqa: E402

UA = {"User-Agent": "cijene-hr/0.1 (+price comparison; official price lists)"}


def archives() -> dict[str, str]:
    r = httpx.get(f"{CijeneDevAdapter.base_url}/v0/list", headers=UA, timeout=60)
    r.raise_for_status()
    return {a["date"]: a["url"] for a in r.json()["archives"]}


def process(zpath: Path, day: date, chains: list[str]) -> int:
    with engine.connect() as c:
        chain_ids = {r.code: r.id for r in c.execute(text("SELECT id, code FROM chain"))}
    rows_out = []
    with zipfile.ZipFile(zpath) as zf:
        if chains == ["all"]:
            chains = sorted({n.split("/")[0] for n in zf.namelist() if n.endswith("/prices.csv")} - {"plodine"})
        for chain in chains:
            try:
                stores = {s["store_id"]: s for s in csv.DictReader(io.StringIO(
                    zf.read(f"{chain}/stores.csv").decode("utf-8-sig")))}
            except KeyError:
                print(f"  {chain}: nema u arhivi")
                continue
            # šifra artikla -> naš product_id, po (pod)trgovcu (spar/interspar)
            targets = {CijeneDevAdapter.chain_for_store(chain, s.get("type")) for s in stores.values()}
            code_map: dict[str, dict[str, int]] = {}
            with engine.connect() as c:
                for t in targets:
                    if t in chain_ids:
                        code_map[t] = dict(c.execute(text(
                            "SELECT code, product_id FROM chain_product WHERE chain_id = :c "
                            "AND product_id IS NOT NULL"), {"c": chain_ids[t]}).all())
            eff: dict[tuple, list] = defaultdict(list)
            reg: dict[tuple, list] = defaultdict(list)
            deals: dict[tuple, int] = defaultdict(int)
            with zf.open(f"{chain}/prices.csv") as fh:
                for p in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")):
                    st = stores.get(p["store_id"])
                    t = CijeneDevAdapter.chain_for_store(chain, st.get("type") if st else None)
                    pid = code_map.get(t, {}).get(p["product_id"])
                    if pid is None:
                        continue
                    regular = parse_decimal(p.get("price"))
                    special = parse_decimal(p.get("special_price"))
                    if regular is None and special is None:
                        continue
                    e = min(x for x in (regular, special) if x is not None)
                    k = (pid, t)
                    eff[k].append(e)
                    reg[k].append(regular if regular is not None else e)
                    low = parse_decimal(p.get("best_price_30"))
                    if special is not None or (low is not None and e < low):
                        deals[k] += 1
            for (pid, t), vals in eff.items():
                vals.sort()
                rows_out.append({
                    "p": pid, "c": chain_ids[t], "d": day,
                    "med": statistics.median_low(vals), "mn": vals[0], "mx": vals[-1],
                    "reg": statistics.median_low(sorted(reg[(pid, t)])),
                    "st": len(vals), "dl": deals[(pid, t)],
                })
            print(f"  {chain}: {len(eff)} proizvoda")
    with engine.begin() as c:
        for i in range(0, len(rows_out), 5000):
            c.execute(text(
                """INSERT INTO chain_daily (product_id, chain_id, day, price_median, price_min,
                       price_max, regular_median, stores, deal_stores)
                   VALUES (:p, :c, :d, :med, :mn, :mx, :reg, :st, :dl)
                   ON CONFLICT DO NOTHING"""), rows_out[i:i + 5000])
    return len(rows_out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--until", type=date.fromisoformat, default=date.today() - timedelta(days=1))
    ap.add_argument("--chains", default="all",
                    help='zarezom odvojeni trgovci ili "all" (svi iz arhive osim Plodina)')
    args = ap.parse_args()
    chains = args.chains.split(",")
    avail = archives()
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(args.days):
            day = args.until - timedelta(days=i)
            url = avail.get(day.isoformat())
            if not url:
                print(f"{day}: nema arhive")
                continue
            zpath = Path(tmp) / "a.zip"
            with httpx.stream("GET", url, headers=UA, timeout=600, follow_redirects=True) as r:
                r.raise_for_status()
                with open(zpath, "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
            print(f"{day}: preuzeto, obrađujem")
            n = process(zpath, day, chains)
            print(f"{day}: {n} dnevnih zapisa", flush=True)
            zpath.unlink()


if __name__ == "__main__":
    main()
