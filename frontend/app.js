"use strict";

/* Cijene BoLaNI – javno sučelje.
 * Korisnik pretražuje proizvode (padajući izbornik), otvara usporedbu po
 * trgovcima i slaže popis za kupnju (donji desni kut). Popis se čuva samo u
 * njegovom pregledniku.
 *
 * Podaci dolaze iz API-ja (server) ili iz statičnih JSON datoteka
 * (window.CIJENE_STATIC = { base: "data/" }), s istim oblikom odgovora.
 */

const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const qEl = $("#q");
const STATIC = window.CIJENE_STATIC || null;
let chart = null;
let CONFIG = { public: true };

const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* privatni način */ } },
};

const eur = (v) => v == null ? "–" :
  Number(v).toLocaleString("hr-HR", { style: "currency", currency: "EUR" });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const norm = (s) => String(s ?? "").replace(/[đĐ]/g, "dj").normalize("NFKD")
  .replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
const plural = (n, one, few, many) => {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
};
const stores = (n) => `${n} ${plural(n, "poslovnica", "poslovnice", "poslovnica")}`;
const shopsN = (n) => `${n} ${plural(n, "trgovina", "trgovine", "trgovina")}`;

// ===================================================================== data
async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(r.status === 404 ? "Nije pronađeno" : `Greška ${r.status}`);
  return r.json();
}

const data = (() => {
  const summaries = new Map();
  let indexP = null, eansP = null;
  let restLoaded = false, restP = null;
  const shards = new Map();

  const expand = ([id, name, brand, qty, min, chains, extra, pct, feat, cat]) => ({
    id, name, brand, qty, min_price: min, chains, pct, feat: feat ? feat.split(",") : [], cat,
    hay: " " + norm(`${name} ${brand ?? ""} ${extra ?? ""}`) + " ",
    nm: " " + norm(name) + " ",
  });

  function loadIndex() {
    if (!indexP) {
      indexP = getJSON(STATIC.base + "index.json").then((ix) => {
        ix.items = ix.items.map(expand);
        // ostatak (proizvodi samo kod jednog trgovca) dolazi u pozadini
        restP = getJSON(STATIC.base + ix.rest).then((rest) => {
          for (const r of rest) ix.items.push(expand(r));
          restLoaded = true;
          if (!$("#sug").hidden) updateSug();
        }).catch(() => {});
        return ix;
      });
    }
    return indexP;
  }

  function loadEans() {
    if (!eansP) eansP = loadIndex().then((ix) => getJSON(STATIC.base + ix.eans)).then((rows) => new Map(rows));
    return eansP;
  }

  return {
    async meta() {
      if (STATIC) { const ix = await loadIndex(); return { public: true, updated: ix.updated, chains: ix.chains, features: ix.features }; }
      return getJSON("/api/config");
    },
    async suggest(q, limit = 8) {
      if (!STATIC) return getJSON(`/api/suggest?q=${encodeURIComponent(q)}&limit=${limit}`);
      const ix = await loadIndex();
      const digits = q.replace(/\D/g, "");
      const toks = norm(q).split(" ").filter(Boolean);
      if (!toks.length) return [];
      const byCode = digits.length >= 8 && digits.length >= q.trim().length - 1;
      const eans = byCode ? await loadEans() : null;
      const out = [];
      for (const it of ix.items) {
        // svaka riječ mora odgovarati početku neke riječi u nazivu; barkod se traži u eans.json
        if (byCode ? !(eans.get(it.id) || "").startsWith(digits) : !toks.every((t) => it.hay.includes(" " + t))) continue;
        let score = Math.min(it.chains, 6) * 2;
        // riječi u samom nazivu (ne samo u nazivima drugih trgovaca), naziv počinje upitom
        if (toks.every((t) => it.nm.includes(" " + t))) score += 7;
        if (it.nm.startsWith(" " + toks[0])) score += 5;
        score -= it.name.length / 40;
        out.push([score, it]);
      }
      return out.sort((a, b) => b[0] - a[0]).slice(0, limit).map(([, it]) => it);
      // (rezultat ide u renderSug, koji iz njega slaže i pitanja za sužavanje)
    },
    async categories() {
      if (!STATIC) return getJSON("/api/categories");
      return (await loadIndex()).categories || [];
    },
    // proizvodi u (pot)kategoriji, najprije oni koje ima više trgovaca
    async browse(cat) {
      if (!STATIC) return getJSON(`/api/browse?cat=${encodeURIComponent(cat)}&limit=2000`);
      const ix = await loadIndex();
      await restP;
      const pre = cat + "/";
      return ix.items.filter((it) => it.cat && (it.cat === cat || it.cat.startsWith(pre)))
        .sort((a, b) => b.chains - a.chains || a.name.localeCompare(b.name, "hr"));
    },
    async summary(id) {
      id = Number(id);
      if (summaries.has(id)) return summaries.get(id);
      let s;
      if (STATIC) {
        const ix = await loadIndex();
        const k = id % ix.shards;
        if (!shards.has(k)) shards.set(k, getJSON(`${STATIC.base}p/${k}.json`));
        s = (await shards.get(k))[id];
        if (!s) throw new Error("Nije pronađeno");
        s = expandSummary(id, s, ix.chains);
      } else {
        s = await getJSON(`/api/products/${id}/summary`);
      }
      summaries.set(id, s);
      return s;
    },
  };
})();

// statični zapis: {n, b, q, e, c: [[chain, price, min, max, regular, stores, deals]], h: {chain: [[day, price]]}}
function expandSummary(id, s, chainNames) {
  return {
    id, name: s.n, brand: s.b, qty: s.q, ean: s.e,
    chains: s.c.map(([chain, price, min, max, regular, st, deals, label, until, card, cardName]) => ({
      chain, chain_name: chainNames[chain] || chain, price, min, max, regular, stores: st, deal_stores: deals,
      deal_label: label ?? null, valid_until: until ?? null, card_price: card ?? null, card_name: cardName ?? null,
    })),
    history: Object.entries(s.h || {}).map(([chain, pts]) => ({
      chain, chain_name: chainNames[chain] || chain, points: pts.map(([day, price]) => ({ day, price })),
    })),
  };
}

