"""Dnevno osvježavanje cijena za statičnu (objavljenu) verziju aplikacije.

    python scripts/daily_refresh.py [--out site] [--skip-export]

1. cijene.dev: najnovija dnevna arhiva (Konzum, Lidl, Kaufland, Spar/Interspar)
2. Plodine: najnoviji cjenik s plodine.hr (zadnja 4 dana)
3. unos samo ako je cjenik noviji od onoga što baza već ima
4. statični izvoz u OUT (index.html + data/), popis datoteka u OUT/files.json

Na kraju ispiše sažetak (JSON) i izađe s kodom 0 ako je stranica spremna za objavu.
Na serveru (docker compose) ovo nije potrebno – worker to radi sam u 08:30.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.adapters.base import unpack  # noqa: E402
from app.adapters.cijenedev import CijeneDevAdapter  # noqa: E402
from app.adapters.plodine import PlodineAdapter  # noqa: E402
from app.db import SessionLocal, engine, init_db  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.services.ingest import run_adapter  # noqa: E402

CDEV_CHAINS = ["konzum", "lidl", "kaufland", "spar"]
UA = {"User-Agent": "cijene-hr/0.1 (+price comparison; official price lists)"}


def last_days() -> dict[str, date | None]:
    with engine.connect() as c:
        return dict(c.execute(text("SELECT code, last_day FROM chain")).all())


def refresh_cijenedev(summary: dict) -> None:
    have = last_days()
    have_min = min((have.get(c) for c in CDEV_CHAINS + ["interspar"] if have.get(c)), default=None)
    r = httpx.get(f"{CijeneDevAdapter.base_url}/v0/list", headers=UA, timeout=60)
    r.raise_for_status()
    archives = sorted(r.json()["archives"], key=lambda a: a["date"], reverse=True)
    latest = archives[0]
    day = date.fromisoformat(latest["date"])
    summary["cijenedev_latest"] = latest["date"]
    if have_min and day <= have_min:
        summary["cijenedev"] = f"bez promjene (baza već ima {have_min})"
        return
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "a.zip"
        with httpx.stream("GET", latest["url"], headers=UA, timeout=900, follow_redirects=True) as resp:
            resp.raise_for_status()
            with open(zpath, "wb") as f:
                for chunk in resp.iter_bytes(1 << 20):
                    f.write(chunk)
        adapter = CijeneDevAdapter(chains=CDEV_CHAINS)
        files = adapter.prepare(zpath.name, zpath.read_bytes())
        res = run_adapter(SessionLocal, "cijenedev", day, files=files, adapter=adapter)
    summary["cijenedev"] = {k: res[k] for k in ("status", "stores", "rows_total", "error")} | {"day": str(day)}


def refresh_plodine(summary: dict) -> None:
    have = last_days().get("plodine")
    adapter = PlodineAdapter()
    for back in range(0, 4):
        day = date.today() - timedelta(days=back)
        if have and day <= have:
            summary["plodine"] = f"bez promjene (baza već ima {have})"
            return
        urls = adapter.discover(day)
        if urls:
            files = [f for u in urls for f in adapter.download(u)]
            res = run_adapter(SessionLocal, "plodine", day, files=files, adapter=adapter)
            summary["plodine"] = {k: res[k] for k in ("status", "stores", "rows_total", "error")} | {"day": str(day)}
            return
    summary["plodine"] = "nema novog cjenika u zadnja 4 dana"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "site")
    ap.add_argument("--skip-export", action="store_true")
    args = ap.parse_args()
    setup_logging()
    init_db()
    summary: dict = {}
    for name, fn in (("cijenedev", refresh_cijenedev), ("plodine", refresh_plodine)):
        try:
            fn(summary)
        except Exception as e:  # noqa: BLE001 – jedan izvor ne smije srušiti drugi
            summary[name] = f"GREŠKA: {type(e).__name__}: {e}"
    summary["last_day"] = {k: str(v) for k, v in sorted(last_days().items())}
    ok = True
    if not args.skip_export:
        r = subprocess.run([sys.executable, str(ROOT / "scripts/export_static.py"), str(args.out), "--artifact"],
                           capture_output=True, text=True)
        summary["export"] = (r.stdout.strip() or r.stderr.strip()[-500:])
        ok = r.returncode == 0
        if ok:
            files = sorted(str(p.relative_to(args.out)) for p in (args.out / "data").rglob("*.json"))
            (args.out / "files.json").write_text(json.dumps([{"path": f} for f in files]))
            summary["files"] = len(files)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
