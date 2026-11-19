"""Stvarne datoteke trgovaca (tests/fixtures/<trgovac>/real/*), preuzete
skriptom scripts/fetch_samples.py. Ako ih nema, testovi se preskaču."""
from pathlib import Path

import pytest

from app.adapters import all_adapters, get_adapter

FIX = Path(__file__).parent / "fixtures"
CASES = [(c, p) for c in all_adapters() for p in sorted((FIX / c / "real").glob("*"))
         if (FIX / c / "real").is_dir()]


@pytest.mark.skipif(not CASES, reason="nema stvarnih primjeraka (scripts/fetch_samples.py)")
@pytest.mark.parametrize("chain,path", CASES, ids=[f"{c}/{p.name}" for c, p in CASES])
def test_real_file_parses(chain, path):
    adapter = get_adapter(chain)()
    parsed = [sp for f in adapter.prepare(path.name, path.read_bytes()) for sp in adapter.parse(f)]
    assert parsed, "adapter nije vratio nijednu poslovnicu"
    for sp in parsed:
        assert sp.rows, f"{sp.source_file}: nema redova"
        assert sp.store.code
        ok = sum(1 for r in sp.rows if r.regular_price and r.regular_price > 0)
        assert ok / len(sp.rows) > 0.95
        with_ean = sum(1 for r in sp.rows if r.barcode)
        assert with_ean / len(sp.rows) > 0.5, "većina redova bi trebala imati barkod"