// ===================================================================== basket
const basket = {
  items: store.get("cijene.basket", []),   // [{id, qty, name, label, chain?}] – chain: korisnikov odabir trgovine
  save() {
    store.set("cijene.basket", this.items); renderFab();
    if (!$("#panel").hidden) renderPanel();
    markAdded();
  },
  find(id) { return this.items.find((i) => i.id === Number(id)); },
  add(p, n = 1, chain = null, chainName = null) {
    const it = this.find(p.id);
    if (it) { it.qty += n; if (chain) it.chain = chain; }
    else this.items.push({ id: Number(p.id), qty: n, name: p.name,
      label: [p.brand && prettyBrand(p.brand), p.qty].filter(Boolean).join(" · "), ...(chain ? { chain } : {}) });
    this.save();
    toast(`Dodano na popis${chainName ? ` (${chainName})` : ""}: ${p.name}`);
  },
  setChain(id, chain) {
    const it = this.find(id); if (!it) return;
    if (chain) it.chain = chain; else delete it.chain;
    this.save();
  },
  setQty(id, qty) {
    const it = this.find(id); if (!it) return;
    if (qty <= 0) return this.remove(id);
    it.qty = qty;
    this.save();
  },
  // uklanjanje se uvijek može poništiti ("Vrati") – lako je slučajno dodirnuti
  remove(id) {
    const idx = this.items.findIndex((i) => i.id === Number(id));
    if (idx < 0) return;
    const [it] = this.items.splice(idx, 1);
    this.save();
    toast(`Uklonjeno: ${it.name}`, "Vrati", () => {
      if (!this.find(it.id)) this.items.splice(Math.min(idx, this.items.length), 0, it);
      this.save();
    });
  },
  clear() {
    const before = this.items;
    if (!before.length) return;
    this.items = []; this.save();
    toast(`Popis je ispražnjen (${before.length} ${plural(before.length, "proizvod", "proizvoda", "proizvoda")})`, "Vrati", () => {
      this.items = before; this.save();
    });
  },
};

function toast(msg, actionLabel, action) {
  const t = $("#toast");
  t.innerHTML = `<span class="t-msg"></span>${actionLabel ? `<button class="t-act">${esc(actionLabel)}</button>` : ""}`;
  t.querySelector(".t-msg").textContent = msg;
  if (actionLabel) t.querySelector(".t-act").onclick = () => { t.hidden = true; action(); };
  t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, actionLabel ? 6000 : 2200);
}

// trgovci s istom najnižom cijenom (unutar pola centa)
const tiedChains = (s) => {
  if (!s.chains.length) return [];
  const min = Math.min(...s.chains.map((c) => c.price));
  return s.chains.filter((c) => c.price - min < 0.005).sort((a, b) => a.chain_name.localeCompare(b.chain_name, "hr"));
};

// Dodavanje na popis: ako više trgovina ima istu najnižu cijenu, korisnik bira trgovinu.
async function addToBasket(p) {
  if (basket.find(p.id)) return basket.add(p);
  let s = null;
  try { s = await data.summary(p.id); } catch { /* bez sažetka: dodaj bez izbora */ }
  const ties = s ? tiedChains(s) : [];
  if (ties.length < 2) return basket.add(p);
  const choice = await pickShop(p, ties);
  if (choice === undefined) return;                 // odustao
  basket.add(p, 1, choice?.chain ?? null, choice?.chain_name ?? null);
}

let pickResolve = null;
function pickShop(p, ties) {
  $("#pick-title").textContent = `Ista najniža cijena u ${ties.length} ${plural(ties.length, "trgovini", "trgovine", "trgovina")}`;
  $("#pick-sub").textContent = `${p.name} – ${eur(ties[0].price)}. U koju trgovinu ga staviti na popis?`;
  $("#pick-opts").innerHTML = ties.map((c, i) => `
    <button class="pick-opt" data-pick="${i}">
      <span class="po-name">${esc(c.chain_name)}</span>
      <span class="po-meta small muted">${c.deal_stores ? '<span class="badge deal">akcija</span> ' : ""}${stores(c.stores)}</span>
      <strong class="po-price">${eur(c.price)}</strong>
    </button>`).join("");
  $("#pick").hidden = false;
  $("#pick-opts button")?.focus();
  return new Promise((resolve) => {
    pickResolve = (v) => { $("#pick").hidden = true; pickResolve = null; resolve(v); };
    $("#pick-opts").onclick = (e) => { const b = e.target.closest("[data-pick]"); if (b) pickResolve(ties[Number(b.dataset.pick)]); };
  });
}
$("#pick-later").addEventListener("click", () => pickResolve?.(null));
$("#pick").addEventListener("click", (e) => { if (e.target.id === "pick") pickResolve?.(undefined); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && pickResolve) pickResolve(undefined); }, true);

// najjeftiniji trgovac za proizvod (po uobičajenoj cijeni, pa po najnižoj)
const best = (s) => s.chains.slice().sort((a, b) => a.price - b.price || a.min - b.min)[0];

async function basketPlan() {
  const rows = await Promise.all(basket.items.map(async (it) => {
    try { return { it, s: await data.summary(it.id) }; } catch { return { it, s: null }; }
  }));
  const byChain = new Map();
  const allChains = new Map();
  let total = 0;
  for (const r of rows) {
    if (!r.s || !r.s.chains.length) continue;
    // korisnik je odabrao trgovinu (npr. kod iste najniže cijene) – poštuj odabir
    const picked = r.it.chain && r.s.chains.find((c) => c.chain === r.it.chain);
    const b = picked || best(r.s);
    r.best = b;
    r.picked = !!picked;
    const withCard = r.s.chains.filter((c) => c.card_price != null && c.card_price < b.price)
      .sort((x, y) => x.card_price - y.card_price)[0];
    r.card = withCard || null;
    total += b.price * r.it.qty;
    if (!byChain.has(b.chain)) byChain.set(b.chain, { chain: b.chain, name: b.chain_name, items: [], total: 0 });
    const g = byChain.get(b.chain);
    g.items.push(r); g.total += b.price * r.it.qty;
    for (const c of r.s.chains) {
      if (!allChains.has(c.chain)) allChains.set(c.chain, { chain: c.chain, name: c.chain_name, total: 0, have: 0 });
      const a = allChains.get(c.chain); a.total += c.price * r.it.qty; a.have += 1;
    }
  }
  const priced = rows.filter((r) => r.best).length;
  return {
    rows, total, priced,
    groups: [...byChain.values()].sort((a, b) => b.items.length - a.items.length || a.name.localeCompare(b.name, "hr")),
    oneShop: [...allChains.values()].sort((a, b) => (b.have - a.have) || (a.total - b.total)),
  };
}

function renderFab() {
  const n = basket.items.reduce((a, i) => a + i.qty, 0);
  $("#fab-count").textContent = n;
  $("#fab-count").hidden = n === 0;
  $("#fab").setAttribute("aria-label", `Popis za kupnju, ${n} proizvoda`);
}

let panelTab = store.get("cijene.tab", "shops");

