"""Preuzmi stvarne dnevne cjenike i spremi po 2 datoteke po trgovcu kao
test fixture (tests/fixtures/<trgovac>/real/). Pokreni na mreži koja ima
pristup stranicama trgovaca:

    cd backend && python ../scripts/fetch_samples.py [--date 2026-11-19] [--chains konzum,lidl]

Nakon toga `pytest tests/test_real_samples.py` provjerava da ih svi
adapteri parsiraju bez greške.
"""
import argparse
import io
import sys
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.adapters import default_adapters, get_adapter  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--date", type=date.fromisoformat, default=date.today())
ap.add_argument("--chains")
ap.add_argument("--per-chain", type=int, default=2)
args = ap.parse_args()

out_root = Path(__file__).resolve().parents[1] / "backend/tests/fixtures"
for code in (args.chains.split(",") if args.chains else default_adapters()):
    adapter = get_adapter(code)()
    try:
        urls = adapter.discover(args.date)
        print(f"{code}: {len(urls)} URL-ova")
        files = adapter.download(urls[0]) if urls else []
        if len(urls) > 1 and len(files) < args.per_chain:
            files += adapter.download(urls[1])
        out = out_root / code / "real"
        out.mkdir(parents=True, exist_ok=True)
        for f in files[: args.per_chain]:
            # skrati na prvih 200 redaka da fixture ostane malen
            lines = f.data.splitlines(keepends=True)[:200]
            (out / Path(f.name).name).write_bytes(b"".join(lines))
            print(f"  spremljeno {f.name}")
    except Exception as e:  # noqa: BLE001
        print(f"{code}: GREŠKA {type(e).__name__}: {e}")
