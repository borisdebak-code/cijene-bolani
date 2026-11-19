"""Unos cjenika u bazu.

Za svaku poslovnicu (StorePrices):
  1. COPY svih redova u privremenu `stg` tablicu (brzo i za 20k+ redova)
  2. upsert `chain_product` (šifra artikla kod trgovca)
  3. povezivanje s kanonskim `product` (EAN u SQL-u, ostalo fuzzy)
  4. ažuriranje intervala cijena (`price`) – stari zapisi se ne prepisuju

Svaki trgovac ima svoj `ingest_run`, a svaka datoteka koja pukne svoj
`ingest_error`, tako da se odmah vidi koji je adapter pukao i zašto.
"""
from __future__ import annotations

import logging
import os
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..adapters import Adapter, AdapterError, FormatChanged, RawFile, StorePrices, get_adapter
from ..geo import normalize_city
from ..normalize import match_name, normalize_ean, parse_quantity, search_text
from . import matching

log = logging.getLogger("cijene.ingest")

CHAIN_NAMES = {
    "konzum": "Konzum", "lidl": "Lidl", "kaufland": "Kaufland", "spar": "Spar",
    "interspar": "Interspar", "plodine": "Plodine", "tommy": "Tommy", "studenac": "Studenac",
    "eurospin": "Eurospin", "ktc": "KTC", "ntl": "NTL", "ribola": "Ribola", "metro": "Metro",
    "dm": "dm", "boso": "Boso", "trgovina_krk": "Trgovina Krk", "trgovina-krk": "Trgovina Krk",
    "zabac": "Žabac", "vrutak": "Vrutak", "lorenco": "Lorenco",
}

RAW_ARCHIVE_DIR = os.environ.get("RAW_ARCHIVE_DIR")
ROW_DROP_ALERT = 0.5   # upozori ako je redova < 50% prošlog uspješnog runa