async function renderPanel() {
  const body = $("#panel-body");
  $("#panel").querySelectorAll("[data-tab]").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === panelTab));
  if (!basket.items.length) {
    body.innerHTML = `<div class="p-empty">
      <p><strong>Popis je prazan.</strong></p>
      <p class="muted">Potraži proizvod i dodaj ga gumbom <span class="kbd">+</span>. Ovdje ćeš vidjeti gdje je koji proizvod najjeftiniji i dobiti zaseban popis za svaku trgovinu.</p></div>`;
    $("#panel-foot").innerHTML = "";
    return;
  }
  const plan = await basketPlan();
  if (panelTab === "shops") {
    body.innerHTML = plan.groups.map((g) => `
      <section class="shop">
        <header><h3>${esc(g.name)}</h3><span class="sum">${eur(g.total)}</span></header>
        <ul>${g.items.map((r) => `<li>
          <span class="q">${r.it.qty}×</span>
          <a href="#" data-go="${r.it.id}" class="nm">${esc(r.it.name)}</a>
          <span class="pr">${eur(r.best.price * r.it.qty)}</span>
          <button class="rm" data-rm="${r.it.id}" aria-label="Ukloni s popisa: ${esc(r.it.name)}" title="Ukloni s popisa">${TRASH_ICON}</button></li>`).join("")}
        </ul>
        <div class="shop-actions">
          <button class="ghost sm" data-copy="${esc(g.chain)}">${SHARE_ICON}Pošalji popis za ${esc(g.name)}</button>
          <a class="ghost sm wa" href="${waLink(groupText(g))}" target="_blank" rel="noopener" aria-label="Pošalji popis za ${esc(g.name)} na WhatsApp">${WA_ICON}</a>
        </div>
      </section>`).join("") + missing(plan);
  } else if (panelTab === "items") {
    body.innerHTML = `<ul class="items">${plan.rows.map((r) => `<li>
        <div class="it-main">
          <a href="#" data-go="${r.it.id}" class="nm">${esc(r.it.name)}</a>
          <div class="muted small">${esc(r.it.label || "")}</div>
          ${r.best ? `<label class="small shop-pick">Trgovina:
            <select data-chain-for="${r.it.id}" aria-label="Trgovina za ${esc(r.it.name)}">
              <option value="">najjeftinija (${esc(best(r.s).chain_name)})</option>
              ${r.s.chains.slice().sort((a, b) => a.price - b.price).map((c) =>
                `<option value="${esc(c.chain)}"${r.picked && c.chain === r.it.chain ? " selected" : ""}>${esc(c.chain_name)} – ${eur(c.price)}</option>`).join("")}
            </select></label>` : '<div class="small muted">Trenutno nema cijene</div>'}
          ${r.card ? cardLine(r.card.card_price, r.card.card_name, r.card.chain_name) : ""}
        </div>
        <div class="stepper" role="group" aria-label="Količina">
          <button class="ghost sm" data-dec="${r.it.id}" aria-label="Manje">−</button>
          <span>${r.it.qty}</span>
          <button class="ghost sm" data-inc="${r.it.id}" aria-label="Više">+</button>
          <button class="rm" data-rm="${r.it.id}" aria-label="Ukloni s popisa: ${esc(r.it.name)}" title="Ukloni s popisa">${TRASH_ICON}</button>
        </div></li>`).join("")}</ul>
      <button class="link danger" id="clear">Isprazni popis</button>`;
  } else {
    const n = plan.priced;
    body.innerHTML = `<p class="muted small">Što ako sve kupiš u jednoj trgovini (${n} ${plural(n, "proizvod", "proizvoda", "proizvoda")} s cijenom):</p>
      <table class="one"><tbody>${plan.oneShop.map((c, i) => `<tr class="${c.have === n && i === 0 ? "win" : ""}">
        <td>${esc(c.name)}</td>
        <td class="num">${eur(c.total)}</td>
        <td class="small muted">${c.have === n ? "sve ima" : `nema ${n - c.have}`}</td></tr>`).join("")}</tbody></table>`;
  }
  const saving = plan.oneShop.find((c) => c.have === plan.priced);
  const all = listText(plan);
  $("#panel-foot").innerHTML = `
    <div><span class="muted small">Ukupno, svaki proizvod gdje je najjeftiniji</span><strong>${eur(plan.total)}</strong></div>
    ${saving && saving.total > plan.total + 0.005 ? `<div class="small muted">U jednoj trgovini najmanje ${eur(saving.total)} (${esc(saving.name)})</div>` : ""}
    <div class="export" role="group" aria-label="Pošalji popis">
      <button class="primary sm" data-share="all">${SHARE_ICON}Podijeli</button>
      <a class="ghost sm wa" href="${waLink(all)}" target="_blank" rel="noopener">${WA_ICON}WhatsApp</a>
      <button class="ghost sm" data-copyall="1">Kopiraj</button>
    </div>`;
  $("#panel-foot [data-share]").onclick = () => shareText(all, "Popis za kupnju");
  $("#panel-foot [data-copyall]").onclick = (e) => copyText(all, e.currentTarget, "Popis kopiran – zalijepi ga u Bilješke ili poruku");

  body.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => {
    const g = plan.groups.find((x) => x.chain === b.dataset.copy);
    shareText(groupText(g), `${g.name} – popis za kupnju`, b);
  }));
}

// ---- tekst popisa za Bilješke / WhatsApp / poruku
const itemLine = (r) => `☐ ${r.it.qty}× ${r.it.name}${r.it.label ? ` (${r.it.label})` : ""} – ${eur(r.best.price * r.it.qty)}`;

function groupText(g) {
  return `${g.name} – popis za kupnju\n${g.items.map(itemLine).join("\n")}\nUkupno: ${eur(g.total)}`;
}

function listText(plan) {
  const parts = ["Popis za kupnju (Cijene BoLaNI)", ""];
  for (const g of plan.groups) {
    parts.push(`${g.name.toUpperCase()} – ${eur(g.total)}`, ...g.items.map(itemLine), "");
  }
  const miss = plan.rows.filter((r) => !r.best);
  if (miss.length) parts.push("BEZ CIJENE", ...miss.map((r) => `☐ ${r.it.qty}× ${r.it.name}`), "");
  parts.push(`Ukupno: ${eur(plan.total)}`);
  if (META?.updated) parts.push(`Cijene od ${fmtDate(META.updated)}`);
  return parts.join("\n");
}

const waLink = (text) => `https://wa.me/?text=${encodeURIComponent(text)}`;

// Izbornik dijeljenja na mobitelu (Bilješke, WhatsApp, Viber, e-mail...). Gdje ga
// preglednik ne nudi (npr. stranica ugrađena u drugu stranicu), popis se kopira.
async function shareText(text, title, anchor) {
  if (navigator.share) {
    try { await navigator.share({ title, text }); return; }
    catch (e) { if (e && e.name === "AbortError") return; }
  }
  copyText(text, anchor, "Dijeljenje ovdje nije dostupno – popis je kopiran, zalijepi ga u Bilješke ili poruku");
}

