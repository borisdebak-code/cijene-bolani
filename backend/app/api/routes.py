from __future__ import annotations

import re
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_session
from ..normalize import normalize_ean, search_text

router = APIRouter(prefix="/api")

EFFECTIVE = "LEAST(p.special_price, p.regular_price)"
# cijena je "trenutna" ako je viđena u zadnjem cjeniku trgovca (uz 2 dana tolerancije
# za poslovnice čiji cjenik kasni)
FRESH = "p.is_current AND p.last_seen >= c.last_day - 2"


def _store_filter(city: str | None, store_id: int | None) -> tuple[str, dict]:
    sql, params = "", {}
    if city:
        sql += " AND s.city ILIKE :city"
        params["city"] = city
    if store_id:
        sql += " AND s.id = :store_id"
        params["store_id"] = store_id
    return sql, params


@router.get("/search")
def search(
    q: str = Query(..., min_length=2, max_length=100),
    city: str | None = None,
    limit: int = Query(30, le=100),
    db: Session = Depends(get_session),
):
    """Pretraga po nazivu (svaka riječ mora biti prisutna) ili barkodu."""
    sfilter, params = _store_filter(city, None)
    digits = re.sub(r"\D", "", q)
    if len(digits) >= 8 and len(digits) >= len(q.strip()) - 1:
        ean = normalize_ean(digits) or digits
        hits = """SELECT DISTINCT pr.id, 1.0 AS sim FROM product pr
                  LEFT JOIN chain_product cp ON cp.product_id = pr.id
                  WHERE pr.ean = :ean OR cp.ean = :ean OR cp.barcode_raw LIKE :ean_like"""
        params |= {"ean": ean, "ean_like": f"%{digits}%"}
    else:
        tokens = search_text(q).split()
        if not tokens:
            return []
        hits = """SELECT pr.id, similarity(pr.search_text, :q) AS sim FROM product pr
                  WHERE pr.search_text LIKE ALL(:pats)
                  ORDER BY sim DESC, length(pr.search_text) LIMIT 300"""
        params |= {"q": " ".join(tokens), "pats": [f"%{t}%" for t in tokens]}

    rows = db.execute(text(
        f"""
        WITH hits AS ({hits})
        SELECT pr.id, pr.name, pr.brand, pr.quantity, pr.unit, pr.ean, pr.category,
               agg.min_price, agg.max_price, agg.chains, agg.on_sale
        FROM hits h JOIN product pr ON pr.id = h.id
        LEFT JOIN LATERAL (
            SELECT min({EFFECTIVE}) AS min_price, max({EFFECTIVE}) AS max_price,
                   count(DISTINCT cp.chain_id) AS chains,
                   bool_or(p.special_price IS NOT NULL) AS on_sale
            FROM chain_product cp
            JOIN chain c ON c.id = cp.chain_id
            JOIN price p ON p.chain_product_id = cp.id
            JOIN store s ON s.id = p.store_id
            WHERE cp.product_id = pr.id AND {FRESH} {sfilter}
        ) agg ON true
        ORDER BY (agg.chains > 0) DESC NULLS LAST,
                 h.sim + LEAST(COALESCE(agg.chains, 0), 5) * 0.05 DESC, pr.name
        LIMIT :limit
        """), {**params, "limit": limit}).mappings().all()
    return [dict(r) for r in rows]


@router.get("/products/{product_id}")
def product(product_id: int, db: Session = Depends(get_session)):
    pr = db.execute(text("SELECT id, name, brand, quantity, unit, ean, category FROM product "
                         "WHERE id = :id"), {"id": product_id}).mappings().first()
    if not pr:
        raise HTTPException(404, "Proizvod ne postoji")
    variants = db.execute(text(
        """SELECT c.code AS chain, c.name AS chain_name, cp.code, cp.name, cp.brand,
                  cp.quantity_raw, cp.unit_raw, cp.barcode_raw, cp.match_method
           FROM chain_product cp JOIN chain c ON c.id = cp.chain_id
           WHERE cp.product_id = :id ORDER BY c.name"""), {"id": product_id}).mappings().all()
    return {**pr, "variants": [dict(v) for v in variants]}


