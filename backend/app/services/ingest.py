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
from ..normalize import clean_brand, display_name, match_name, normalize_ean, parse_quantity, search_text
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

# Privremene tablice za jedan paket (jedan trgovac, jedna ili više poslovnica).
# stgp = jedan red po artiklu trgovca, stg = jedan red po (poslovnica, artikl).
STGP_COLS = ("code", "name", "brand", "quantity_raw", "unit_raw", "quantity", "unit", "ean",
             "barcode_raw", "category", "match_name", "name_display", "search_text")
STG_COLS = ("store_id", "code", "regular_price", "unit_price", "special_price",
            "lowest_30d", "anchor_price")
BATCH_ROWS = int(os.environ.get("INGEST_BATCH_ROWS", "400000"))


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
        {"chain": chain_id, "code": s.code[:64], "name": (s.name or "")[:100] or None,
         "address": (s.address or "")[:200] or None,
         "postcode": (s.postcode or "")[:10] or None,
         "city": (normalize_city(s.city, s.postcode) or "")[:100] or None},
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


MAX_PRICE = 100_000      # Numeric(10,2); veće je sigurno greška u datoteci (npr. barkod u stupcu cijene)
MAX_QTY = 10 ** 8


def _money(v):
    return v if v is not None and 0 <= v < MAX_PRICE else None


def ingest_store_prices(session: Session, sp: StorePrices, day: date) -> dict:
    return ingest_batch(session, [sp], day)