function copyText(text, anchor, msg) {
  const fallback = () => {
    const ta = document.createElement("textarea"); ta.value = text; ta.className = "copy-fallback"; ta.readOnly = true;
    (anchor || $("#panel-foot")).after(ta); ta.focus(); ta.select();
    toast("Označeno – kopiraj ručno (dugi pritisak → Kopiraj)");
  };
  try { navigator.clipboard.writeText(text).then(() => toast(msg || "Kopirano"), fallback); } catch { fallback(); }
}

const TRASH_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4.5h6V7m-8 0 1 12.5a1.5 1.5 0 0 0 1.5 1.5h5a1.5 1.5 0 0 0 1.5-1.5L17 7M10 11v6m4-6v6"/></svg>';
const SHARE_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15V3m0 0-4 4m4-4 4 4"/><path d="M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7"/></svg>';
const WA_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11.5a8.5 8.5 0 0 1-12.6 7.4L3.5 20l1.1-3.8A8.5 8.5 0 1 1 20 11.5Z"/></svg>';

function missing(plan) {
  const m = plan.rows.filter((r) => !r.best);
  return m.length ? `<section class="shop muted"><header><h3>Bez cijene</h3></header><ul>${m.map((r) =>
    `<li><span class="q">${r.it.qty}×</span><span class="nm">${esc(r.it.name)}</span><span></span><button class="rm" data-rm="${r.it.id}" aria-label="Ukloni s popisa: ${esc(r.it.name)}" title="Ukloni s popisa">${TRASH_ICON}</button></li>`).join("")}</ul></section>` : "";
}

function openPanel(open) {
  $("#panel").hidden = !open;
  $("#fab").setAttribute("aria-expanded", open);
  if (open) renderPanel();
}

// ===================================================================== search dropdown
// Pretraga vraća sve kandidate (do 400), a odavde se slažu pitanja za sužavanje
// izbora: količina, proizvođač, postotak (mliječna mast / alkohol / kakao) i vrsta.
let sugTimer = null, sugSeq = 0, sugItems = [], sugActive = -1;
let sugQuery = "", sugAll = [], sugFilters = {};
const MAX_CANDIDATES = 400;
let FEATURE_LABELS = {};

function closeSug() { $("#sug").hidden = true; sugActive = -1; qEl.setAttribute("aria-expanded", "false"); }

const PCT_LABEL = { m: "Mliječna mast", a: "Alkohol", k: "Udio kakaa", p: "Postotak" };
const prettyBrand = (b) => (b === b.toUpperCase() && /[A-ZČĆŽŠĐ]{3}/.test(b))
  ? b.toLowerCase().replace(/(^|[\s\-.])(\p{L})/gu, (m, s1, c) => s1 + c.toUpperCase()) : b;
// "1.5 l" -> [1500, "ml"] za sortiranje
function qtyValue(label) {
  const m = /^([\d.]+)\s*(\S*)/.exec(label || "");
  if (!m) return [Infinity, ""];
  const n = parseFloat(m[1]), u = m[2];
  return u === "kg" ? [n * 1000, "g"] : u === "l" ? [n * 1000, "ml"] : [n, u];
}

// facete: za svako pitanje broji opcije među proizvodima koji zadovoljavaju OSTALE odabire
const FACETS = [
  { key: "qty", get: (p) => (p.qty ? [p.qty] : []),
    label: (items) => {
      const u = majority(items.map((p) => qtyValue(p.qty)[1]));
      return { ml: "Koja zapremnina?", g: "Koja težina?", kom: "Koliko komada?" }[u] || "Koja količina?";
    },
    sort: (a, b) => qtyValue(a.value)[0] - qtyValue(b.value)[0],
    show: (v) => v.replace(".", ",") },
  { key: "brand", get: (p) => (p.brand ? [norm(p.brand)] : []), label: () => "Koji proizvođač?",
    sort: (a, b) => b.count - a.count, show: (v, items) => prettyBrand(items.find((p) => p.brand && norm(p.brand) === v).brand) },
  { key: "pct", get: (p) => (p.pct ? [p.pct] : []),
    label: (items) => `${PCT_LABEL[majority(items.map((p) => p.pct?.[0]).filter(Boolean))] || "Postotak"}?`,
    only: (items) => { const k = majority(items.map((p) => p.pct?.[0]).filter(Boolean)); return (v) => v[0] === k; },
    sort: (a, b) => parseFloat(a.value.slice(1)) - parseFloat(b.value.slice(1)),
    show: (v) => `${v.slice(1).replace(".", ",")} %` },
  { key: "feat", get: (p) => p.feat || [], label: () => "Koja vrsta?",
    sort: (a, b) => b.count - a.count, show: (v) => FEATURE_LABELS[v] || v },
];

function majority(arr) {
  const c = {}; let best = null;
  for (const x of arr) { c[x] = (c[x] || 0) + 1; if (best == null || c[x] > c[best]) best = x; }
  return best;
}

const matches = (p, filters, skip) => FACETS.every((f) =>
  f.key === skip || filters[f.key] == null || f.get(p).includes(filters[f.key]));

function buildFacets(all, filters) {
  const out = [];
  for (const f of FACETS) {
    const base = all.filter((p) => matches(p, filters, f.key));
    const only = f.only ? f.only(base) : () => true;
    const counts = new Map();
    for (const p of base) for (const v of f.get(p)) if (only(v)) counts.set(v, (counts.get(v) || 0) + 1);
    let opts = [...counts].map(([value, count]) => ({ value, count }));
    const selected = filters[f.key];
    // pitanje ima smisla samo ako dijeli rezultate na barem dvije skupine
    if (selected == null && (opts.length < 2 || opts.every((o) => o.count === base.length))) continue;
    opts = opts.sort((a, b) => b.count - a.count).slice(0, 12).sort(f.sort);
    if (selected != null && !opts.some((o) => o.value === selected)) opts.unshift({ value: selected, count: 0 });
    out.push({ key: f.key, label: f.label(base), opts: opts.map((o) => ({ ...o, text: f.show(o.value, base) })) });
  }
  return out;
}

async function updateSug() {
  const q = qEl.value.trim();
  if (q.length < 2) return closeSug();
  const seq = ++sugSeq;
  let items;
  try { items = await data.suggest(q, MAX_CANDIDATES); } catch { items = []; }
  if (seq !== sugSeq) return;
  if (norm(q) !== sugQuery) { sugFilters = {}; sugQuery = norm(q); }
  sugAll = items;
  renderSug();
}

const LIST_MAX = 60;   // toliko redova se iscrtava; ostalo se dobiva sužavanjem izbora

