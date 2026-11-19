"""Napuni bazu demo podacima iz test fixturea kroz zadnjih N dana, s malim
promjenama cijena i povremenim akcijama – da se aplikacija može isprobati
bez pristupa stranicama trgovaca.

    docker compose exec api python /srv/scripts/seed_demo.py --days 60
    # ili lokalno: cd backend && python ../scripts/seed_demo.py
"""
import argparse
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.adapters import RawFile, get_adapter  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.services.ingest import run_adapter  # noqa: E402

FIX = ROOT / "backend/tests/fixtures"
ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=60)
args = ap.parse_args()
init_db()
rnd = random.Random(42)
price_re = re.compile(rb"(?<![\d.,])(\d{1,2})([.,])(\d{2})(?![\d.,])")


def wiggle(data: bytes, day_idx: int, enc: str, chain: str) -> bytes:
    # svakih ~10 dana cijene se malo pomaknu; deterministički po danu
    period = day_idx // 10
    r = random.Random(f"{chain}-{period}")

    def sub(m):
        v = int(m.group(1)) * 100 + int(m.group(3))
        v = max(9, round(v * (1 + r.uniform(-0.08, 0.08))))
        return f"{v // 100}{m.group(2).decode()}{v % 100:02d}".encode()
    text = data.decode(enc)
    head, _, body = text.partition("\n")
    return (head + "\n").encode(enc) + price_re.sub(sub, body.encode(enc))


for chain in ("konzum", "lidl", "plodine", "kaufland", "spar"):
    adapter = get_adapter(chain)()
    base = [f for p in sorted((FIX / chain).glob("*.*")) for f in adapter.prepare(p.name, p.read_bytes())]
    for i in range(args.days, -1, -1):
        day = date.today() - timedelta(days=i)
        enc = "cp1250" if chain in ("lidl", "spar") else "utf-8"
        files = [RawFile(f.name, wiggle(f.data, i, enc, chain)) for f in base]
        r = run_adapter(SessionLocal, chain, day, files=files, adapter=adapter)
    print(chain, r["status"], r["rows_total"])
