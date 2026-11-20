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


def _download(url: str, dest: Path) -> None:
    with httpx.stream("GET", url, headers=UA, timeout=900, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(1 << 20):
                f.write(chunk)


def refresh_cijenedev(summary: dict) -> None:
    """Za svakog trgovca najnovija arhiva u kojoj on stvarno postoji.

    Dnevna arhiva se puni tijekom dana – ujutro u današnjoj često još nema
    nekih trgovaca (npr. Kauflanda i Lidla), pa ih uzimamo iz jučerašnje."""
    import zipfile

    have = last_days()
    r = httpx.get(f"{CijeneDevAdapter.base_url}/v0/list", headers=UA, timeout=60)
    r.raise_for_status()
    archives = sorted(r.json()["archives"], key=lambda a: a["date"], reverse=True)[:4]
    summary["cijenedev_latest"] = archives[0]["date"]
    done: dict[str, str] = {}
    pending = set(CDEV_CHAINS)
    for a in archives:
        day = date.fromisoformat(a["date"])

        def newer(c: str) -> bool:
            # spar puni i interspar – dovoljno je da je jedan od njih stariji
            targets = [c, "interspar"] if c == "spar" else [c]
            return any(have.get(t) is None or have[t] < day for t in targets)

        want = sorted(c for c in pending if newer(c))
        for c in sorted(pending - set(want)):              # već imamo ovaj ili noviji dan
            done[c] = f"bez promjene (baza već ima {have.get(c)})"
        pending -= {c for c in pending if c not in want}
        if not want:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            zpath = Path(tmp) / "a.zip"
            _download(a["url"], zpath)
            with zipfile.ZipFile(zpath) as zf:
                present = {n.split("/")[0] for n in zf.namelist() if n.endswith("/prices.csv")}
            chains = [c for c in want if c in present]
            if not chains:
                continue
            adapter = CijeneDevAdapter(chains=chains)
            res = run_adapter(SessionLocal, "cijenedev", day, files=adapter.prepare(zpath.name, zpath.read_bytes()),
                              adapter=adapter)
        for c in chains:
            done[c] = f"{day} ({res['status']})"
        pending -= set(chains)
        if not pending:
            break
    for c in pending:
        done.setdefault(c, "bez promjene ili nije u zadnje 4 arhive")
    summary["cijenedev"] = done


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
    ap.add_argument("--standalone", action="store_true",
                    help="izvoz za GitHub Pages / statični hosting (instalabilna aplikacija), ne claude.ai")
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
        cmd = [sys.executable, str(ROOT / "scripts/export_static.py"), str(args.out)]
        if not args.standalone:
            cmd.append("--artifact")
        r = subprocess.run(cmd, capture_output=True, text=True)
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