function refineHTML(facets, filters, total) {
  const anySel = Object.values(filters).some((v) => v != null);
  return `
      <div class="r-head"><strong>${total} ${plural(parseInt(total, 10), "proizvod", "proizvoda", "proizvoda")}</strong>
        <span class="muted">· suzi izbor</span>
        ${anySel ? '<button class="link r-clear" data-clear="1">Poništi</button>' : ""}</div>
      ${facets.map((f) => `<div class="q" role="group" aria-label="${esc(f.label)}" data-qkey="${f.key}">
        <div class="q-label">${esc(f.label)}</div>
        <div class="q-opts">${f.opts.map((o) => {
          const on = filters[f.key] === o.value;
          return `<button class="opt${on ? " on" : ""}" data-facet="${f.key}" data-val="${esc(o.value)}" aria-pressed="${on}">
            ${esc(o.text)}${on ? ' <span aria-hidden="true">×</span>' : ` <span class="cnt">${o.count}</span>`}</button>`;
        }).join("")}</div></div>`).join("")}`;
}

// nakon odabira opcije pitanja se iscrtaju ponovno – zadrži gdje je korisnik bio
// (npr. pomaknuo je red do "1 l"), ne vraćaj na početak
function keepScroll(container, render) {
  const pos = {};
  container.querySelectorAll("[data-qkey]").forEach((q) => { pos[q.dataset.qkey] = q.querySelector(".q-opts").scrollLeft; });
  const top = container.scrollTop;
  render();
  container.querySelectorAll("[data-qkey]").forEach((q) => {
    if (pos[q.dataset.qkey] != null) q.querySelector(".q-opts").scrollLeft = pos[q.dataset.qkey];
  });
  container.scrollTop = top;
}

function rowHTML(p, i, prefix = "sug") {
  return `
    <li role="option" id="${prefix}-${i}" data-i="${i}">
      <div class="s-main"><div class="s-name">${esc(p.name)}</div>
        <div class="muted small s-meta">${esc([p.brand && prettyBrand(p.brand), p.qty].filter(Boolean).join(" · "))}</div></div>
      <div class="s-price"><strong>${p.min_price != null ? "od " + eur(p.min_price) : ""}</strong>
        <div class="muted small s-chains">${shopsN(p.chains)}</div></div>
      <button class="add" data-add="${i}" data-pid="${p.id}" aria-label="Dodaj na popis: ${esc(p.name)}" title="Dodaj na popis">+</button>
    </li>`;
}

function renderSug() {
  const filtered = sugAll.filter((p) => matches(p, sugFilters));
  sugItems = filtered.slice(0, LIST_MAX); sugActive = -1;
  const facets = sugAll.length > 1 ? buildFacets(sugAll, sugFilters) : [];
  const anySel = Object.values(sugFilters).some((v) => v != null);
  const list = $("#sug-list");
  const listChanged = list.dataset.sig !== sugQuery + JSON.stringify(sugFilters);
  list.dataset.sig = sugQuery + JSON.stringify(sugFilters);
  list.innerHTML = sugItems.length ? sugItems.map((p, i) => rowHTML(p, i)).join("") + (filtered.length > LIST_MAX
      ? `<li class="more muted small" role="presentation">i još ${filtered.length - LIST_MAX} – suzi izbor${facets.length ? " ispod" : " preciznijim upitom"}</li>` : "")
    : `<li class="none muted">Nema proizvoda za „${esc(qEl.value.trim())}”${anySel ? " uz odabrane uvjete" : ""}</li>`;
  const total = `${filtered.length}${sugAll.length >= MAX_CANDIDATES && !anySel ? "+" : ""}`;
  const ref = $("#sug-refine");
  keepScroll(ref, () => { ref.innerHTML = facets.length ? refineHTML(facets, sugFilters, total) : ""; });
  $("#sug-refine").hidden = !facets.length;
  $("#sug").hidden = false;
  if (listChanged) list.scrollTop = 0;
  qEl.setAttribute("aria-expanded", "true");
  fitSug();
  updateBar();
  markAdded();
}

// proizvodi koji su već na popisu: "+" postaje kvačica s količinom (jasno je koji je dodan)
function markAdded(root) {
  const roots = root ? [root] : [$("#sug-list"), $("#cat-list")].filter(Boolean);
  for (const btn of roots.flatMap((r) => [...r.querySelectorAll("[data-pid]")])) {
    const it = basket.find(btn.dataset.pid);
    btn.classList.toggle("in", !!it);
    btn.innerHTML = it ? `<span aria-hidden="true">✓</span>${it.qty}` : "+";
    btn.title = it ? `Na popisu (${it.qty}) – dodaj još` : "Dodaj na popis";
  }
}

// ---- vlastiti klizač uz rezultate (mobilni preglednici skrivaju standardnu traku)
const sbar = $(".sbar"), sthumb = $(".sthumb");
function updateBar() {
  const l = $("#sug-list");
  const max = l.scrollHeight - l.clientHeight;
  sbar.hidden = max <= 2;
  if (max <= 2) return;
  const track = sbar.clientHeight;
  const h = Math.max(32, track * l.clientHeight / l.scrollHeight);
  sthumb.style.height = `${h}px`;
  sthumb.style.transform = `translateY(${(l.scrollTop / max) * (track - h)}px)`;
}
function barScrollTo(clientY, grabOffset) {
  const l = $("#sug-list"), r = sbar.getBoundingClientRect();
  const h = sthumb.offsetHeight;
  const ratio = Math.min(1, Math.max(0, (clientY - r.top - grabOffset) / (r.height - h)));
  l.scrollTop = ratio * (l.scrollHeight - l.clientHeight);
}
let grab = null;
sbar.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  const t = sthumb.getBoundingClientRect();
  grab = e.target === sthumb ? e.clientY - t.top : sthumb.offsetHeight / 2;
  sbar.setPointerCapture(e.pointerId);
  sbar.classList.add("drag");
  barScrollTo(e.clientY, grab);
});
sbar.addEventListener("pointermove", (e) => { if (grab != null) barScrollTo(e.clientY, grab); });
const endGrab = () => { grab = null; sbar.classList.remove("drag"); };
sbar.addEventListener("pointerup", endGrab);
sbar.addEventListener("pointercancel", endGrab);
$("#sug-list").addEventListener("scroll", updateBar, { passive: true });

