from decimal import Decimal

import pytest

from app.adapters import FormatChanged
from app.adapters.csvutil import decode_bytes, map_headers, parse_decimal, parse_price_csv
from app.normalize import match_name, normalize_ean, parse_quantity


@pytest.mark.parametrize("raw,expected", [
    ("1,99", "1.99"), ("1.99", "1.99"), ("1.234,56", "1234.56"), ("1,234.56", "1234.56"),
    ("€ 2,49", "2.49"), ("2,49 €", "2.49"), ("  0,5 ", "0.5"), ("12", "12"),
    ("", None), (None, None), ("-", None), ("n/a", None),
])
def test_parse_decimal(raw, expected):
    assert parse_decimal(raw) == (Decimal(expected) if expected else None)


def test_decode_utf8_bom_and_cp1250():
    assert decode_bytes("﻿Čokolada".encode("utf-8")) == ("Čokolada", "utf-8")
    text, enc = decode_bytes("Čokolada žuta đ".encode("cp1250"))
    assert text == "Čokolada žuta đ" and enc == "cp1250"


@pytest.mark.parametrize("header", [
    # Konzum
    "NAZIV PROIZVODA,ŠIFRA PROIZVODA,MARKA PROIZVODA,NETO KOLIČINA,JEDINICA MJERE,"
    "MALOPRODAJNA CIJENA,CIJENA ZA JEDINICU MJERE,MPC ZA VRIJEME POSEBNOG OBLIKA PRODAJE,"
    "NAJNIŽA CIJENA U POSLJEDNJIH 30 DANA,SIDRENA CIJENA NA 2.5.2025,BARKOD,KATEGORIJA PROIZVODA",
    # Lidl
    "NAZIV,ŠIFRA,NETO_KOLIČINA,JEDINICA_MJERE,MARKA,MALOPRODAJNA_CIJENA,CIJENA_ZA_JEDINICU_MJERE,"
    "MPC_ZA_VRIJEME_POSEBNOG_OBLIKA_PRODAJE,NAJNIZA_CIJENA_U_POSLJ._30_DANA,"
    "Sidrena_cijena_na_02.05.2025,BARKOD,KATEGORIJA_PROIZVODA",
    # Plodine
    "Naziv proizvoda,Sifra proizvoda,Marka proizvoda,Neto kolicina,Jedinica mjere,"
    "Maloprodajna cijena,Cijena po JM,MPC za vrijeme posebnog oblika prodaje,"
    "Najniza cijena u poslj. 30 dana,Sidrena cijena na 2.5.2025,Barkod,Kategorija proizvoda",
])
def test_map_headers_known_chains(header):
    m = map_headers(header.split(","))
    assert m == {"name": 0, "code": 1, "brand": m["brand"], "quantity": m["quantity"],
                 "unit": m["unit"], "regular_price": 5, "unit_price": 6, "special_price": 7,
                 "lowest_30d": 8, "anchor_price": 9, "barcode": 10, "category": 11}
    assert {m["brand"], m["quantity"], m["unit"]} == {2, 3, 4}


def test_map_headers_format_changed():
    with pytest.raises(FormatChanged, match="Nedostaju obavezni stupci"):
        map_headers(["PRODUCT", "SKU", "PRICE EUR"])


def test_parse_csv_semicolon_and_bad_rows():
    data = ("Naziv;Maloprodajna cijena;Barkod\n"
            "Mlijeko;1,29;3858882210041\n"
            ";1,00;\n"            # bez naziva
            "Kruh;;\n"            # bez cijene
            "\n").encode()
    rows, warnings, enc = parse_price_csv(data)
    assert [r.name for r in rows] == ["Mlijeko"]
    assert rows[0].regular_price == Decimal("1.29")
    assert len(warnings) == 2


@pytest.mark.parametrize("raw,expected", [
    ("3858882210041", "3858882210041"),
    ("3858882210042", None),                       # loš checksum
    ("3858882210041;3858882210999", "3858882210041"),
    ("x;5449000000996", "5449000000996"),
    ("049000000443", "0049000000443"),             # UPC-A -> 13
    ("2000500001009", None),                       # interni kod (vaga)
    ("96385074", "96385074"),                      # EAN-8
    ("", None), (None, None), ("0000000000000", None),
])
def test_normalize_ean(raw, expected):
    assert normalize_ean(raw) == expected


@pytest.mark.parametrize("q,u,name,expected", [
    ("1", "L", None, (Decimal("1000"), "ml")),
    ("0,5", "l", None, (Decimal("500"), "ml")),
    ("0.4", "kg", None, (Decimal("400"), "g")),
    ("500", "G", None, (Decimal("500"), "g")),
    ("500 g", None, None, (Decimal("500"), "g")),
    (None, None, "Pivo 6x0,33 l", (Decimal("1980"), "ml")),
    ("1", "KOM", None, (Decimal("1"), "kom")),
    (None, None, "Banane", (None, None)),
])
def test_parse_quantity(q, u, name, expected):
    assert parse_quantity(q, u, name) == expected


def test_match_name_strips_brand_and_qty():
    assert match_name("KRUH BIJELI 500 G", "KONZUM") == "kruh bijeli"
    assert match_name("Nutella krema 400g", "Ferrero") == "nutella krema"
