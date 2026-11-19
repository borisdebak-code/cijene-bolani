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


def _from_text(text: str) -> tuple[Decimal, str] | None:
    for m in _QTY.finditer(text):
        uu = m["unit"].lower()
        if uu in _UNIT_ALIASES:
            base, factor = _UNIT_ALIASES[uu]
            n = parse_decimal(m["num"])
            if n is None or n == 0:
                continue
            mult = int(m["mult"]) if m["mult"] else 1
            return (n * factor * mult).normalize(), base
    return None


def parse_quantity(quantity: str | None, unit: str | None = None,
                   name: str | None = None) -> tuple[Decimal | None, str | None]:
    """('0,5', 'l') -> (500, 'ml'); ('500 g', None) -> (500, 'g');
    ('6x0,33 l') -> (1980, 'ml'). Ako polja nisu dovoljna, pokušaj iz naziva."""
    q = (quantity or "").strip().lower()
    u = strip_accents((unit or "").strip().lower()).rstrip(".")
    # cijene.dev: "g (1kg)", "ml (1 L)" – količina je izražena u jedinici iz zagrade
    m = re.match(r"^[a-z]+\s*\(\s*\d*\s*([a-z]+)\s*\)$", u)
    if m:
        u = m.group(1)
    # Kaufland i sl.: jedinica "KOM", a količina u kg – naziv ("460g") je pouzdaniji
    if u in {"kom", "ko", "kos", ""}:
        from_name = _from_text(strip_accents((name or "").lower()))
        if from_name and from_name[1] in {"g", "ml"}:
            return from_name
    if u == "ko":
        u = "kom"

    # 1) količina i jedinica u odvojenim stupcima
    num = parse_decimal(q) if re.fullmatch(r"[\d.,\s]+", q or "x") else None
    if num is not None and u in _UNIT_ALIASES:
        base, factor = _UNIT_ALIASES[u]
        # "0,02 kom" je zapravo težina u kg uz krivu jedinicu – ne znamo količinu
        if base == "kom" and num < 1:
            return _from_text(strip_accents((name or "").lower())) or (None, None)
        return (num * factor).normalize(), base

    # 2) "500 g", "6x0,33l" u stupcu količine, pa u nazivu
    for text in (q, strip_accents((name or "").lower())):
        found = _from_text(text)
        if found:
            return found

    if num is not None and not u:
        return num.normalize(), None
    if u in _UNIT_ALIASES:
        return None, _UNIT_ALIASES[u][0]
    return None, None


# --------------------------------------------------------------------------- #
# Tekst
# --------------------------------------------------------------------------- #

def clean_brand(brand: str | None) -> str | None:
    """Neki trgovci umjesto prazne marke pišu '#', '-', '.'."""
    if not brand or not re.search(r"[0-9A-Za-zČĆŽŠĐčćžšđ]", brand):
        return None
    return brand.strip(" `'\"#*-.,;")


def search_text(*parts: str | None) -> str:
    s = " ".join(p for p in parts if p)
    s = strip_accents(s).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_NOISE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:x\s*\d+(?:[.,]\d+)?)?\s*(?:g|gr|kg|dag|ml|cl|dl|l|kom)\b")


def match_name(name: str, brand: str | None) -> str:
    """Naziv za fuzzy usporedbu: bez količina, marke i interpunkcije."""
    s = search_text(name)
    s = re.sub(r"\s+", " ", _NOISE.sub(" ", s)).strip()
    if brand:
        without = s
        for tok in search_text(brand).split():
            without = re.sub(rf"\b{re.escape(tok)}\b", " ", without)
        without = re.sub(r"\s+", " ", without).strip()
        # "Nutella 400g" (marka Nutella) – bez marke ne bi ostalo ništa
        if without:
            s = without
    return s


def display_name(s: str) -> str:
    """KONZUM CSV-ovi su često u verzalu – za prikaz malo uljepšamo."""
    if s.isupper():
        return s[:1] + s[1:].lower()
    return s


# --------------------------------------------------------------------------- #
# Atributi za sužavanje pretrage ("koja mliječna mast?", "koja vrsta?")
# --------------------------------------------------------------------------- #

_PCT = re.compile(r"(?<![\d,.])(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
_MM = re.compile(r"(?<![\d,.])(\d{1,2}(?:[.,]\d)?)\s*m\.?\s*m\b")      # "2,8 mm", "3.2 m.m."
_DAIRY = ("mlijek", "jogurt", "vrhnj", "sir ", "sirni", "kefir", "maslac", "skut", "acidofil",
          "puding", "mozzarel", "gauda", "edamer", "mileram", "kiselo")
_ALCOHOL = ("pivo", "piva", "vino", "vina", "rakij", "liker", "vodk", "votk", "gin ", "whisk",
            "viski", "rum ", "konjak", "brandy", "pelinkovac", "travarica", "sljivovica", "prosecco",
            "pjenusac", "sampanj", " vol")
_COCOA = ("cokolad", "kakao", "cocoa")

#: kod -> (oznaka za prikaz, uzorci u nazivu bez dijakritike)
FEATURES = {
    "bl": ("bez laktoze", ("bez laktoze", "b laktoze", "lactose free", "laktoze free")),
    "bg": ("bez glutena", ("bez glutena", "gluten free", "glutenfree")),
    "bs": ("bez šećera", ("bez secera", "bez dodanog secera", "zero", "sugar free")),
    "t": ("trajno / UHT", ("trajno", "uht")),
    "s": ("svježe", ("svjeze", "svjez ", " svj ")),
    "bio": ("bio / eko", (" bio ", " eko ", "organic")),
    "l": ("light / lagano", ("light", "lagan")),
    "i": ("integralno", ("integral",)),
    "p": ("proteinsko", ("protein",)),
    "v": ("vegansko / biljno", ("vegan", "biljn")),
}


def product_attrs(name: str, brand: str | None = None) -> tuple[str | None, list[str]]:
    """Vrati (postotak s vrstom, npr. 'm2.8' / 'a5' / 'k70' / 'p10', [kodovi vrste]).

    m = mliječna mast, a = alkohol, k = udio kakaa, p = ostalo.
    """
    t = " " + strip_accents(f"{name} {brand or ''}").lower().replace("_", " ") + " "
    pct = None
    m = _PCT.search(t)
    mm = _MM.search(t)
    if m or mm:
        val = (m or mm).group(1).replace(",", ".")
        val = f"{float(val):g}"
        if mm or any(k in t for k in _DAIRY):
            kind = "m"
        elif any(k in t for k in _ALCOHOL):
            kind = "a"
        elif any(k in t for k in _COCOA):
            kind = "k"
        else:
            kind = "p"
        pct = kind + val
    words = re.sub(r"[^a-z0-9%]+", " ", t)
    words = f" {words} "
    feats = [code for code, (_, pats) in FEATURES.items()
             if any(p in t or p in words for p in pats)]
    return pct, feats