// visina izbornika prema vidljivom dijelu ekrana (na mobitelu tipkovnica zauzme pola)
function fitSug() {
  const box = $("#sug");
  if (box.hidden) return;
  const vv = window.visualViewport;
  const vh = vv ? vv.height + vv.offsetTop : window.innerHeight;
  const top = box.getBoundingClientRect().top;
  // mobitel: dok je polje aktivno, tipkovnica zauzme ~pola ekrana – izbornik ostaje iznad nje
  // (i kad preglednik ne javi promjenu vidljivog dijela); bez tipkovnice najviše ~55 %
  const mobile = window.innerWidth < 640;
  const typing = mobile && document.activeElement === qEl;
  const cap = mobile ? window.innerHeight * (typing ? 0.4 : 0.55) : 820;
  const maxH = Math.max(200, Math.min(cap, vh - top - 10));
  box.style.maxHeight = `${Math.round(maxH)}px`;
  // pitanja najviše ~45 % izbornika, ostatak rezultati (postotak ne radi unutar flexa s max-height)
  $("#sug-refine").style.maxHeight = `${Math.round(maxH * 0.45)}px`;
  updateBar();
}
window.visualViewport?.addEventListener("resize", fitSug);
window.visualViewport?.addEventListener("scroll", fitSug);
qEl.addEventListener("blur", () => setTimeout(fitSug, 300));
qEl.addEventListener("focus", () => setTimeout(fitSug, 300));
window.addEventListener("resize", fitSug);

function setActive(i) {
  const lis = [...$("#sug-list").querySelectorAll("li[data-i]")];
  if (!lis.length) return;
  sugActive = (i + lis.length) % lis.length;
  lis.forEach((li, k) => li.classList.toggle("active", k === sugActive));
  qEl.setAttribute("aria-activedescendant", `sug-${sugActive}`);
  lis[sugActive].scrollIntoView({ block: "nearest" });
}

function choose(p) { closeSug(); qEl.blur(); go(p.id); }

qEl.addEventListener("input", () => { clearTimeout(sugTimer); sugTimer = setTimeout(updateSug, STATIC ? 80 : 150); });
qEl.addEventListener("focus", () => { if (qEl.value.trim().length >= 2) updateSug(); });
qEl.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); if ($("#sug").hidden) updateSug(); else setActive(sugActive + 1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); setActive(sugActive - 1); }
  else if (e.key === "Enter") { e.preventDefault(); const p = sugItems[sugActive >= 0 ? sugActive : 0]; if (p && !$("#sug").hidden) choose(p); }
  else if (e.key === "Escape") closeSug();
});
$("#sug").addEventListener("mousedown", (e) => e.preventDefault());   // zadrži fokus u polju
$("#sug").addEventListener("click", (e) => {
  const opt = e.target.closest("[data-facet]");
  if (opt) {
    const k = opt.dataset.facet, v = opt.dataset.val;
    sugFilters[k] = sugFilters[k] === v ? null : v;
    return renderSug();
  }
  if (e.target.closest("[data-clear]")) { sugFilters = {}; return renderSug(); }
  const add = e.target.closest("[data-add]");
  if (add) { addToBasket(sugItems[Number(add.dataset.add)]); return; }
  const li = e.target.closest("li[data-i]");
  if (li) choose(sugItems[Number(li.dataset.i)]);
});
// composedPath: kliknuti gumb (npr. opcija za sužavanje) može već biti zamijenjen novim HTML-om
document.addEventListener("click", (e) => {
  if (!e.composedPath().some((el) => el.classList?.contains("search") || el.id === "pick")) closeSug();
});
$("#search-form").addEventListener("submit", (e) => e.preventDefault());

// ===================================================================== views
// ---- instalacija kao aplikacija (PWA)
let installEvt = null;
const standalone = () => window.matchMedia?.("(display-mode: standalone)").matches || navigator.standalone;
const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installEvt = e; showInstall(); });
window.addEventListener("appinstalled", () => { installEvt = null; showInstall(); toast("Aplikacija je instalirana"); });

function installHTML() {
  if (!STATIC?.pwa || standalone()) return "";
  if (installEvt) return `<div class="install"><div><strong>Instaliraj aplikaciju</strong>
      <span class="muted small">Ikona na početnom zaslonu, otvara se kao prava aplikacija.</span></div>
      <button class="primary sm" id="install-btn">Instaliraj</button></div>`;
  if (isIOS) return `<div class="install"><div><strong>Dodaj na početni zaslon</strong>
      <span class="muted small">U Safariju dodirni <b>Dijeli</b> (kvadrat sa strelicom) pa <b>Dodaj na početni zaslon</b>.</span></div></div>`;
  return "";
}
function showInstall() {
  const box = $("#install-slot");
  if (!box) return;
  box.innerHTML = installHTML();
  $("#install-btn")?.addEventListener("click", async () => {
    if (!installEvt) return;
    installEvt.prompt();
    await installEvt.userChoice.catch(() => null);
    installEvt = null; showInstall();
  });
}
if (STATIC?.pwa && "serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
}

let CATS = null;
async function categories() { if (!CATS) CATS = await data.categories(); return CATS; }

async function renderHome(meta) {
  view.innerHTML = `
    <section class="hero">
      <h1>Gdje je najjeftinije?</h1>
      <p class="muted">Upiši proizvod ili barkod gore, ili ga pronađi po kategorijama kao u katalogu.</p>
    </section>
    <div id="install-slot"></div>
    <div id="cats" class="cat-grid" aria-label="Kategorije"><p class="muted">Učitavam kategorije…</p></div>
    ${meta?.updated ? `<p class="muted small">Cijene ažurirane: ${esc(fmtDate(meta.updated))}</p>` : ""}`;
  showInstall();
  let cats;
  try { cats = await categories(); } catch { $("#cats").innerHTML = ""; return; }
  if (!$("#cats")) return;
  $("#cats").innerHTML = cats.filter((c) => c.count).map((c) => `
    <button class="cat-tile" data-cat="${c.code}">
      <span class="ct-name">${esc(c.name)}</span>
      <span class="ct-count">${c.count.toLocaleString("hr-HR")} proizvoda</span>
    </button>`).join("");
}

async function renderCategory(code) {
  const cats = await categories();
  const c = cats.find((x) => x.code === code);
  if (!c) return renderHome(META);
  view.innerHTML = `
    <button class="back-cat" data-go="home">← Sve kategorije</button>
    <h1 class="cat-title">${esc(c.name)}</h1>
    <div class="cat-grid">
      ${c.subs.filter((sc) => sc.count).map((sc) => `
        <button class="cat-tile sub" data-cat="${sc.code}">
          <span class="ct-name">${esc(sc.name)}</span>
          <span class="ct-count">${sc.count.toLocaleString("hr-HR")} proizvoda</span>
        </button>`).join("")}
      <button class="cat-tile all" data-cat="${c.code}" data-all="1">
        <span class="ct-name">Sve iz kategorije</span>
        <span class="ct-count">${c.count.toLocaleString("hr-HR")} proizvoda</span>
      </button>
    </div>`;
}

// popis proizvoda u (pot)kategoriji, s istim pitanjima za sužavanje kao pretraga
let listAll = [], listFilters = {}, listShown = 0, listItems = [];
const LIST_PAGE = 40;

