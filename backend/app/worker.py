"""Worker za preuzimanje cjenika.

    python -m app.worker schedule                 # dnevno u 08:30 (Europe/Zagreb)
    python -m app.worker once                     # odmah, svi zakazani trgovci, danas
    python -m app.worker once --chains lidl,konzum --date 2026-11-18
    python -m app.worker load konzum datoteka.csv [--date ...]   # lokalne datoteke / ZIP
    python -m app.worker list                     # popis adaptera
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from .adapters import all_adapters, default_adapters, get_adapter
from .db import SessionLocal, init_db
from .logging_setup import setup_logging
from .services.ingest import run_adapter

log = logging.getLogger("cijene.worker")
TZ = ZoneInfo(os.environ.get("TZ_NAME", "Europe/Zagreb"))


def today() -> date:
    from datetime import datetime

    return datetime.now(TZ).date()


def configured_chains() -> list[str]:
    """INGEST_CHAINS=cijenedev,plodine – koji adapteri idu u dnevni posao."""
    env = [c.strip() for c in os.environ.get("INGEST_CHAINS", "").split(",") if c.strip()]
    return env or default_adapters()


def ok_today(day: date) -> set[str]:
    from sqlalchemy import text

    with SessionLocal() as s:
        return {r[0] for r in s.execute(text(
            "SELECT DISTINCT chain_code FROM ingest_run WHERE day = :d AND status = 'ok'"), {"d": day})}


def run_all(chains: list[str] | None = None, day: date | None = None,
            only_missing: bool = False) -> list[dict]:
    day = day or today()
    chains = chains or configured_chains()
    if only_missing:
        chains = [c for c in chains if c not in ok_today(day)]
        if not chains:
            return []
        log.info("Ponovni pokušaj za: %s", ", ".join(chains))
    results = []
    for code in chains:
        # svaki trgovac zasebno – greška jednoga ne zaustavlja ostale
        results.append({"chain": code, **run_adapter(SessionLocal, code, day)})
    failed = [r["chain"] for r in results if r["status"] != "ok"]
    if failed:
        log.error("Problemi s adapterima: %s", ", ".join(failed))
    else:
        log.info("Svi adapteri OK (%d)", len(results))
    return results


def schedule() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    hour, minute = os.environ.get("INGEST_AT", "08:30").split(":")
    sched = BlockingScheduler(timezone=TZ)
    sched.add_job(run_all, CronTrigger(hour=int(hour), minute=int(minute), timezone=TZ),
                  id="daily_ingest", max_instances=1, coalesce=True, misfire_grace_time=3600)
    # neki trgovci (i cijene.dev arhiva) objave cjenik kasnije – ponavljaj svaki sat
    # do RETRY_UNTIL samo za one koji danas još nisu uspjeli
    until = int(os.environ.get("RETRY_UNTIL_HOUR", "14"))
    if until > int(hour):
        sched.add_job(run_all, CronTrigger(hour=f"{int(hour) + 1}-{until}", minute=int(minute),
                                           timezone=TZ),
                      kwargs={"only_missing": True}, id="retry_ingest", max_instances=1,
                      coalesce=True, misfire_grace_time=1800)
    log.info("Zakazano dnevno preuzimanje u %s:%s (%s)", hour, minute, TZ)
    if os.environ.get("INGEST_ON_START", "").lower() in {"1", "true", "yes"}:
        run_all()
    sched.start()


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    ap = argparse.ArgumentParser(prog="app.worker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("schedule")
    sub.add_parser("list")
    sub.add_parser("categorize", help="ponovno razvrstaj sve proizvode u kategorije")
    o = sub.add_parser("once")
    o.add_argument("--chains", help="zarezom odvojeni kodovi (default: svi zakazani)")
    o.add_argument("--date", type=date.fromisoformat)
    ld = sub.add_parser("load")
    ld.add_argument("chain")
    ld.add_argument("paths", nargs="+", type=Path)
    ld.add_argument("--date", type=date.fromisoformat)
    args = ap.parse_args(argv)

    if args.cmd == "list":
        for code, cls in all_adapters().items():
            print(f"{code:12} {cls.name:25} {'dnevno' if cls.enabled_by_default else 'ručno'}")
        return 0

    init_db()
    if args.cmd == "categorize":
        from .services.ingest import assign_categories

        with SessionLocal() as s:
            n = assign_categories(s, all_products=True)
            s.commit()
        print(f"razvrstano {n} proizvoda")
        return 0
    if args.cmd == "schedule":
        schedule()
        return 0
    if args.cmd == "once":
        res = run_all(args.chains.split(",") if args.chains else None, args.date)
        return 0 if all(r["status"] == "ok" for r in res) else 1
    if args.cmd == "load":
        adapter = get_adapter(args.chain)()
        files = [f for p in args.paths for f in adapter.prepare(p.name, p.read_bytes())]
        r = run_adapter(SessionLocal, args.chain, args.date or today(), files=files, adapter=adapter)
        print(r)
        return 0 if r["status"] == "ok" else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