@router.get("/products/{product_id}/prices")
def product_prices(
    product_id: int,
    city: str | None = None,
    store_id: int | None = None,
    db: Session = Depends(get_session),
):
    """Trenutne cijene po poslovnicama, od najjeftinije. `chains` = najbolja
    cijena po trgovcu (za kratku usporedbu)."""
    sfilter, params = _store_filter(city, store_id)
    rows = db.execute(text(
        f"""
        SELECT c.code AS chain, c.name AS chain_name, s.id AS store_id, s.name AS store_name,
               s.address, s.city, p.regular_price, p.special_price, p.unit_price,
               p.lowest_30d, p.anchor_price, {EFFECTIVE} AS price,
               p.valid_from, p.last_seen, cp.unit_raw
        FROM chain_product cp
        JOIN chain c ON c.id = cp.chain_id
        JOIN price p ON p.chain_product_id = cp.id
        JOIN store s ON s.id = p.store_id
        WHERE cp.product_id = :pid AND {FRESH} {sfilter}
        ORDER BY price, c.name, s.city, s.address
        """), {"pid": product_id, **params}).mappings().all()

    stores = []
    chains: dict[str, dict] = {}
    for r in rows:
        r = dict(r)
        r["is_deal"] = bool(
            r["special_price"] is not None
            or (r["lowest_30d"] is not None and r["price"] < r["lowest_30d"])
        )
        stores.append(r)
        ch = chains.get(r["chain"])
        if ch is None:
            chains[r["chain"]] = {
                "chain": r["chain"], "chain_name": r["chain_name"], "min_price": r["price"],
                "max_price": r["price"], "stores": 1, "best_store": r, "is_deal": r["is_deal"],
            }
        else:
            ch["max_price"] = max(ch["max_price"], r["price"])
            ch["stores"] += 1
            ch["is_deal"] = ch["is_deal"] or r["is_deal"]
    return {"chains": sorted(chains.values(), key=lambda c: c["min_price"]), "stores": stores}


@router.get("/products/{product_id}/history")
def product_history(
    product_id: int,
    days: int = Query(90, ge=7, le=730),
    city: str | None = None,
    store_id: int | None = None,
    db: Session = Depends(get_session),
):
    """Najniža (i prosječna) cijena po trgovcu po danu."""
    sfilter, params = _store_filter(city, store_id)
    end = date.today()
    start = end - timedelta(days=days)
    rows = db.execute(text(
        f"""
        SELECT g.d::date AS day, c.code AS chain, c.name AS chain_name,
               min({EFFECTIVE}) AS min_price, round(avg({EFFECTIVE}), 2) AS avg_price,
               min(p.regular_price) AS min_regular
        FROM generate_series(CAST(:start AS date), CAST(:end AS date), interval '1 day') g(d)
        JOIN price p ON p.valid_from <= g.d AND p.last_seen >= g.d
        JOIN chain_product cp ON cp.id = p.chain_product_id
        JOIN chain c ON c.id = cp.chain_id
        JOIN store s ON s.id = p.store_id
        WHERE cp.product_id = :pid {sfilter}
        GROUP BY 1, 2, 3 ORDER BY 2, 1
        """), {"pid": product_id, "start": start, "end": end, **params}).mappings().all()
    series: dict[str, dict] = {}
    for r in rows:
        s = series.setdefault(r["chain"], {"chain": r["chain"], "chain_name": r["chain_name"],
                                           "points": []})
        s["points"].append({"day": r["day"], "min": r["min_price"], "avg": r["avg_price"],
                            "regular": r["min_regular"]})
    return list(series.values())


@router.get("/cities")
def cities(db: Session = Depends(get_session)):
    return [dict(r) for r in db.execute(text(
        "SELECT city, count(*) AS stores FROM store WHERE city IS NOT NULL "
        "GROUP BY city ORDER BY city")).mappings()]


@router.get("/stores")
def stores(city: str | None = None, chain: str | None = None, db: Session = Depends(get_session)):
    sql = ("SELECT s.id, c.code AS chain, c.name AS chain_name, s.code, s.name, s.address, "
           "s.postcode, s.city FROM store s JOIN chain c ON c.id = s.chain_id WHERE true")
    params = {}
    if city:
        sql += " AND s.city ILIKE :city"
        params["city"] = city
    if chain:
        sql += " AND c.code = :chain"
        params["chain"] = chain
    return [dict(r) for r in db.execute(text(sql + " ORDER BY c.name, s.city, s.address"), params).mappings()]


@router.get("/chains")
def chains(db: Session = Depends(get_session)):
    return [dict(r) for r in db.execute(text(
        "SELECT c.code, c.name, c.last_day, (SELECT count(*) FROM store s WHERE s.chain_id = c.id) "
        "AS stores FROM chain c ORDER BY c.name")).mappings()]


@router.get("/status")
def status(db: Session = Depends(get_session)):
    """Stanje zadnjeg preuzimanja po adapteru – ovdje se vidi koji je pukao."""
    from ..adapters import all_adapters

    last = {r["chain_code"]: dict(r) for r in db.execute(text(
        """SELECT DISTINCT ON (chain_code) id, chain_code, day, status, started_at,
                  finished_at, files_total, files_failed, rows_total, rows_skipped, stores, error
           FROM ingest_run ORDER BY chain_code, started_at DESC""")).mappings()}
    errors = db.execute(text(
        """SELECT r.chain_code, e.kind, e.file, left(e.message, 500) AS message, e.created_at
           FROM ingest_error e JOIN ingest_run r ON r.id = e.run_id
           WHERE e.kind <> 'row' ORDER BY e.created_at DESC LIMIT 50""")).mappings().all()
    out = []
    for code, cls in all_adapters().items():
        out.append({"adapter": code, "name": cls.name, "scheduled": cls.enabled_by_default,
                    "last_run": last.get(code)})
    return {"adapters": out, "recent_errors": [dict(e) for e in errors]}