async function renderCatList(code) {
  const cats = await categories();
  const [main] = code.split("/");
  const c = cats.find((x) => x.code === main);
  const sub = c?.subs.find((x) => x.code === code);
  view.innerHTML = `
    <button class="back-cat" data-catback="${main}">← ${esc(c ? c.name : "Kategorije")}</button>
    <h1 class="cat-title">${esc(sub ? sub.name : c ? `${c.name} – sve` : "")}</h1>
    <p class="muted">Učitavam…</p>`;
  listAll = await data.browse(code);
  listFilters = {}; listShown = LIST_PAGE;
  view.innerHTML = `
    <button class="back-cat" data-catback="${main}">← ${esc(c ? c.name : "Kategorije")}</button>
    <h1 class="cat-title">${esc(sub ? sub.name : c ? `${c.name} – sve` : "")}</h1>
    <div id="cat-refine" class="refine page"></div>
    <ul id="cat-list" class="cat-list"></ul>
    <p id="cat-more"></p>`;
  drawCatList(true);
}

function drawCatList(first = false) {
  const filtered = listAll.filter((p) => matches(p, listFilters));
  listItems = filtered.slice(0, listShown);
  const facets = listAll.length > 1 ? buildFacets(listAll, listFilters) : [];
  const ref = $("#cat-refine");
  keepScroll(ref, () => { ref.innerHTML = facets.length ? refineHTML(facets, listFilters, String(filtered.length)) : ""; });
  ref.hidden = !facets.length;
  $("#cat-list").innerHTML = listItems.map((p, i) => rowHTML(p, i, "cl")).join("")
    || '<li class="none muted">Nema proizvoda uz odabrane uvjete.</li>';
  $("#cat-more").innerHTML = filtered.length > listShown
    ? `<button class="ghost" id="cat-more-btn">Prikaži još (${filtered.length - listShown})</button>` : "";
  markAdded($("#cat-list"));
}

view.addEventListener("click", (e) => {
  const t = e.target.closest("[data-cat]");
  if (t) return go({ cat: t.dataset.cat, list: t.dataset.cat.includes("/") || !!t.dataset.all });
  const back = e.target.closest("[data-catback]");
  if (back) return go({ cat: back.dataset.catback, list: false });
  if (!$("#cat-list")) return;
  const opt = e.target.closest("[data-facet]");
  if (opt) {
    const k = opt.dataset.facet, v = opt.dataset.val;
    listFilters[k] = listFilters[k] === v ? null : v;
    listShown = LIST_PAGE; return drawCatList();
  }
  if (e.target.closest("[data-clear]")) { listFilters = {}; return drawCatList(); }
  if (e.target.id === "cat-more-btn") { listShown += LIST_PAGE * 2; return drawCatList(); }
  const add = e.target.closest("#cat-list [data-add]");
  if (add) return addToBasket(listItems[Number(add.dataset.add)]);
  const li = e.target.closest("#cat-list li[data-i]");
  if (li) go(listItems[Number(li.dataset.i)].id);
});

const fmtShort = (d) => new Date(d + "T12:00:00Z").toLocaleDateString("hr-HR", { day: "numeric", month: "numeric", year: "numeric" });

function dealText(c) {
  const label = c.deal_label ? c.deal_label[0].toUpperCase() + c.deal_label.slice(1) : "Akcija";
  const parts = [label];
  if (c.valid_until) parts.push(`vrijedi do ${fmtShort(c.valid_until)}`);
  if (c.deal_stores < c.stores) parts.push(`u ${c.deal_stores} od ${stores(c.stores)}`);
  return parts.join(" · ");
}

const CARD_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5.5" width="18" height="13" rx="2"/><path d="M3 10h18"/></svg>';

// niža cijena uz karticu vjernosti (MultiPlus, Kaufland Card, Lidl Plus...) – drugom bojom
function cardLine(price, name, chain) {
  if (price == null) return "";
  return `<div class="card-line small">${CARD_ICON}<span><strong>${eur(price)}</strong> uz ${esc(name || "karticu vjernosti")}${chain ? ` (${esc(chain)})` : ""}</span></div>`;
}

const fmtDate = (d) => new Date(d).toLocaleDateString("hr-HR", { day: "numeric", month: "long", year: "numeric" });

