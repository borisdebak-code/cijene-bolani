from __future__ import annotations

import re
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_session
from ..normalize import normalize_ean, product_attrs, search_text

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
    import os

    if os.environ.get("PUBLIC_MODE", "").lower() in {"1", "true", "yes"}:
        raise HTTPException(404, "Nije dostupno")
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


# --------------------------------------------------------------------------- #
# Javno sučelje: prijedlozi za pretragu i sažetak proizvoda po trgovcu.
# "Uobičajena cijena" = medijan cijene (s akcijom) u poslovnicama trgovca,
# iz tablice chain_daily – brzo i bez skeniranja svih cijena.
# --------------------------------------------------------------------------- #

def _qty_label(q, unit) -> str | None:
    if q is None:
        return None
    q = float(q)
    if unit == "g" and q >= 1000:
        return f"{q / 1000:g} kg"
    if unit == "ml" and q >= 1000:
        return f"{q / 1000:g} l"
    return f"{q:g} {unit or ''}".strip()


@router.get("/suggest")
def suggest(q: str = Query(..., min_length=2, max_length=100),
            limit: int = Query(8, le=500), db: Session = Depends(get_session)):
    """Prijedlozi za pretragu, rangirani. S većim `limit` (npr. 400) sučelje iz
    atributa (pct, feat) i marke/količine samo slaže pitanja za sužavanje izbora."""
    digits = re.sub(r"\D", "", q)
    if len(digits) >= 8 and len(digits) >= len(q.strip()) - 1:
        hits = """SELECT DISTINCT pr.id, 1.0 AS sim FROM product pr
                  LEFT JOIN chain_product cp ON cp.product_id = pr.id
                  WHERE pr.ean = :ean OR cp.ean = :ean"""
        params = {"ean": normalize_ean(digits) or digits, "words": [r"\m"], "first": ""}
    else:
        tokens = search_text(q).split()
        if not tokens:
            return []
        # LIKE koristi trigram indeks, regex (\m = početak riječi) izbacuje pogotke
        # usred riječi ili barkoda ("400" ne smije pogoditi 4008400401829)
        hits = """SELECT pr.id, similarity(pr.search_text, :q) AS sim FROM product pr
                  WHERE pr.search_text LIKE ALL(:pats) AND pr.search_text ~ ALL(:words)
                  ORDER BY sim DESC LIMIT 400"""
        params = {"q": " ".join(tokens), "pats": [f"%{t}%" for t in tokens],
                  "words": [rf"\m{re.escape(t)}" for t in tokens], "first": re.escape(tokens[0])}
    rows = db.execute(text(
        f"""
        WITH hits AS ({hits})
        SELECT pr.id, pr.name, pr.brand, pr.quantity, pr.unit, pr.ean,
               agg.min_price, agg.chains
        FROM hits h JOIN product pr ON pr.id = h.id
        JOIN LATERAL (
            SELECT min(d.price_median) AS min_price, count(*) AS chains
            FROM chain_daily d JOIN chain c ON c.id = d.chain_id AND d.day = c.last_day
            WHERE d.product_id = pr.id
        ) agg ON agg.chains > 0
        ORDER BY h.sim + LEAST(agg.chains, 6) * 0.12
                 -- riječi u samom nazivu (ne samo u nazivima drugih trgovaca)
                 + CASE WHEN pr.match_name ~ ALL(:words) THEN 0.4 ELSE 0 END
                 + CASE WHEN pr.match_name ~ ('^' || :first) THEN 0.3 ELSE 0 END DESC,
                 pr.name
        LIMIT :limit
        """), {**params, "limit": limit}).mappings().all()
    out = []
    for r in rows:
        pct, feat = product_attrs(r["name"], r["brand"])
        out.append({**dict(r), "qty": _qty_label(r["quantity"], r["unit"]), "pct": pct, "feat": feat})
    return out


@router.get("/products/{product_id}/summary")
def product_summary(product_id: int, days: int = Query(90, ge=1, le=730),
                    db: Session = Depends(get_session)):
    pr = db.execute(text("SELECT id, name, brand, quantity, unit, ean, category FROM product "
                         "WHERE id = :id"), {"id": product_id}).mappings().first()
    if not pr:
        raise HTTPException(404, "Proizvod ne postoji")
    chains_ = db.execute(text(
        """SELECT c.code AS chain, c.name AS chain_name, d.price_median AS price,
                  d.price_min AS min, d.price_max AS max, d.regular_median AS regular,
                  d.stores, d.deal_stores, d.day, d.deal_label, d.valid_until,
                  d.card_price, d.card_name
           FROM chain_daily d JOIN chain c ON c.id = d.chain_id AND d.day = c.last_day
           WHERE d.product_id = :id ORDER BY d.price_median, d.price_min, c.name"""),
        {"id": product_id}).mappings().all()
    hist = db.execute(text(
        """SELECT c.code AS chain, c.name AS chain_name, d.day, d.price_median AS price
           FROM chain_daily d JOIN chain c ON c.id = d.chain_id
           WHERE d.product_id = :id AND d.day >= CAST(:since AS date)
           ORDER BY c.name, d.day"""),
        {"id": product_id, "since": date.today() - timedelta(days=days)}).mappings().all()
    history: dict[str, dict] = {}
    for h in hist:
        history.setdefault(h["chain"], {"chain": h["chain"], "chain_name": h["chain_name"],
                                        "points": []})["points"].append(
            {"day": h["day"], "price": h["price"]})
    return {**pr, "qty": _qty_label(pr["quantity"], pr["unit"]),
            "chains": [dict(c) for c in chains_], "history": list(history.values())}


@router.get("/config")
def config(db: Session = Depends(get_session)):
    import os

    from ..normalize import FEATURES

    return {"public": os.environ.get("PUBLIC_MODE", "").lower() in {"1", "true", "yes"},
            "updated": db.execute(text("SELECT max(last_day) FROM chain")).scalar(),
            "features": {k: v[0] for k, v in FEATURES.items()}}