class ChainLog(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"[{self.extra['chain']}] {msg}", kwargs


# --------------------------------------------------------------------------- #

STG_DDL = """
CREATE TEMP TABLE IF NOT EXISTS stg (
    code text, name text, brand text, quantity_raw text, unit_raw text,
    quantity numeric, unit text, ean text, barcode_raw text, category text,
    match_name text,
    regular_price numeric, unit_price numeric, special_price numeric,
    lowest_30d numeric, anchor_price numeric,
    cp_id int
)
"""

STG_COLS = ("code", "name", "brand", "quantity_raw", "unit_raw", "quantity", "unit", "ean",
            "barcode_raw", "category", "match_name", "regular_price", "unit_price",
            "special_price", "lowest_30d", "anchor_price")


def ensure_chain(session: Session, code: str) -> int:
    return session.execute(
        text("INSERT INTO chain (code, name) VALUES (:c, :n) "
             "ON CONFLICT (code) DO UPDATE SET code = EXCLUDED.code RETURNING id"),
        {"c": code, "n": CHAIN_NAMES.get(code, code.replace("_", " ").title())},
    ).scalar_one()


def ensure_store(session: Session, chain_id: int, sp: StorePrices) -> int:
    s = sp.store
    return session.execute(
        text(
            """
            INSERT INTO store (chain_id, code, name, address, postcode, city)
            VALUES (:chain, :code, :name, :address, :postcode, :city)
            ON CONFLICT (chain_id, code) DO UPDATE SET
              name = COALESCE(EXCLUDED.name, store.name),
              address = COALESCE(EXCLUDED.address, store.address),
              postcode = COALESCE(EXCLUDED.postcode, store.postcode),
              city = COALESCE(EXCLUDED.city, store.city)
            RETURNING id
            """
        ),
        {"chain": chain_id, "code": s.code, "name": s.name, "address": s.address,
         "postcode": s.postcode, "city": normalize_city(s.city, s.postcode)},
    ).scalar_one()


def _extend_search_text(session: Session, linked: list[tuple]) -> None:
    """Kad se artikl drugog trgovca spoji na postojeći proizvod, dodaj njegov
    naziv u tekst za pretragu (npr. 'Nutella' i 'NUTELLA KREMA' nalaze isto)."""
    if not linked:
        return
    ids, extras = [], []
    for pid, name, brand in linked:
        ids.append(pid)
        extras.append(search_text(name, brand))
    session.execute(text(
        """
        UPDATE product pr SET search_text = pr.search_text || ' ' || v.extra
        FROM unnest(CAST(:ids AS int[]), CAST(:extras AS text[])) AS v(id, extra)
        WHERE pr.id = v.id AND position(v.extra IN pr.search_text) = 0
        """), {"ids": ids, "extras": extras})


def _stg_rows(sp: StorePrices) -> list[tuple]:
    out: dict[str, tuple] = {}
    for r in sp.rows:
        ean = normalize_ean(r.barcode)
        qty, unit = parse_quantity(r.quantity, r.unit, r.name)
        code = r.code or ean or ("n:" + search_text(r.name, r.quantity, r.unit))[:64]
        out[code] = (
            code, r.name[:300], (r.brand or None) and r.brand[:150], (r.quantity or "")[:50] or None,
            (r.unit or "")[:20] or None, qty, unit, ean, (r.barcode or "")[:200] or None,
            (r.category or "")[:200] or None, match_name(r.name, r.brand),
            r.regular_price, r.unit_price, r.special_price, r.lowest_30d, r.anchor_price,
        )
    return list(out.values())


def ingest_store_prices(session: Session, sp: StorePrices, day: date) -> dict:
    chain_id = ensure_chain(session, sp.chain)
    store_id = ensure_store(session, chain_id, sp)
    rows = _stg_rows(sp)

    session.execute(text(STG_DDL))
    session.execute(text("TRUNCATE stg"))
    raw = session.connection().connection.driver_connection
    with raw.cursor() as cur:
        with cur.copy(f"COPY stg ({', '.join(STG_COLS)}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)

    p = {"chain": chain_id, "store": store_id, "d": day}

    # 2. chain_product
    session.execute(text(
        """
        INSERT INTO chain_product (chain_id, code, name, brand, quantity_raw, unit_raw,
                                   quantity, unit, ean, barcode_raw, category)
        SELECT :chain, code, name, brand, quantity_raw, unit_raw, quantity, unit, ean,
               barcode_raw, category FROM stg
        ON CONFLICT (chain_id, code) DO UPDATE SET
            name = EXCLUDED.name, brand = EXCLUDED.brand,
            quantity_raw = EXCLUDED.quantity_raw, unit_raw = EXCLUDED.unit_raw,
            quantity = EXCLUDED.quantity, unit = EXCLUDED.unit,
            ean = COALESCE(EXCLUDED.ean, chain_product.ean),
            barcode_raw = EXCLUDED.barcode_raw, category = EXCLUDED.category
        WHERE (chain_product.name, chain_product.brand, chain_product.quantity_raw,
               chain_product.unit_raw, chain_product.ean, chain_product.category)
              IS DISTINCT FROM
              (EXCLUDED.name, EXCLUDED.brand, EXCLUDED.quantity_raw, EXCLUDED.unit_raw,
               COALESCE(EXCLUDED.ean, chain_product.ean), EXCLUDED.category)
        """), p)
    session.execute(text(
        "UPDATE stg SET cp_id = cp.id FROM chain_product cp "
        "WHERE cp.chain_id = :chain AND cp.code = stg.code"), p)

    # 3a. povezivanje po EAN-u (bulk)
    newly_linked = session.execute(text(
        """
        UPDATE chain_product cp SET product_id = pr.id, match_method = 'ean'
        FROM stg, product pr
        WHERE cp.id = stg.cp_id AND cp.product_id IS NULL
          AND cp.ean IS NOT NULL AND pr.ean = cp.ean
        RETURNING cp.product_id, cp.name, cp.brand
        """), p).all()
    linked_ean = len(newly_linked)

    # 3b. ostalo: fuzzy ili novi proizvod
    unlinked = [
        matching.Unlinked(cp_id=r.cp_id, name=r.name, brand=r.brand, quantity=r.quantity,
                          unit=r.unit, ean=r.ean, category=r.category, match_name=r.match_name)
        for r in session.execute(text(
            "SELECT stg.cp_id, cp.name, cp.brand, cp.quantity, cp.unit, cp.ean, cp.category, "
            "stg.match_name FROM stg JOIN chain_product cp ON cp.id = stg.cp_id "
            "WHERE cp.product_id IS NULL")).all()
    ]
    mstats = matching.link_unlinked(session, chain_id, unlinked)
    _extend_search_text(session, newly_linked + [
        (pid, u.name, u.brand) for u, pid in mstats.pop("linked") if pid is not None])

    # 4. intervali cijena
    same = """(p.regular_price, p.special_price, p.unit_price, p.lowest_30d, p.anchor_price)
              IS NOT DISTINCT FROM
              (s.regular_price, s.special_price, s.unit_price, s.lowest_30d, s.anchor_price)"""
    extended = session.execute(text(
        f"""
        UPDATE price p SET last_seen = :d FROM stg s
        WHERE p.is_current AND p.store_id = :store AND p.chain_product_id = s.cp_id
          AND p.last_seen < :d AND p.last_seen >= CAST(:d AS date) - 3 AND {same}
        """), p).rowcount
    session.execute(text(
        """
        UPDATE price p SET is_current = false FROM stg s
        WHERE p.is_current AND p.store_id = :store AND p.chain_product_id = s.cp_id
          AND p.last_seen < :d
        """), p)
    inserted = session.execute(text(
        """
        INSERT INTO price (store_id, chain_product_id, valid_from, last_seen, is_current,
                           regular_price, unit_price, special_price, lowest_30d, anchor_price)
        SELECT :store, s.cp_id, :d, :d, true, s.regular_price, s.unit_price,
               s.special_price, s.lowest_30d, s.anchor_price
        FROM stg s
        WHERE NOT EXISTS (SELECT 1 FROM price p WHERE p.is_current
                          AND p.store_id = :store AND p.chain_product_id = s.cp_id)
        """), p).rowcount

    session.execute(text(
        "UPDATE chain SET last_day = :d WHERE id = :chain AND (last_day IS NULL OR last_day < :d)"), p)

    return {"rows": len(rows), "linked_ean": linked_ean, **mstats,
            "price_extended": extended, "price_new": inserted}


# --------------------------------------------------------------------------- #

def _archive(adapter_code: str, day: date, raw: RawFile) -> None:
    if not RAW_ARCHIVE_DIR:
        return
    d = Path(RAW_ARCHIVE_DIR) / adapter_code / day.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    (d / Path(raw.name).name).write_bytes(raw.data)


def _record_error(session: Session, run_id: int, kind: str, message: str, file: str | None = None):
    session.execute(text(
        "INSERT INTO ingest_error (run_id, file, kind, message) VALUES (:r, :f, :k, :m)"),
        {"r": run_id, "f": (file or "")[:300] or None, "k": kind, "m": message[:5000]})


def run_adapter(session_factory, adapter_code: str, day: date,
                files: Iterable[RawFile] | None = None, adapter: Adapter | None = None) -> dict:
    """Preuzmi i unesi cjenike jednog trgovca za jedan dan.

    `files` – ako je zadano, ne preuzima se ništa (lokalne datoteke, testovi).
    Greške se nikad ne propagiraju van: zapisuju se u ingest_run/ingest_error.
    """
    clog = ChainLog(log, {"chain": adapter_code})
    adapter = adapter or get_adapter(adapter_code)()

    with session_factory() as s:
        run_id = s.execute(text(
            "INSERT INTO ingest_run (chain_code, adapter, day, status) "
            "VALUES (:c, :a, :d, 'running') RETURNING id"),
            {"c": adapter_code, "a": type(adapter).__name__, "d": day}).scalar_one()
        s.commit()

    stats = {"files_total": 0, "files_failed": 0, "rows_total": 0, "rows_skipped": 0, "stores": 0}
    fatal: str | None = None
    clog.info("start, dan %s", day)

    try:
        source = files if files is not None else adapter.fetch(day)
        for raw in source:
            stats["files_total"] += 1
            _archive(adapter_code, day, raw)
            try:
                parsed = list(adapter.parse(raw))
            except Exception as e:  # noqa: BLE001
                stats["files_failed"] += 1
                kind = "format_changed" if isinstance(e, FormatChanged) else "parse"
                clog.error("%s: %s – %s", kind, raw.name, e)
                with session_factory() as s:
                    _record_error(s, run_id, kind, f"{e}\n{traceback.format_exc(limit=3)}", raw.name)
                    s.commit()
                continue
            for sp in parsed:
                try:
                    with session_factory() as s:
                        r = ingest_store_prices(s, sp, day)
                        for w in sp.warnings[:20]:
                            _record_error(s, run_id, "row", w, sp.source_file)
                        s.commit()
                    stats["rows_total"] += r["rows"]
                    stats["rows_skipped"] += len(sp.warnings)
                    stats["stores"] += 1
                    clog.debug("%s: %s", sp.source_file, r)
                except Exception as e:  # noqa: BLE001
                    stats["files_failed"] += 1
                    clog.exception("unos nije uspio: %s", sp.source_file)
                    with session_factory() as s:
                        _record_error(s, run_id, "db", f"{e}", sp.source_file)
                        s.commit()
    except AdapterError as e:
        fatal = str(e)
        clog.error("adapter: %s", e)
    except Exception as e:  # noqa: BLE001 – npr. HTTP greška kod discover
        fatal = f"{type(e).__name__}: {e}"
        clog.exception("preuzimanje nije uspjelo")

    # status + detekcija promjene formata po padu broja redaka
    with session_factory() as s:
        prev = s.execute(text(
            "SELECT rows_total FROM ingest_run WHERE chain_code = :c AND status = 'ok' "
            "AND id <> :id ORDER BY day DESC, id DESC LIMIT 1"),
            {"c": adapter_code, "id": run_id}).scalar()
        if fatal:
            _record_error(s, run_id, "download", fatal)
        if fatal or stats["stores"] == 0:
            status = "failed"
            fatal = fatal or "nijedna poslovnica nije uspješno učitana"
        elif stats["files_failed"]:
            status = "partial"
            fatal = f"{stats['files_failed']}/{stats['files_total']} datoteka nije uspjelo"
        else:
            status = "ok"
        if status != "failed" and prev and stats["rows_total"] < prev * ROW_DROP_ALERT:
            status = "partial"
            fatal = (f"Broj redaka pao s {prev} na {stats['rows_total']} – "
                     "moguća promjena formata ili nepotpun cjenik")
            _record_error(s, run_id, "row_drop", fatal)
        s.execute(text(
            """UPDATE ingest_run SET status = :st, finished_at = :now, error = :err,
               files_total = :files_total, files_failed = :files_failed,
               rows_total = :rows_total, rows_skipped = :rows_skipped, stores = :stores
               WHERE id = :id"""),
            {"st": status, "now": datetime.now(timezone.utc), "err": fatal, "id": run_id, **stats})
        s.commit()

    level = logging.INFO if status == "ok" else logging.ERROR
    clog.log(level, "kraj: %s %s", status, stats)
    return {"run_id": run_id, "status": status, "error": fatal, **stats}