async function renderProduct(id) {
  view.innerHTML = `<p class="muted">Učitavam…</p>`;
  let s;
  try { s = await data.summary(id); } catch (e) { view.innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  const b = s.chains[0]?.price;
  const inList = basket.find(s.id);
  const tied = new Set(tiedChains(s).map((c) => c.chain));
  view.innerHTML = `
    <div class="p-head">
      <div>
        <h1>${esc(s.name)}</h1>
        <p class="muted">${esc([s.brand, s.qty, s.ean && `EAN ${s.ean}`].filter(Boolean).join(" · "))}</p>
      </div>
      <button id="add-btn" class="primary">${inList ? `Na popisu (${inList.qty}) · dodaj još` : "+ Dodaj na popis"}</button>
    </div>
    <h2>Usporedba po trgovcima</h2>
    ${s.chains.length ? `<div class="chains">${s.chains.map((c, i) => {
      const diff = b ? ((c.price - b) / b) * 100 : 0;
      const low = tied.has(c.chain);
      const mine = inList && inList.chain === c.chain;
      const extra = i >= CARDS_SHOWN && !low && !mine;
      return `<div class="chain-card ${low ? "best" : ""}${extra ? " extra" : ""}"${extra ? " hidden" : ""}>
        <div class="cname"><span>${esc(c.chain_name)}</span>
          ${low ? '<span class="badge">najjeftinije</span>' : `<span class="muted small">+${diff.toFixed(0)} %</span>`}</div>
        <div><span class="big">${eur(c.price)}</span>
          ${c.regular > c.price ? `<span class="old">${eur(c.regular)}</span>` : ""}</div>
        <div class="small muted">${c.deal_stores ? `<span class="badge deal">akcija</span> ` : ""}
          ${c.min < c.max ? `${eur(c.min)} – ${eur(c.max)} · ` : ""}${stores(c.stores)}</div>
        ${c.deal_stores ? `<div class="deal-line small">${esc(dealText(c))}</div>` : ""}
        ${cardLine(c.card_price, c.card_name)}
        <button class="ghost sm add-to${mine ? " on" : ""}" data-add-chain="${esc(c.chain)}">
          ${mine ? `✓ Na popisu (${inList.qty}) · još` : `+ Na popis za ${esc(c.chain_name)}`}</button>
      </div>`; }).join("")}</div>
      ${s.chains.length > CARDS_SHOWN ? `<button class="ghost show-all" id="show-all">Prikaži sve trgovce (${s.chains.length})</button>` : ""}
      ${tied.size > 1 ? `<p class="small tie-note">Ista najniža cijena u ${shopsN(tied.size)} – odaberi u koju želiš staviti proizvod.</p>` : ""}
      <p class="muted small">Prikazana je uobičajena cijena (medijan) u poslovnicama trgovca, uključujući akcije.</p>`
    : `<div class="empty">Ovaj proizvod trenutno nema cijenu ni u jednoj trgovini.</div>`}
    ${s.history.some((h) => h.points.length > 1) ? `
      <h2>Povijest cijene</h2>
      <div class="panel-box"><div class="chart-box"><canvas id="chart" aria-label="Graf povijesti cijene"></canvas></div>
        ${s.history.length > CHART_SHOWN ? `<p class="small muted">Prikazano je ${CHART_SHOWN} najjeftinijih trgovaca – ostale uključi dodirom na naziv u legendi.</p>` : ""}</div>` : ""}`;
  $("#add-btn").addEventListener("click", async () => { await addToBasket(s); renderProduct(s.id); });
  $("#show-all")?.addEventListener("click", (e) => {
    view.querySelectorAll(".chain-card.extra").forEach((el) => { el.hidden = false; });
    e.currentTarget.remove();
  });
  view.querySelectorAll("[data-add-chain]").forEach((btn) => btn.addEventListener("click", () => {
    const c = s.chains.find((x) => x.chain === btn.dataset.addChain);
    basket.add(s, 1, c.chain, c.chain_name);
    renderProduct(s.id);
  }));
  drawHistory(s.history, s.chains);
}

const PALETTE = ["#0b6e4f", "#d1495b", "#2e86de", "#c98a12", "#7b5ea7", "#00798c", "#8d6a4f", "#e07a5f",
  "#3d5a80", "#9c6644", "#6a994e", "#bc4749", "#5e60ce", "#f4a259", "#2a9d8f", "#b5838d"];
const colorFor = (i) => PALETTE[i] || `hsl(${(i * 137.5) % 360} 55% 45%)`;
const CHART_SHOWN = 6;
const CARDS_SHOWN = 8;   // kartice trgovaca na stranici proizvoda (najjeftiniji), ostali na "Prikaži sve"   // s puno trgovaca graf prikazuje najjeftinije, ostali su u legendi

function drawHistory(series, current = []) {
  const canvas = $("#chart");
  if (!canvas || !window.Chart) return;
  const shown = new Set(current.slice().sort((a, b) => a.price - b.price).slice(0, CHART_SHOWN).map((c) => c.chain));
  if (!shown.size) series.slice(0, CHART_SHOWN).forEach((s) => shown.add(s.chain));
  const known = [...new Set(series.flatMap((s) => s.points.map((p) => p.day)))].sort();
  if (!known.length) return;
  // svaki dan u rasponu, da vremenska os bude ravnomjerna
  const labels = [];
  for (let d = new Date(known[0] + "T12:00:00Z"), end = new Date(known[known.length - 1] + "T12:00:00Z");
       d <= end; d.setUTCDate(d.getUTCDate() + 1)) labels.push(d.toISOString().slice(0, 10));
  const css = getComputedStyle(document.documentElement);
  const text = css.getPropertyValue("--muted").trim();
  const grid = css.getPropertyValue("--line").trim();
  if (chart) chart.destroy();
  chart = new Chart(canvas, {
    type: "line",
    data: {
      labels: labels.map((d) => new Date(d).toLocaleDateString("hr-HR", { day: "numeric", month: "numeric" })),
      datasets: series.map((s, i) => {
        const m = Object.fromEntries(s.points.map((p) => [p.day, p.price]));
        const first = s.points[0]?.day, last = s.points[s.points.length - 1]?.day;
        // cijena vrijedi do sljedeće promjene (statični izvoz sprema samo promjene)
        let prev = null;
        const vals = labels.map((d) => {
          if (d in m) prev = m[d];
          return d >= first && d <= last ? prev : null;
        });
        const color = colorFor(i);
        return { label: s.chain_name, data: vals, borderColor: color, hidden: !shown.has(s.chain),
          backgroundColor: color, stepped: true, pointRadius: 0, pointHitRadius: 8, borderWidth: 2 };
      }),
    },
    options: {
      maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { color: text, maxTicksLimit: 8 }, grid: { color: grid } },
        y: { ticks: { color: text, callback: (v) => eur(v) }, grid: { color: grid } },
      },
      plugins: {
        legend: { labels: { color: text, boxWidth: 12 } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${eur(c.parsed.y)}` } },
      },
    },
  });
}

// ===================================================================== router
// Bez hash putanja (rade i u ugrađenom pregledniku): stog u memoriji.
let current = null; const stack = [];
let META = null;
function go(id, push = true) { if (push) stack.push(current); current = id; window.scrollTo(0, 0); route(); }
function route() {
  // "← natrag" samo na stranici proizvoda; kategorije imaju svoj gumb za povratak
  $("#back").hidden = typeof current !== "number";
  if (current == null) return renderHome(META);
  if (typeof current === "object") return current.list ? renderCatList(current.cat) : renderCategory(current.cat);
  return renderProduct(current);
}

document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-go]");
  if (!a) return;
  e.preventDefault();
  const g = a.dataset.go;
  if (g === "home") { stack.length = 0; current = null; return route(); }
  if (g === "back") { current = stack.pop() ?? null; return route(); }
  if (window.innerWidth < 640) openPanel(false);
  go(Number(g));
});
$("#fab").addEventListener("click", () => openPanel($("#panel").hidden));
$("#panel-close").addEventListener("click", () => openPanel(false));
$("#panel").addEventListener("change", (e) => {
  const sel = e.target.closest("[data-chain-for]");
  if (sel) basket.setChain(sel.dataset.chainFor, sel.value);
});
$("#panel").addEventListener("click", (e) => {
  const t = e.target.closest("[data-tab]");
  if (t) { panelTab = t.dataset.tab; store.set("cijene.tab", panelTab); return renderPanel(); }
  const inc = e.target.closest("[data-inc]"), dec = e.target.closest("[data-dec]");
  if (inc) basket.setQty(inc.dataset.inc, basket.find(inc.dataset.inc).qty + 1);
  if (dec) basket.setQty(dec.dataset.dec, basket.find(dec.dataset.dec).qty - 1);
  if (e.target.id === "clear") basket.clear();
  const rm = e.target.closest("[data-rm]");
  if (rm) basket.remove(rm.dataset.rm);
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#panel").hidden) openPanel(false); });

(async function init() {
  // uži ekran (logo "Cijene BoLaNI" zauzme više mjesta): kraći natpis u polju za pretragu
  if (window.innerWidth < 420) qEl.placeholder = window.innerWidth < 380 ? "Traži…" : "Proizvod ili barkod";
  renderFab();
  try { META = await data.meta(); CONFIG = META; FEATURE_LABELS = META.features || {}; } catch { META = null; }
  if (META?.updated) $("#updated").textContent = `Cijene od ${fmtDate(META.updated)}`;
  route();
})();
