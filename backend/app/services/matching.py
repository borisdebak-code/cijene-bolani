"""Spajanje proizvoda između trgovaca.

1. EAN (globalni, provjeren checksum) -> isti proizvod.
2. Bez EAN-a (ili EAN koji još ne postoji u bazi): fuzzy po nazivu, uz
   uvjet da se *količina i jedinica* točno poklapaju i da marka nije u
   sukobu. Kandidate pronalazi pg_trgm, konačnu ocjenu daje rapidfuzz.
3. Inače novi kanonski proizvod.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from rapidfuzz import fuzz
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..normalize import display_name, search_text

FUZZY_THRESHOLD = 88


@dataclass
class Unlinked:
    cp_id: int
    name: str
    brand: str | None
    quantity: Decimal | None
    unit: str | None
    ean: str | None
    category: str | None
    match_name: str


def brands_compatible(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return True
    a, b = search_text(a), search_text(b)
    return a == b or a in b or b in a


def score(a: str, b: str) -> float:
    return max(fuzz.token_sort_ratio(a, b), fuzz.token_set_ratio(a, b) - 4)


def find_fuzzy(session: Session, item: Unlinked, chain_id: int, *, only_without_ean: bool) -> int | None:
    if item.quantity is None or item.unit is None or len(item.match_name) < 3:
        return None
    rows = session.execute(
        text(
            f"""
            SELECT p.id, p.match_name, p.brand
            FROM product p
            WHERE p.unit = :unit AND p.quantity = :qty
              AND p.match_name % :m
              {"AND p.ean IS NULL" if only_without_ean else ""}
              AND NOT EXISTS (SELECT 1 FROM chain_product x
                              WHERE x.product_id = p.id AND x.chain_id = :chain)
            ORDER BY similarity(p.match_name, :m) DESC
            LIMIT 8
            """
        ),
        {"unit": item.unit, "qty": item.quantity, "m": item.match_name, "chain": chain_id},
    ).all()
    best, best_score = None, 0.0
    for pid, mname, pbrand in rows:
        if not brands_compatible(item.brand, pbrand):
            continue
        s = score(item.match_name, mname)
        if s > best_score:
            best, best_score = pid, s
    return best if best_score >= FUZZY_THRESHOLD else None


def create_product(session: Session, item: Unlinked) -> int:
    return session.execute(
        text(
            """
            INSERT INTO product (ean, name, brand, quantity, unit, category, search_text, match_name)
            VALUES (:ean, :name, :brand, :qty, :unit, :cat, :st, :mn)
            ON CONFLICT (ean) DO UPDATE SET ean = EXCLUDED.ean
            RETURNING id
            """
        ),
        {
            "ean": item.ean, "name": display_name(item.name), "brand": item.brand,
            "qty": item.quantity, "unit": item.unit, "cat": item.category,
            "st": search_text(item.name, item.brand, item.ean),
            "mn": item.match_name,
        },
    ).scalar_one()


def link_unlinked(session: Session, chain_id: int, items: list[Unlinked]) -> dict[str, int]:
    stats: dict = {"fuzzy": 0, "new": 0, "linked": []}
    for item in items:
        method = "new"
        pid = find_fuzzy(session, item, chain_id, only_without_ean=item.ean is not None)
        if pid is not None:
            method = "fuzzy"
            if item.ean:
                # proizvod do sada nije imao EAN – sad ga dobiva
                session.execute(
                    text("UPDATE product SET ean = :e, search_text = search_text || ' ' || :e "
                         "WHERE id = :id AND ean IS NULL"),
                    {"e": item.ean, "id": pid},
                )
        else:
            pid = create_product(session, item)
        session.execute(
            text("UPDATE chain_product SET product_id = :p, match_method = :m WHERE id = :id"),
            {"p": pid, "m": method, "id": item.cp_id},
        )
        stats[method] += 1
        if method == "fuzzy":
            stats["linked"].append((item, pid))
    return stats
