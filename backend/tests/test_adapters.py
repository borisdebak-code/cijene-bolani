"""Testovi adaptera na primjercima datoteka (tests/fixtures/<trgovac>/)."""
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.adapters import FormatChanged, all_adapters, default_adapters, get_adapter
from app.adapters.base import RawFile, unpack


def load(fixtures, chain):
    adapter = get_adapter(chain)()
    files = []
    for p in sorted(x for x in (fixtures / chain).iterdir() if x.is_file()):
        files.extend(adapter.prepare(p.name, p.read_bytes()))
    return [sp for f in files for sp in adapter.parse(f)]


def test_registry_discovers_all_adapter_files():
    assert {"konzum", "lidl", "plodine", "kaufland", "spar", "cijenedev"} <= set(all_adapters())
    assert "cijenedev" not in default_adapters()


def test_konzum(fixtures):
    stores = {sp.store.code: sp for sp in load(fixtures, "konzum")}
    zg = stores["0204"]
    assert (zg.store.address, zg.store.postcode, zg.store.city, zg.store.name) == \
        ("Put Gajeva 2", "10000", "Zagreb", "Supermarket")
    assert stores["0811"].store.city == "Split"
    assert zg.encoding == "utf-8" and len(zg.rows) == 6 and not zg.warnings
    nut = next(r for r in zg.rows if r.code == "204411")
    assert nut.regular_price == Decimal("4.99")
    assert nut.special_price == Decimal("3.99")
    assert nut.lowest_30d == Decimal("4.49") and nut.anchor_price == Decimal("4.79")
    assert next(r for r in zg.rows if r.code == "600011").name.startswith("ČOKOLADA")


def test_lidl_zip_cp1250(fixtures):
    stores = {sp.store.code: sp for sp in load(fixtures, "lidl")}
    assert set(stores) == {"265", "102"}
    pr = stores["265"]
    assert (pr.store.address, pr.store.postcode, pr.store.city) == \
        ("Ulica Franje Glada 13", "40323", "Prelog")
    assert stores["102"].store.address == "Zagrebačka Cesta 204"
    assert pr.encoding == "cp1250"
    milk = pr.rows[0]
    assert milk.name == "Svježe mlijeko 2.8% m.m." and milk.regular_price == Decimal("1.19")
    assert ";" in milk.barcode
    assert len(pr.rows) == 4 and len(pr.warnings) == 1   # čokolada bez cijene


def test_plodine(fixtures):
    stores = {sp.store.code: sp for sp in load(fixtures, "plodine")}
    j = stores["064"]
    assert (j.store.address, j.store.postcode, j.store.city, j.store.name) == \
        ("Ulica Franje Tudjmana 4", "10450", "Jastrebarsko", "Supermarket")
    assert stores["015"].store.address == "Slavonska Avenija 11a"
    nut = next(r for r in j.rows if r.code == "12002")
    assert (nut.regular_price, nut.special_price, nut.unit_price) == \
        (Decimal("4.89"), Decimal("3.99"), Decimal("12.23"))


def test_cijenedev_multi_chain(fixtures):
    sps = load(fixtures, "cijenedev")
    assert [(sp.chain, sp.store.code, len(sp.rows)) for sp in sps] == [("spar", "S1", 2)]
    cola = sps[0].rows[1]
    assert cola.special_price == Decimal("1.39") and cola.lowest_30d == Decimal("1.59")


def test_broken_format_raises(fixtures):
    p = next((fixtures / "broken").iterdir())
    with pytest.raises(FormatChanged):
        list(get_adapter("konzum")().parse(RawFile(p.name, p.read_bytes())))


# --- discover() uz lažni HTTP ------------------------------------------------ #

def mock_client(pages: dict[str, str]) -> httpx.Client:
    def handler(req: httpx.Request):
        key = str(req.url)
        for prefix, body in pages.items():
            if key.startswith(prefix):
                return httpx.Response(200, text=body)
        return httpx.Response(200, text="<html></html>")
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_konzum_discover_paginates():
    page1 = ('<a href="/cjenici/download?title=SUPERMARKET%2CA%2C0001.CSV">x</a>'
             '<a href="/cjenici/download?title=SUPERMARKET%2CB%2C0002.CSV&amp;format=csv">y</a>')
    page2 = '<a href="/cjenici/download?title=SUPERMARKET%2CC%2C0003.CSV">z</a>'
    c = mock_client({
        "https://www.konzum.hr/cjenici?date=2026-11-19&page=1": page1,
        "https://www.konzum.hr/cjenici?date=2026-11-19&page=2": page2,
    })
    urls = get_adapter("konzum")(c).discover(date(2026, 11, 19))
    assert len(urls) == 3 and urls[1].endswith("&format=csv")


