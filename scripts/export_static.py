"""Izvoz aplikacije u statičnu stranicu (bez servera).

    cd backend && python ../scripts/export_static.py OUT_DIR [--artifact] [--days 90] [--shards 128]

Rezultat:
    OUT_DIR/index.html        sučelje (isti app.js kao server, u statičnom načinu)
    OUT_DIR/data/index.json   indeks za pretragu: [id, naziv, marka, količina, min. cijena, br. trgovaca, dodatne riječi]
    OUT_DIR/data/p/<k>.json   sažetak proizvoda (cijene po trgovcu + povijest), k = id % shards

--artifact: stranica bez <html>/<head> omotača, CSS i JS ugrađeni, Chart.js s CDN-a
            (za objavu kao claude.ai artifact). Bez toga: samostalna stranica za bilo
            koji statični hosting (GitHub Pages, Netlify, S3, nginx...).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from sqlalchemy import text  # noqa: E402

from app.api.routes import _qty_label  # noqa: E402
from app.db import engine  # noqa: E402
from app.categories import tree as category_tree  # noqa: E402
from app.normalize import FEATURES, product_attrs, search_text  # noqa: E402

FE = ROOT / "frontend"
CHART_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.js"


def money(v):
    return None if v is None else round(float(v), 2)


def own_brands(excl: dict, total: dict, share: float = 0.8) -> dict[str, str]:
    out = {}
    for b, per in excl.items():
        chain, n = max(per.items(), key=lambda kv: kv[1])
        if n >= share * total[b]:
            out[b] = chain
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--artifact", action="store_true")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--shards", type=int, default=128)
    args = ap.parse_args()

    out: Path = args.out
    for sub in ("p", "c"):
        (out / "data" / sub).mkdir(parents=True, exist_ok=True)
        for old in (out / "data" / sub).glob("*.json"):
            old.unlink()

    with engine.connect() as c:
        chains = {r.code: r.name for r in c.execute(text("SELECT code, name FROM chain"))}
        updated = c.execute(text("SELECT max(last_day) FROM chain")).scalar()
        current = c.execute(text(
            """SELECT d.product_id, c.code, d.price_median, d.price_min, d.price_max,
                      d.regular_median, d.stores, d.deal_stores, d.deal_label, d.valid_until,
                      d.card_price, d.card_name
               FROM chain_daily d JOIN chain c ON c.id = d.chain_id AND d.day = c.last_day
               ORDER BY d.product_id, d.price_median, d.price_min""")).all()
        by_product: dict[int, list] = defaultdict(list)
        for r in current:
            row = [r.code, money(r.price_median), money(r.price_min), money(r.price_max),
                   money(r.regular_median), r.stores, r.deal_stores]
            # podaci o akciji i kartici samo kad postoje (štedi prostor)
            extra = [r.deal_label if r.deal_stores else None,
                     r.valid_until.isoformat() if r.valid_until else None,
                     money(r.card_price), r.card_name]
            while extra and extra[-1] is None:
                extra.pop()
            by_product[r.product_id].append(row + extra)
        hist: dict[int, dict] = defaultdict(lambda: defaultdict(list))
        since = (updated or date.today()) - timedelta(days=args.days)
        for r in c.execute(text(
                """SELECT d.product_id, c.code, d.day, d.price_median FROM chain_daily d
                   JOIN chain c ON c.id = d.chain_id WHERE d.day >= :s
                   ORDER BY d.product_id, c.code, d.day"""), {"s": since}):
            pts = hist[r.product_id][r.code]
            p = money(r.price_median)
            # spremi samo promjene (+ zadnji dan) – graf je stepenast
            if pts and pts[-1][1] == p:
                pts[-1][2] = r.day.isoformat()
            else:
                pts.append([r.day.isoformat(), p, r.day.isoformat()])
        products = c.execute(text(
            "SELECT id, name, brand, quantity, unit, ean, search_text, cat FROM product WHERE id = ANY(:ids)"),
            {"ids": list(by_product)}).all()

    items, shards, eans = [], defaultdict(dict), []
    by_chain: dict[str, list] = defaultdict(list)       # trgovina -> [[id, cijena]]
    # marka -> {trgovac (spar = spar + interspar) -> broj artikala samo kod njega}, ukupno artikala
    brand_excl: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    brand_total: dict[str, int] = defaultdict(int)
    cat_counts: dict[str, int] = defaultdict(int)
    for p in products:
        cs = by_product[p.id]
        qty = _qty_label(p.quantity, p.unit)
        own = set(search_text(p.name, p.brand).split())
        if p.ean:
            own.add(p.ean)
            eans.append([p.id, p.ean])
        # nazivi istog proizvoda kod drugih trgovaca (bez barkodova – oni su u eans.json)
        extra = " ".join(t for t in dict.fromkeys(p.search_text.split())
                         if t not in own and not (t.isdigit() and len(t) >= 8))
        pct, feat = product_attrs(p.name, p.brand)
        cat = p.cat if p.cat and p.cat != "-" else None
        for x in cs:
            by_chain[x[0]].append([p.id, x[1]])
        if p.brand:
            b = p.brand.strip().lower()
            groups = {"spar" if x[0] == "interspar" else x[0] for x in cs}
            brand_total[b] += 1
            if len(groups) == 1:
                brand_excl[b][next(iter(groups))] += 1
        items.append([p.id, p.name, p.brand, qty, min(x[1] for x in cs), len(cs), extra or None,
                      pct, ",".join(feat) or None, cat])
        if cat:
            cat_counts[cat] += 1
        h = {}
        for chain, pts in hist.get(p.id, {}).items():
            flat = []
            for d0, price, d1 in pts:
                flat.append([d0, price])
                if d1 != d0:
                    flat.append([d1, price])
            h[chain] = flat
        shards[p.id % args.shards][p.id] = {"n": p.name, "b": p.brand, "q": qty, "e": p.ean,
                                            "c": cs, "h": h}

    items.sort(key=lambda x: (-x[5], x[1]))
    dump = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    # osnovni indeks (proizvodi kod 2+ trgovaca) učitava se odmah, ostatak u pozadini,
    # barkodovi tek kad korisnik upiše broj – da prva pretraga na mobitelu bude brza
    core = [i for i in items if i[5] >= 2]
    rest = [i for i in items if i[5] < 2]
    index = {"updated": updated.isoformat() if updated else None, "chains": chains,
             "features": {k: v[0] for k, v in FEATURES.items()},
             "categories": [{**c, "subs": [{**sc, "count": cat_counts.get(sc["code"], 0)} for sc in c["subs"]],
                             "count": sum(cat_counts.get(sc["code"], 0) for sc in c["subs"])}
                            for c in category_tree()],
             "shards": args.shards, "items": core,
             # vlastite marke: marka koju prodaje samo jedan trgovac (najčešće njegova robna marka)
             # (≥ 80 % artikala marke samo kod jednog trgovca – pokoji krivo spojen artikl ne smeta)
             "own": own_brands(brand_excl, brand_total),
             "chain_items": {c: len(v) for c, v in by_chain.items()}, "rest": "index-rest.json", "eans": "eans.json"}
    (out / "data" / "index.json").write_text(dump(index))
    (out / "data" / "index-rest.json").write_text(dump(rest))
    (out / "data" / "eans.json").write_text(dump(eans))
    for code, rows in by_chain.items():
        (out / "data" / "c" / f"{code}.json").write_text(dump(rows))
    for k in range(args.shards):
        (out / "data" / "p" / f"{k}.json").write_text(
            json.dumps(shards.get(k, {}), ensure_ascii=False, separators=(",", ":")))

    html = (FE / "index.html").read_text()
    body = html.split("<!--APP-->", 1)[1].split("</body>", 1)[0]
    css = (FE / "style.css").read_text()
    js = (FE / "app.js").read_text()
    boot = 'window.CIJENE_STATIC = { base: "data/" };'
    if args.artifact:
        page = (f'<title>Cijene BoLaNI</title>\n<meta name="claude:viewport-fit-sides" content="cover">\n'
                f"<style>\n{css}\n</style>\n<script src=\"{CHART_CDN}\"></script>\n{body}\n"
                f"<script>\n{boot}\n{js}\n</script>\n")
    else:
        (out / "vendor").mkdir(exist_ok=True)
        shutil.copy(FE / "vendor" / "chart.umd.js", out / "vendor" / "chart.umd.js")
        shutil.copy(FE / "style.css", out / "style.css")
        shutil.copy(FE / "app.js", out / "app.js")
        # instalabilna aplikacija (PWA): manifest, ikone, servisni radnik
        shutil.copy(FE / "manifest.webmanifest", out / "manifest.webmanifest")
        shutil.copytree(FE / "icons", out / "icons", dirs_exist_ok=True)
        build = hashlib.sha1((js + css + str(updated)).encode()).hexdigest()[:10]
        (out / "sw.js").write_text((FE / "sw.js").read_text().replace("__BUILD__", build))
        (out / ".nojekyll").write_text("")
        boot = 'window.CIJENE_STATIC = { base: "data/", pwa: true };'
        page = (html.replace("/static/", "")
                .replace('<script src="app.js" defer></script>',
                         f'<script>{boot}</script>\n  <script src="app.js" defer></script>'))
    (out / "index.html").write_text(page)

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"{len(items)} proizvoda, {len(chains)} trgovaca, podaci od {updated}; "
          f"{size / 1e6:.1f} MB u {out}")


if __name__ == "__main__":
    main()
