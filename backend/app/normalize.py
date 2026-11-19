"""Normalizacija proizvoda: EAN, količina/jedinica mjere, tekst za pretragu."""
from __future__ import annotations

import re
from decimal import Decimal

from .adapters.csvutil import parse_decimal, strip_accents

# --------------------------------------------------------------------------- #
# EAN / GTIN
# --------------------------------------------------------------------------- #

def _gtin_check_ok(code: str) -> bool:
    digits = [int(c) for c in code]
    body, check = digits[:-1], digits[-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def normalize_ean(raw: str | None) -> str | None:
    """Vrati globalno jedinstveni EAN (8 ili 13 znamenki) ili None.

    - više barkodova u jednom polju ('3850…;3850…') -> uzimamo prvi ispravan
    - UPC-A (12) i GTIN-14 svodimo na 13 znamenki
    - kodovi za interno vaganje/trgovinu (prefiks 2, '02', '04') NISU globalni,
      pa ih ne koristimo za spajanje između trgovaca
    """
    if not raw:
        return None
    for cand in re.split(r"[;,|/\s]+", str(raw)):
        c = re.sub(r"\D", "", cand)
        if c.endswith(".0"):
            c = c[:-2]
        if len(c) == 12:
            c = "0" + c
        elif len(c) == 14 and c.startswith("0"):
            c = c[1:]
        if len(c) not in (8, 13) or not _gtin_check_ok(c) or set(c) == {"0"}:
            continue
        if (len(c) == 13 and (c[0] == "2" or c[:2] in {"02", "04"})) or \
                (len(c) == 8 and c[0] in "02"):
            continue
        return c
    return None


# --------------------------------------------------------------------------- #
# Količina i jedinica mjere
# --------------------------------------------------------------------------- #

_UNIT_ALIASES = {
    "g": ("g", 1), "gr": ("g", 1), "gram": ("g", 1), "grama": ("g", 1),
    "dag": ("g", 10), "dkg": ("g", 10),
    "kg": ("g", 1000), "kilogram": ("g", 1000),
    "ml": ("ml", 1), "cl": ("ml", 10), "dl": ("ml", 100),
    "l": ("ml", 1000), "lit": ("ml", 1000), "litra": ("ml", 1000), "lt": ("ml", 1000),
    "kom": ("kom", 1), "komad": ("kom", 1), "kos": ("kom", 1), "pak": ("kom", 1),
    "par": ("kom", 1), "kut": ("kom", 1), "m": ("m", 1), "m2": ("m2", 1),
}

_QTY = re.compile(
    r"(?:(?P<mult>\d+)\s*[x×*]\s*)?(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>[a-zA-Z]{1,8}\d?)\b"
)


def parse_quantity(quantity: str | None, unit: str | None = None,
                   name: str | None = None) -> tuple[Decimal | None, str | None]:
    """('0,5', 'l') -> (500, 'ml'); ('500 g', None) -> (500, 'g');
    ('6x0,33 l') -> (1980, 'ml'). Ako polja nisu dovoljna, pokušaj iz naziva."""
    q = (quantity or "").strip().lower()
    u = strip_accents((unit or "").strip().lower()).rstrip(".")

    # 1) količina i jedinica u odvojenim stupcima
    num = parse_decimal(q) if re.fullmatch(r"[\d.,\s]+", q or "x") else None
    if num is not None and u in _UNIT_ALIASES:
        base, factor = _UNIT_ALIASES[u]
        return (num * factor).normalize(), base

    # 2) "500 g", "6x0,33l" u stupcu količine, pa u nazivu
    for text in (q, strip_accents((name or "").lower())):
        for m in _QTY.finditer(text):
            uu = m["unit"].lower()
            if uu in _UNIT_ALIASES:
                base, factor = _UNIT_ALIASES[uu]
                n = parse_decimal(m["num"])
                if n is None:
                    continue
                mult = int(m["mult"]) if m["mult"] else 1
                return (n * factor * mult).normalize(), base

    if num is not None and not u:
        return num.normalize(), None
    if u in _UNIT_ALIASES:
        return None, _UNIT_ALIASES[u][0]
    return None, None


# --------------------------------------------------------------------------- #
# Tekst
# --------------------------------------------------------------------------- #

def search_text(*parts: str | None) -> str:
    s = " ".join(p for p in parts if p)
    s = strip_accents(s).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_NOISE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:x\s*\d+(?:[.,]\d+)?)?\s*(?:g|gr|kg|dag|ml|cl|dl|l|kom)\b")


def match_name(name: str, brand: str | None) -> str:
    """Naziv za fuzzy usporedbu: bez količina, marke i interpunkcije."""
    s = search_text(name)
    s = _NOISE.sub(" ", s)
    if brand:
        for tok in search_text(brand).split():
            s = re.sub(rf"\b{re.escape(tok)}\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def display_name(s: str) -> str:
    """KONZUM CSV-ovi su često u verzalu – za prikaz malo uljepšamo."""
    if s.isupper():
        return s[:1] + s[1:].lower()
    return s