@pytest.mark.parametrize("chain,url", [
    ("lidl", "https://tvrtka.lidl.hr/cijene"),
    ("plodine", "https://www.plodine.hr/info-o-cijenama"),
])
def test_zip_discover_picks_date(chain, url):
    html = ('<a href="/files/Popis_cijena_18_11_2026.zip">a</a>'
            '<a href="https://cdn.example/x/cjenici_19_11_2026_08_00.zip">b</a>')
    urls = get_adapter(chain)(mock_client({url: html})).discover(date(2026, 11, 19))
    assert urls == ["https://cdn.example/x/cjenici_19_11_2026_08_00.zip"]


def test_kaufland_tab_and_short_headers(fixtures):
    stores = {sp.store.code: sp for sp in load(fixtures, "kaufland")}
    vg = stores["4630"]
    assert (vg.store.city, vg.store.address, vg.store.name) == \
        ("Velika Gorica", "Zagrebacka Ulica 44", "Hipermarket")
    assert stores["4100"].store.city == "Zagreb"
    nut = next(r for r in vg.rows if r.code == "00445566")
    assert (nut.regular_price, nut.special_price, nut.unit_price, nut.lowest_30d) == \
        (Decimal("4.69"), Decimal("3.79"), Decimal("11.73"), Decimal("4.29"))
    assert (nut.quantity, nut.unit) == ("0,4", "kg") and not vg.warnings


def test_spar_and_interspar(fixtures):
    sps = {sp.store.code: sp for sp in load(fixtures, "spar")}
    vg, zg = sps["8740"], sps["8102"]
    assert (vg.chain, vg.store.city, vg.store.address) == \
        ("interspar", "Velika Gorica", "Zagrebacka Ulica 1")
    assert (zg.chain, zg.store.city, zg.store.address) == ("spar", "Zagreb", "Ilica 250")
    assert vg.encoding == "cp1250"
    cola = next(r for r in vg.rows if r.code == "301177")
    assert (cola.regular_price, cola.special_price) == (Decimal("1.65"), Decimal("1.29"))


def test_spar_discover_json():
    import json

    body = json.dumps({"files": [
        {"name": "a.csv", "URL": "https://www.spar.hr/datoteke_cjenici/a.csv"},
        {"name": "b.csv", "URL": "https://www.spar.hr/datoteke_cjenici/b.csv"}]})
    c = mock_client({"https://www.spar.hr/datoteke_cjenici/Cjenik20261119.json": body})
    assert get_adapter("spar")(c).discover(date(2026, 11, 19)) == [
        "https://www.spar.hr/datoteke_cjenici/a.csv", "https://www.spar.hr/datoteke_cjenici/b.csv"]


def test_kaufland_discover_via_asset_json():
    import json

    page = '<div data-props="{&quot;settingsUrl&quot;:&quot;/popis-mpc.assetSearch.id=assetList_1.json&quot;}">'
    assets = json.dumps([
        {"label": "Hipermarket_Velika_Gorica_X_4630_19112026_7-30.csv",
         "path": "/files/Hipermarket_Velika_Gorica_X_4630_19112026_7-30.csv"},
        {"label": "stari", "path": "/files/Hipermarket_Zagreb_X_4100_18112026_7-30.csv"}])
    c = mock_client({
        "https://www.kaufland.hr/popis-mpc.assetSearch": assets,
        "https://www.kaufland.hr/akcije-novosti/popis-mpc.html": page,
    })
    assert get_adapter("kaufland")(c).discover(date(2026, 11, 19)) == [
        "https://www.kaufland.hr/files/Hipermarket_Velika_Gorica_X_4630_19112026_7-30.csv"]


def test_spar_real_filename_store_code():
    from app.adapters.spar import SparAdapter

    s = SparAdapter.store_from_filename(
        "hipermarket_zadar_bleiburskih_zrtava_18_8701_interspar_8701_zd_bleib._0567_20261119_0330.csv")
    assert (s.code, s.city, s.address) == ("8701", "Zadar", "Bleiburskih Zrtava 18")


def test_konzum_new_header_text_column_ignored():
    from app.adapters.csvutil import map_headers

    h = ("NAZIV PROIZVODA,ŠIFRA PROIZVODA,MARKA PROIZVODA,NETO KOLIČINA,JEDINICA MJERE,"
         "MALOPRODAJNA CIJENA,POSEBAN OBLIK PRODAJE,NAZIV POSEBNOG OBLIKA PRODAJE,"
         "CIJENA ZA JEDINICU MJERE,BARKOD,BAZNA CIJENA").split(",")
    m = map_headers(h)
    assert (m["special_price"], m["unit_price"], m["barcode"]) == (6, 8, 9)
    assert 7 not in m.values()
