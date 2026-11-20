"""Ingest + API na pravoj PostgreSQL bazi."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.adapters.base import RawFile, unpack

DAY = date.today() - timedelta(days=1)


def raw_files(fixtures, chain) -> list[RawFile]:
    return [f for p in sorted((fixtures / chain).iterdir()) if p.is_file() for f in unpack(p.name, p.read_bytes())]


@pytest.fixture()
def loaded(db_engine, fixtures):
    from app.db import SessionLocal
    from app.services.ingest import run_adapter

    results = {c: run_adapter(SessionLocal, c, DAY, files=raw_files(fixtures, c))
               for c in ("konzum", "lidl", "plodine", "kaufland", "spar")}
    return results


@pytest.fixture()
def client(loaded):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def q(engine, sql, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).all()


def product_id(engine, ean):
    return q(engine, "SELECT id FROM product WHERE ean = :e", e=ean)[0][0]


MLIJEKO, NUTELLA, KRUH = "3858882210041", "8000500310687", "3859991001230"


def test_runs_ok(loaded, db_engine):
    assert {r["status"] for r in loaded.values()} == {"ok"}
    assert loaded["konzum"]["stores"] == 3 and loaded["lidl"]["rows_skipped"] == 2
    assert loaded["kaufland"]["stores"] == 2 and loaded["spar"]["stores"] == 2


def test_matching(loaded, db_engine):
    # isti EAN kod sva tri trgovca -> jedan proizvod
    chains = q(db_engine, """SELECT c.code FROM chain_product cp JOIN chain c ON c.id = cp.chain_id
                             JOIN product p ON p.id = cp.product_id WHERE p.ean = :e ORDER BY 1""",
               e=MLIJEKO)
    assert [r[0] for r in chains] == ["interspar", "kaufland", "konzum", "lidl", "plodine", "spar"]
    # Plodine kruh nema EAN -> fuzzy spojen s Konzumovim (naziv + 500 g)
    m = q(db_engine, """SELECT cp.match_method, p.ean FROM chain_product cp
                        JOIN chain c ON c.id = cp.chain_id JOIN product p ON p.id = cp.product_id
                        WHERE (c.code, cp.code) IN (('plodine', '12004'), ('kaufland', '00990011'))""")
    assert m == [("fuzzy", KRUH), ("fuzzy", KRUH)]
    # banane: interni kodovi, ali isti naziv i 1 kg -> spojene po nazivu
    n = q(db_engine, """SELECT count(DISTINCT cp.product_id) FROM chain_product cp
                        WHERE cp.name ILIKE 'banane'""")
    assert n[0][0] == 1


def test_idempotent_same_day(loaded, db_engine, fixtures):
    from app.db import SessionLocal
    from app.services.ingest import run_adapter

    before = q(db_engine, "SELECT count(*) FROM price")[0][0]
    run_adapter(SessionLocal, "konzum", DAY, files=raw_files(fixtures, "konzum"))
    assert q(db_engine, "SELECT count(*) FROM price")[0][0] == before


def test_history_intervals(loaded, db_engine, fixtures):
    """Ista cijena -> produži interval; nova cijena -> novi red, stari ostaje."""
    from app.db import SessionLocal
    from app.services.ingest import run_adapter

    day2 = DAY + timedelta(days=1)
    files = raw_files(fixtures, "konzum")
    zg = next(f for f in files if "ZAGREB" in f.name)
    changed = RawFile(zg.name, zg.data.replace(b'"1,35","1,35"', b'"1,19","1,19"'))
    run_adapter(SessionLocal, "konzum", day2, files=[changed])

    rows = q(db_engine, """
        SELECT p.valid_from, p.last_seen, p.regular_price, p.is_current
        FROM price p JOIN chain_product cp ON cp.id = p.chain_product_id
        JOIN store s ON s.id = p.store_id JOIN chain c ON c.id = cp.chain_id
        WHERE c.code = 'konzum' AND s.code = '0204' AND cp.code = :code ORDER BY p.id""",
             code="100231")
    assert rows == [(DAY, DAY, Decimal("1.35"), False), (day2, day2, Decimal("1.19"), True)]
    cola = q(db_engine, """
        SELECT p.valid_from, p.last_seen FROM price p JOIN chain_product cp
        ON cp.id = p.chain_product_id JOIN store s ON s.id = p.store_id
        WHERE cp.code = '300100' AND s.code = '0204'""")
    assert cola == [(DAY, day2)]


def test_broken_file_logged(db_engine, fixtures):
    from app.db import SessionLocal
    from app.services.ingest import run_adapter

    files = raw_files(fixtures, "konzum") + raw_files(fixtures, "broken")
    r = run_adapter(SessionLocal, "konzum", DAY, files=files)
    assert r["status"] == "partial" and r["files_failed"] == 1
    errs = q(db_engine, "SELECT kind, file FROM ingest_error WHERE run_id = :r AND kind <> 'row'",
             r=r["run_id"])
    assert errs[0][0] == "format_changed" and errs[0][1].startswith("BROKEN")


def test_download_failure_logged(db_engine):
    from app.adapters import AdapterError
    from app.adapters.lidl import LidlAdapter
    from app.db import SessionLocal
    from app.services.ingest import run_adapter

    class Offline(LidlAdapter):
        def discover(self, day):
            raise AdapterError("nema cjenika")

    r = run_adapter(SessionLocal, "lidl", DAY, adapter=Offline())
    assert r["status"] == "failed" and "nema cjenika" in r["error"]


# --- API ------------------------------------------------------------------- #

def test_api_search_by_name_and_barcode(client):
    res = client.get("/api/search", params={"q": "svjeze mlijeko"}).json()
    assert res[0]["ean"] == MLIJEKO and res[0]["chains"] == 6
    assert Decimal(str(res[0]["min_price"])) == Decimal("1.15")
    res = client.get("/api/search", params={"q": NUTELLA}).json()
    assert len(res) == 1 and res[0]["on_sale"] is True


def test_api_prices_sorted_and_city_filter(client, db_engine):
    pid = product_id(db_engine, NUTELLA)
    data = client.get(f"/api/products/{pid}/prices").json()
    prices = [s["price"] for s in data["stores"]]
    assert prices == sorted(prices)
    assert [c["chain"] for c in data["chains"]] == \
        ["kaufland", "konzum", "plodine", "lidl", "interspar", "spar"]
    assert data["chains"][0]["min_price"] == 3.79 and data["chains"][0]["is_deal"]

    zg = client.get(f"/api/products/{pid}/prices", params={"city": "Zagreb"}).json()
    assert {s["city"] for s in zg["stores"]} == {"Zagreb"}
    assert len(zg["stores"]) == 5


def test_api_velika_gorica(client, db_engine):
    """Konzum (poštanski broj 10410), Kaufland i Interspar (grad iz imena
    datoteke) moraju završiti pod istim gradom."""
    pid = product_id(db_engine, NUTELLA)
    vg = client.get(f"/api/products/{pid}/prices", params={"city": "Velika Gorica"}).json()
    assert {s["chain"] for s in vg["stores"]} == {"konzum", "kaufland", "interspar"}
    assert {s["city"] for s in vg["stores"]} == {"Velika Gorica"}
    cities = {c["city"]: c["stores"] for c in client.get("/api/cities").json()}
    assert cities["Velika Gorica"] == 3


def test_api_history(client, db_engine):
    pid = product_id(db_engine, MLIJEKO)
    series = client.get(f"/api/products/{pid}/history", params={"days": 30}).json()
    by_chain = {s["chain"]: s["points"] for s in series}
    assert set(by_chain) == {"konzum", "lidl", "plodine", "kaufland", "spar", "interspar"}
    assert by_chain["lidl"][-1]["min"] == 1.15


def test_api_status_and_meta(client):
    st = client.get("/api/status").json()
    by = {a["adapter"]: a for a in st["adapters"]}
    assert all(by[c]["last_run"]["status"] == "ok" for c in ("konzum", "kaufland", "spar"))
    cities = {c["city"] for c in client.get("/api/cities").json()}
    assert {"Zagreb", "Split", "Prelog", "Jastrebarsko", "Velika Gorica"} <= cities
    assert client.get("/").status_code == 200


def test_api_suggest(client, db_engine):
    res = client.get("/api/suggest", params={"q": "nutel"}).json()
    assert res[0]["ean"] == NUTELLA and res[0]["chains"] == 6 and res[0]["qty"] == "400 g"
    assert client.get("/api/suggest", params={"q": MLIJEKO}).json()[0]["ean"] == MLIJEKO
    assert client.get("/api/suggest", params={"q": "nepostojeciproizvod"}).json() == []


def test_api_summary_per_chain(client, db_engine):
    pid = product_id(db_engine, NUTELLA)
    s = client.get(f"/api/products/{pid}/summary").json()
    by = {c["chain"]: c for c in s["chains"]}
    # Konzum: Zagreb 3,99 (akcija), Split i V. Gorica 4,99 -> medijan 4,99, min 3,99
    assert (by["konzum"]["price"], by["konzum"]["min"], by["konzum"]["stores"]) == (4.99, 3.99, 3)
    assert by["konzum"]["deal_stores"] == 1
    prices = [c["price"] for c in s["chains"]]
    assert prices == sorted(prices)
    assert {h["chain"] for h in s["history"]} == set(by)


def test_public_mode_hides_admin(loaded, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("PUBLIC_MODE", "1")
    with TestClient(app) as c:
        assert c.get("/api/status").status_code == 404
        assert c.get("/api/config").json()["public"] is True


def test_export_static(loaded, tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    from app.db import DATABASE_URL

    root = Path(__file__).resolve().parents[2]
    subprocess.run([sys.executable, str(root / "scripts/export_static.py"), str(tmp_path),
                    "--shards", "4"], check=True, env={"DATABASE_URL": DATABASE_URL, "PATH": "/usr/bin"})
    ix = json.loads((tmp_path / "data/index.json").read_text())
    nut = next(i for i in ix["items"] if i[1].lower().startswith("nutella"))
    assert nut[5] == 6 and nut[4] == 3.79
    shard = json.loads((tmp_path / f"data/p/{nut[0] % 4}.json").read_text())
    assert {c[0] for c in shard[str(nut[0])]["c"]} == set(ix["chains"])
    assert 'CIJENE_STATIC' in (tmp_path / "index.html").read_text()


def test_same_day_reload_fills_sale_details(loaded, db_engine, fixtures):
    """Ponovni unos istog dana dopunjava vrstu akcije, a povijest ostaje ista."""
    from app.db import SessionLocal
    from app.services.ingest import run_adapter

    before = q(db_engine, "SELECT count(*) FROM price")[0][0]
    files = raw_files(fixtures, "konzum")
    zg = next(f for f in files if "ZAGREB" in f.name)
    # isti cjenik, ali s vrstom akcije i datumom trajanja
    data = zg.data.decode().splitlines()
    data[0] += ',NAZIV POSEBNOG OBLIKA PRODAJE,AKCIJA VRIJEDI DO'
    data[1:] = [line + ',"Akcijska prodaja","24.11.2026"' for line in data[1:] if line]
    run_adapter(SessionLocal, "konzum", DAY,
                files=[RawFile(zg.name, ("\r\n".join(data) + "\r\n").encode())])
    assert q(db_engine, "SELECT count(*) FROM price")[0][0] == before
    row = q(db_engine, """SELECT p.sale_type, p.valid_until FROM price p
                          JOIN chain_product cp ON cp.id = p.chain_product_id
                          JOIN store s ON s.id = p.store_id
                          WHERE cp.code = '204411' AND s.code = '0204'""")
    assert row == [("Akcijska prodaja", date(2026, 11, 24))]
    d = q(db_engine, """SELECT d.deal_label, d.valid_until FROM chain_daily d
                        JOIN chain c ON c.id = d.chain_id JOIN product pr ON pr.id = d.product_id
                        WHERE c.code = 'konzum' AND pr.ean = :e""", e=NUTELLA)
    assert d == [("Akcijska prodaja", date(2026, 11, 24))]


def test_api_suggest_returns_attrs_for_refinement(client):
    res = client.get("/api/suggest", params={"q": "mlijeko", "limit": 100}).json()
    milk = next(r for r in res if r["ean"] == MLIJEKO)
    assert milk["pct"] == "m2.8" and "s" in milk["feat"]
    assert client.get("/api/config").json()["features"]["bl"] == "bez laktoze"


def test_api_categories_and_browse(client, db_engine):
    cats = {c["code"]: c for c in client.get("/api/categories").json()}
    mlijecni = cats["mlijecni"]
    assert mlijecni["name"] == "Mliječni proizvodi i jaja"
    subs = {s["code"]: s["count"] for s in mlijecni["subs"]}
    assert subs["mlijecni/mlijeko"] >= 1 and mlijecni["count"] == sum(subs.values())
    milk = client.get("/api/browse", params={"cat": "mlijecni/mlijeko"}).json()
    assert milk[0]["ean"] == MLIJEKO and milk[0]["chains"] == 6 and milk[0]["pct"] == "m2.8"
    whole = client.get("/api/browse", params={"cat": "mlijecni"}).json()
    assert {r["id"] for r in milk} <= {r["id"] for r in whole}