def ingest_batch(session: Session, sps: list[StorePrices], day: date) -> dict:
    """Unesi cjenike više poslovnica *istog* trgovca jednim prolazom."""
    chain = sps[0].chain
    assert all(sp.chain == chain for sp in sps), "paket mora biti jedan trgovac"
    chain_id = ensure_chain(session, chain)

    products: dict[str, tuple] = {}
    prices: dict[tuple, tuple] = {}
    for sp in sps:
        store_id = ensure_store(session, chain_id, sp)
        for r in sp.rows:
            regular = _money(r.regular_price)
            if regular is None:
                sp.warnings.append(f"neispravna cijena {r.regular_price} ({r.name[:60]})")
                continue
            ean = normalize_ean(r.barcode)
            code = (r.code or ean or "n:" + search_text(r.name, r.quantity, r.unit))[:64]
            if code not in products:
                qty, unit = parse_quantity(r.quantity, r.unit, r.name)
                if qty is not None and qty >= MAX_QTY:
                    qty, unit = None, None
                r.brand = clean_brand(r.brand)
                products[code] = (
                    code, r.name[:300], (r.brand or None) and r.brand[:150],
                    (r.quantity or "")[:50] or None, (r.unit or "")[:20] or None, qty, unit, ean,
                    (r.barcode or "")[:200] or None, (r.category or "")[:200] or None,
                    match_name(r.name, r.brand), display_name(r.name)[:300],
                    search_text(r.name, r.brand, ean),
                )
            prices[(store_id, code)] = (store_id, code, regular, _money(r.unit_price),
                                        _money(r.special_price), _money(r.lowest_30d),
                                        _money(r.anchor_price))

    session.execute(text(
        """CREATE TEMP TABLE stgp (code text, name text, brand text, quantity_raw text,
             unit_raw text, quantity numeric, unit text, ean text, barcode_raw text,
             category text, match_name text, name_display text, search_text text,
             cp_id int) ON COMMIT DROP"""))
    session.execute(text(
        """CREATE TEMP TABLE stg (store_id int, code text, regular_price numeric,
             unit_price numeric, special_price numeric, lowest_30d numeric,
             anchor_price numeric) ON COMMIT DROP"""))
    raw = session.connection().connection.driver_connection
    with raw.cursor() as cur:
        with cur.copy(f"COPY stgp ({', '.join(STGP_COLS)}) FROM STDIN") as copy:
            for row in products.values():
                copy.write_row(row)
        with cur.copy(f"COPY stg ({', '.join(STG_COLS)}) FROM STDIN") as copy:
            for row in prices.values():
                copy.write_row(row)
    session.execute(text("ANALYZE stgp"))
    session.execute(text("ANALYZE stg"))

    p = {"chain": chain_id, "d": day}

    # 2. chain_product (artikl trgovca, njegova šifra)
    session.execute(text(
        """
        INSERT INTO chain_product (chain_id, code, name, brand, quantity_raw, unit_raw,
                                   quantity, unit, ean, barcode_raw, category)
        SELECT :chain, code, name, brand, quantity_raw, unit_raw, quantity, unit, ean,
               barcode_raw, category FROM stgp
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
        "UPDATE stgp SET cp_id = cp.id FROM chain_product cp "
        "WHERE cp.chain_id = :chain AND cp.code = stgp.code"), p)

    # 3a. povezivanje po EAN-u s postojećim proizvodom
    newly_linked = session.execute(text(
        """
        UPDATE chain_product cp SET product_id = pr.id, match_method = 'ean'
        FROM stgp s, product pr
        WHERE cp.id = s.cp_id AND cp.product_id IS NULL
          AND cp.ean IS NOT NULL AND pr.ean = cp.ean
        RETURNING cp.product_id, cp.name, cp.brand
        """)).all()

    # 3b. novi EAN-ovi: kanonski proizvod jednim upitom
    created = session.execute(text(
        """
        INSERT INTO product (ean, name, brand, quantity, unit, category, search_text, match_name)
        SELECT DISTINCT ON (cp.ean) cp.ean, s.name_display, cp.brand, cp.quantity, cp.unit,
               cp.category, s.search_text, s.match_name
        FROM stgp s JOIN chain_product cp ON cp.id = s.cp_id
        WHERE cp.product_id IS NULL AND cp.ean IS NOT NULL
        ORDER BY cp.ean
        ON CONFLICT (ean) DO NOTHING
        """)).rowcount
    session.execute(text(
        """
        UPDATE chain_product cp SET product_id = pr.id, match_method = 'new'
        FROM stgp s, product pr
        WHERE cp.id = s.cp_id AND cp.product_id IS NULL
          AND cp.ean IS NOT NULL AND pr.ean = cp.ean
        """))

    # 3c. bez EAN-a: fuzzy ili novi proizvod
    unlinked = [
        matching.Unlinked(cp_id=r.cp_id, name=r.name, brand=r.brand, quantity=r.quantity,
                          unit=r.unit, ean=r.ean, category=r.category, match_name=r.match_name)
        for r in session.execute(text(
            "SELECT s.cp_id, cp.name, cp.brand, cp.quantity, cp.unit, cp.ean, cp.category, "
            "s.match_name FROM stgp s JOIN chain_product cp ON cp.id = s.cp_id "
            "WHERE cp.product_id IS NULL")).all()
    ]
    mstats = matching.link_unlinked(session, chain_id, unlinked)
    mstats["new"] += created
    _extend_search_text(session, newly_linked + [
        (pid, u.name, u.brand) for u, pid in mstats.pop("linked") if pid is not None])

    # 4. intervali cijena
    session.execute(text(
        """CREATE TEMP TABLE stp ON COMMIT DROP AS
           SELECT s.store_id, g.cp_id, s.regular_price, s.unit_price, s.special_price,
                  s.lowest_30d, s.anchor_price
           FROM stg s JOIN stgp g USING (code)"""))
    session.execute(text("ANALYZE stp"))
    same = """(p.regular_price, p.special_price, p.unit_price, p.lowest_30d, p.anchor_price)
              IS NOT DISTINCT FROM
              (s.regular_price, s.special_price, s.unit_price, s.lowest_30d, s.anchor_price)"""
    extended = session.execute(text(
        f"""
        UPDATE price p SET last_seen = :d FROM stp s
        WHERE p.is_current AND p.store_id = s.store_id AND p.chain_product_id = s.cp_id
          AND p.last_seen < :d AND p.last_seen >= CAST(:d AS date) - 3 AND {same}
        """), p).rowcount
    session.execute(text(
        """
        UPDATE price p SET is_current = false FROM stp s
        WHERE p.is_current AND p.store_id = s.store_id AND p.chain_product_id = s.cp_id
          AND p.last_seen < :d
        """), p)
    inserted = session.execute(text(
        """
        INSERT INTO price (store_id, chain_product_id, valid_from, last_seen, is_current,
                           regular_price, unit_price, special_price, lowest_30d, anchor_price)
        SELECT s.store_id, s.cp_id, :d, :d, true, s.regular_price, s.unit_price,
               s.special_price, s.lowest_30d, s.anchor_price
        FROM stp s
        WHERE NOT EXISTS (SELECT 1 FROM price p WHERE p.is_current
                          AND p.store_id = s.store_id AND p.chain_product_id = s.cp_id)
        """), p).rowcount

    session.execute(text(
        "UPDATE chain SET last_day = :d WHERE id = :chain AND (last_day IS NULL OR last_day < :d)"), p)

    return {"rows": len(prices), "products": len(products), "linked_ean": len(newly_linked),
            **mstats, "price_extended": extended, "price_new": inserted}


# --------------------------------------------------------------------------- #

def refresh_daily(session: Session, chain_code: str, day: date) -> int:
    """Dnevni sažetak po proizvodu i trgovcu (medijan, min, max, broj
    poslovnica, akcije) – za graf povijesti i usporedbu bez skeniranja
    svih cijena po poslovnicama."""
    p = {"c": chain_code, "d": day}
    session.execute(text(
        "DELETE FROM chain_daily WHERE day = :d AND chain_id = (SELECT id FROM chain WHERE code = :c)"), p)
    return session.execute(text(
        """
        INSERT INTO chain_daily (product_id, chain_id, day, price_median, price_min, price_max,
                                 regular_median, stores, deal_stores)
        SELECT cp.product_id, cp.chain_id, CAST(:d AS date),
               percentile_disc(0.5) WITHIN GROUP (ORDER BY LEAST(p.special_price, p.regular_price)),
               min(LEAST(p.special_price, p.regular_price)),
               max(LEAST(p.special_price, p.regular_price)),
               percentile_disc(0.5) WITHIN GROUP (ORDER BY p.regular_price),
               count(*),
               count(*) FILTER (WHERE p.special_price IS NOT NULL
                                OR LEAST(p.special_price, p.regular_price) < p.lowest_30d)
        FROM price p
        JOIN chain_product cp ON cp.id = p.chain_product_id
        JOIN chain c ON c.id = cp.chain_id
        WHERE c.code = :c AND p.valid_from <= :d AND p.last_seen >= :d
          AND cp.product_id IS NOT NULL
        GROUP BY cp.product_id, cp.chain_id
        """), p).rowcount


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
    chains_touched: set[str] = set()
    batch: list[StorePrices] = []
    batch_rows = 0
    clog.info("start, dan %s", day)

    def flush() -> None:
        nonlocal batch, batch_rows
        if not batch:
            return
        try:
            with session_factory() as s:
                r = ingest_batch(s, batch, day)
                for sp in batch:
                    for w in sp.warnings[:20]:
                        _record_error(s, run_id, "row", w, sp.source_file)
                s.commit()
            stats["rows_total"] += r["rows"]
            stats["rows_skipped"] += sum(len(sp.warnings) for sp in batch)
            stats["stores"] += len(batch)
            chains_touched.add(batch[0].chain)
            clog.info("%s: %d poslovnica, %d cijena (%s novih proizvoda) – ukupno %d poslovnica",
                      batch[0].chain, len(batch), r["rows"], r["new"], stats["stores"])
        except Exception as e:  # noqa: BLE001
            stats["files_failed"] += len(batch)
            clog.exception("unos nije uspio: %s (+%d)", batch[0].source_file, len(batch) - 1)
            with session_factory() as s:
                for sp in batch:
                    _record_error(s, run_id, "db", f"{e}", sp.source_file)
                s.commit()
        batch, batch_rows = [], 0

    try:
        source = files if files is not None else adapter.fetch(day)
        for raw in source:
            stats["files_total"] += 1
            _archive(adapter_code, day, raw)
            # parse() je generator – skupljamo poslovnice istog trgovca u pakete
            it = iter(adapter.parse(raw))
            while True:
                try:
                    sp = next(it)
                except StopIteration:
                    break
                except Exception as e:  # noqa: BLE001
                    stats["files_failed"] += 1
                    kind = "format_changed" if isinstance(e, FormatChanged) else "parse"
                    clog.error("%s: %s – %s", kind, raw.name, e)
                    with session_factory() as s:
                        _record_error(s, run_id, kind, f"{e}\n{traceback.format_exc(limit=3)}", raw.name)
                        s.commit()
                    break
                if batch and (sp.chain != batch[0].chain or batch_rows >= BATCH_ROWS):
                    flush()
                batch.append(sp)
                batch_rows += len(sp.rows)
        flush()
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

    for chain in sorted(chains_touched):
        with session_factory() as s:
            refresh_daily(s, chain, day)
            s.commit()

    level = logging.INFO if status == "ok" else logging.ERROR
    clog.log(level, "kraj: %s %s", status, stats)
    return {"run_id": run_id, "status": status, "error": fatal, **stats}
