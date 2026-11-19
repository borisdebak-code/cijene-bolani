"""Zajednički alati za čitanje službenih cjenika (CSV).

Trgovci objavljuju datoteke u različitim encodinzima (UTF-8, UTF-8 s BOM-om,
Windows-1250), s različitim delimiterima (`,` `;` `|` TAB) i decimalnim
zarezom ili točkom. Ovdje je sve to na jednom mjestu, tako da adapteri
trebaju samo opisati gdje se nalaze datoteke i kako se iz imena datoteke
čita poslovnica.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from .base import FormatChanged, PriceRow

# --------------------------------------------------------------------------- #
# Encoding
# --------------------------------------------------------------------------- #

def decode_bytes(data: bytes) -> tuple[str, str]:
    """Vrati (tekst, encoding). Redoslijed: UTF-8 (s/bez BOM) -> Windows-1250.

    Windows-1250 može dekodirati gotovo svaki niz bajtova, pa ga probamo
    zadnjeg. UTF-16 prepoznajemo po BOM-u (neki ERP izvozi ga koriste).
    """
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    return data.decode("cp1250", errors="replace"), "cp1250"


# --------------------------------------------------------------------------- #
# Brojevi
# --------------------------------------------------------------------------- #

_NUM_CLEAN = re.compile(r"[^\d,.\-]")


def parse_decimal(value: str | None) -> Decimal | None:
    """'1,99' / '1.99' / '1.234,56' / '1,234.56' / '€ 2,49' / '' -> Decimal|None."""
    if value is None:
        return None
    value = str(value)
    if "=" in value:                       # Kaufland: "MPC 2.5.2025=1,99€"
        value = value.rsplit("=", 1)[1]
    s = _NUM_CLEAN.sub("", value.strip())
    if not s or s in {"-", ".", ","}:
        return None
    if "," in s and "." in s:
        # zadnji separator je decimalni
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # samo zarez: decimalni, osim ako izgleda kao tisućice (1,234)
        head, _, tail = s.rpartition(",")
        if s.count(",") > 1:
            s = s.replace(",", "")
        else:
            s = f"{head}.{tail}"
    elif s.count(".") > 1:
        head, _, tail = s.rpartition(".")
        s = head.replace(".", "") + "." + tail
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if d < 0:
        return None
    return d


# --------------------------------------------------------------------------- #
# Zaglavlja
# --------------------------------------------------------------------------- #

def strip_accents(s: str) -> str:
    s = s.replace("đ", "dj").replace("Đ", "Dj")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_header(h: str) -> str:
    h = strip_accents(h or "").lower()
    h = re.sub(r"[^a-z0-9]+", " ", h)
    return h.strip()


# Redoslijed je bitan: specifičnija pravila prije općenitijih.
# Svako pravilo: (polje, [riječi koje sve moraju biti prisutne], [riječi koje ne smiju])
_HEADER_RULES: list[tuple[str, list[str], list[str]]] = [
    # tekstualni/datumski stupci oko akcija – prije cijena, jer i oni sadrže "posebn"/"akcij"
    ("sale_type", ["naziv", "poseb"], []),
    ("sale_type", ["vrst", "akcij"], []),
    ("sale_type", ["tip", "akcij"], []),
    ("valid_until", ["vrijed", "do"], []),
    ("valid_until", ["traj"], []),
    ("valid_until", ["datum", "zavr"], []),
    ("valid_until", ["kraj"], []),
    ("card_price", ["kartic"], []),
    ("card_price", ["multiplus"], []),
    ("card_price", ["loyal"], []),
    ("card_price", ["card"], []),
    ("card_price", ["clansk"], []),
    ("base_price", ["bazn"], []),
    # "Naziv posebnog oblika prodaje" je tekst (npr. "AKCIJA"), ne cijena
    ("special_price", ["poseb"], ["naziv"]),        # "poseban", "posebnog"
    ("special_price", ["akcij"], ["naziv"]),
    ("lowest_30d", ["najniz"], []),
    ("anchor_price", ["sidren"], []),
    ("unit_price", ["cijena", "jedinic"], []),
    ("unit_price", ["cijena", "jm"], []),
    ("unit_price", ["cijena", "kg"], []),
    ("unit_price", ["cijena", "jed", "mj"], []),        # Kaufland: "cijena jed.mj.(EUR)"
    ("regular_price", ["maloprod"], []),                # "maloprodajna" i "maloprod."
    ("regular_price", ["mpc"], []),
    ("regular_price", ["cijena"], []),
    ("unit", ["jedinic", "mjer"], []),
    ("unit", ["jm"], []),
    ("quantity", ["neto"], []),
    ("quantity", ["kolicin"], []),
    ("quantity", ["gramaz"], []),
    ("barcode", ["barkod"], []),
    ("barcode", ["bar", "kod"], []),
    ("barcode", ["ean"], []),
    ("barcode", ["gtin"], []),
    ("category", ["kategorij"], []),
    ("category", ["grupa"], []),
    ("brand", ["marka"], []),
    ("brand", ["brand"], []),
    ("brand", ["proizvodjac"], []),
    ("code", ["sifra"], []),
    ("code", ["artikl", "id"], []),
    ("name", ["naziv"], []),
    ("name", ["opis"], []),
]

REQUIRED_FIELDS = {"name", "regular_price"}
NUMERIC_FIELDS = {"regular_price", "unit_price", "special_price", "lowest_30d", "anchor_price",
                  "base_price", "card_price"}
_FLAG_VALUES = {"da", "ne", "a", "x", "y", "n", "1", "0", "true", "false", "akcija", ""}
_TRUTHY = {"da", "a", "x", "y", "1", "true", "akcija"}
_CARD_NAMES = [("multiplus", "MultiPlus"), ("kaufland card", "Kaufland Card"),
               ("lidl plus", "Lidl Plus"), ("spar plus", "SPAR plus"), ("moj studenac", "Moj Studenac"),
               ("tommy", "Tommy kartica"), ("plodine", "Plodine kartica")]


def map_headers(headers: list[str], numeric_cols: set[int] | None = None,
                flag_cols: set[int] | None = None) -> dict[str, int]:
    """Mapira stupce na kanonska polja. Baca FormatChanged ako nedostaju
    obavezni stupci – to je signal da je trgovac promijenio format.

    numeric_cols: stupci čije su vrijednosti brojevi – cijene se smiju mapirati
    samo na njih (Kaufland "akc.cijena, A=akcija" sadrži samo slovo "A").
    flag_cols: stupci s DA/NE ili A – postaju "sale_flag" ako spominju akciju.
    """
    mapping: dict[str, int] = {}
    for idx, raw in enumerate(headers):
        words = norm_header(raw)
        tokens = words.split()
        if flag_cols and idx in flag_cols and "sale_flag" not in mapping and any(
                t.startswith(("akc", "poseb")) for t in tokens) and "naziv" not in tokens:
            mapping["sale_flag"] = idx
            continue
        for field, must, must_not in _HEADER_RULES:
            if field in mapping:
                continue
            if field in NUMERIC_FIELDS and numeric_cols is not None and idx not in numeric_cols:
                continue
            if all(any(t.startswith(m) for t in tokens) for m in must) and not any(
                any(t.startswith(m) for t in tokens) for m in must_not
            ):
                mapping[field] = idx
                break
    missing = REQUIRED_FIELDS - mapping.keys()
    if missing:
        raise FormatChanged(
            f"Nedostaju obavezni stupci {sorted(missing)}; zaglavlje: {headers!r}"
        )
    return mapping


def card_name_from_header(header: str) -> str:
    h = norm_header(header)
    for key, name in _CARD_NAMES:
        if key in h:
            return name
    return "kartica vjernosti"


_DATE_PATTERNS = [
    (re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"), ("y", "m", "d")),
    (re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})"), ("d", "m", "y")),
    (re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{2})\b"), ("d", "m", "y2")),
]


def parse_date(value: str | None):
    """'24.11.2026.', '2026-11-24', 'do 24.11.26' -> date; raspon '13.11.-24.11.2026' -> kraj."""
    from datetime import date

    if not value:
        return None
    found = None
    for rx, order in _DATE_PATTERNS:
        for m in rx.finditer(value):
            parts = dict(zip(order, m.groups()))
            y = int(parts["y"]) if "y" in parts else 2000 + int(parts["y2"])
            try:
                d = date(y, int(parts["m"]), int(parts["d"]))
            except ValueError:
                continue
            found = d if found is None or d > found else found
        if found:
            return found
    return None


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #

def sniff_delimiter(header_line: str) -> str:
    counts = {d: header_line.count(d) for d in (";", ",", "\t", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def clean_cell(v: str | None) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", " ", v.replace("\xa0", " ")).strip()


def _column_kinds(rows: list[list[str]], ncols: int) -> tuple[set[int], set[int]]:
    """Koji su stupci brojčani, a koji zastavice (DA/NE, A), po uzorku redova."""
    numeric, flags = set(), set()
    for i in range(ncols):
        vals = [clean_cell(r[i]) for r in rows if i < len(r) and clean_cell(r[i])]
        if not vals:
            numeric.add(i)          # prazan stupac ne smeta (npr. nema akcija danas)
            continue
        low = {v.lower() for v in vals}
        if low <= _FLAG_VALUES:
            flags.add(i)
            continue
        ok = sum(1 for v in vals if parse_decimal(v) is not None)
        if ok / len(vals) >= 0.8:
            numeric.add(i)
    return numeric, flags


def parse_price_csv(data: bytes) -> tuple[list[PriceRow], list[str], str]:
    """Parsira CSV cjenik u listu PriceRow.

    Vraća (redovi, upozorenja, encoding). Neispravni pojedinačni redovi ne
    ruše cijelu datoteku – zapisuju se u upozorenja.
    """
    text, encoding = decode_bytes(data)
    lines = text.splitlines()
    # preskoči prazne linije na početku
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        raise FormatChanged("Prazna datoteka")
    delimiter = sniff_delimiter(lines[0])
    all_rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter, quotechar='"'))
    headers = [clean_cell(h) for h in all_rows[0]]
    body = all_rows[1:]
    numeric, flags = _column_kinds(body[:500], len(headers))
    mapping = map_headers(headers, numeric, flags)
    card_name = card_name_from_header(headers[mapping["card_price"]]) if "card_price" in mapping else None

    rows: list[PriceRow] = []
    warnings: list[str] = []

    def get(cells: list[str], field: str) -> str:
        i = mapping.get(field)
        if i is None or i >= len(cells):
            return ""
        return clean_cell(cells[i])

    for lineno, cells in enumerate(body, start=2):
        if not cells or not any(c.strip() for c in cells):
            continue
        name = get(cells, "name")
        regular = parse_decimal(get(cells, "regular_price"))
        special = parse_decimal(get(cells, "special_price"))
        flagged = get(cells, "sale_flag").lower() in _TRUTHY
        if not name:
            warnings.append(f"red {lineno}: nema naziva")
            continue
        if regular is None and special is None:
            warnings.append(f"red {lineno}: nema cijene ({name!r})")
            continue
        if flagged and special is None:
            # Konzum: "POSEBAN OBLIK PRODAJE"=DA, MPC je akcijska, "BAZNA CIJENA" redovna
            base = parse_decimal(get(cells, "base_price"))
            special = regular
            if base is not None and regular is not None and base > regular:
                regular = base
        sale_type = get(cells, "sale_type") or None
        # ista akcijska i redovna cijena: akcija samo ako je trgovac tako označio
        # (Kaufland/Spar za vrijeme akcije u MPC upisuju akcijsku cijenu)
        if special is not None and special == regular and not flagged and not sale_type:
            special = None
        if special is not None and not sale_type:
            sale_type = "Akcija"
        card = parse_decimal(get(cells, "card_price"))
        effective = min(x for x in (regular, special) if x is not None)
        rows.append(
            PriceRow(
                code=get(cells, "code") or None,
                name=name,
                brand=get(cells, "brand") or None,
                quantity=get(cells, "quantity") or None,
                unit=get(cells, "unit") or None,
                barcode=get(cells, "barcode") or None,
                category=get(cells, "category") or None,
                regular_price=regular if regular is not None else special,
                unit_price=parse_decimal(get(cells, "unit_price")),
                special_price=special,
                lowest_30d=parse_decimal(get(cells, "lowest_30d")),
                anchor_price=parse_decimal(get(cells, "anchor_price")),
                sale_type=sale_type if special is not None else None,
                valid_until=parse_date(get(cells, "valid_until")) if special is not None else None,
                card_price=card if card is not None and card < effective else None,
                card_name=card_name if card is not None and card < effective else None,
            )
        )
    return rows, warnings, encoding
