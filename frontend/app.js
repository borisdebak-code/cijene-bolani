"use strict";

/* Cijene HR – javno sučelje.
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
  let restLoaded = false;
  const shards = new Map();

  const expand = ([id, name, brand, qty, min, chains, extra]) => ({
    id, name, brand, qty, min_price: min, chains,
    hay: " " + norm(`${name} ${brand ?? ""} ${extra ?? ""}`) + " ",
    nm: " " + norm(name) + " ",
  });

  function loadIndex() {
    if (!indexP) {
      indexP = getJSON(STATIC.base + "index.json").then((ix) => {
        ix.items = ix.items.map(expand);
        // ostatak (proizvodi samo kod jednog trgovca) dolazi u pozadini
        getJSON(STATIC.base + ix.rest).then((rest) => {
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
      if (STATIC) { const ix = await loadIndex(); return { public: true, updated: ix.updated, chains: ix.chains }; }
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
    chains: s.c.map(([chain, price, min, max, regular, st, deals]) => ({
      chain, chain_name: chainNames[chain] || chain, price, min, max, regular, stores: st, deal_stores: deals,
    })),
    history: Object.entries(s.h || {}).map(([chain, pts]) => ({
      chain, chain_name: chainNames[chain] || chain, points: pts.map(([day, price]) => ({ day, price })),
    })),
  };
}

// ===================================================================== basket
const basket = {
  items: store.get("cijene.basket", []),   // [{id, qty, name, label}]
  save() { store.set("cijene.basket", this.items); renderFab(); if (!$("#panel").hidden) renderPanel(); },
  find(id) { return this.items.find((i) => i.id === Number(id)); },
  add(p, n = 1) {
    const it = this.find(p.id);
    if (it) it.qty += n;
    else this.items.push({ id: Number(p.id), qty: n, name: p.name, label: [p.brand, p.qty].filter(Boolean).join(" · ") });
    this.save();
    toast(`Dodano na popis: ${p.name}`);
  },
  setQty(id, qty) {
    const it = this.find(id); if (!it) return;
    if (qty <= 0) this.items = this.items.filter((i) => i !== it); else it.qty = qty;
    this.save();
  },
  clear() { this.items = []; this.save(); },
};

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, 2200);
}

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
    const b = best(r.s);
    r.best = b;
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
          <span class="pr">${eur(r.best.price * r.it.qty)}</span></li>`).join("")}
        </ul>
        <button class="ghost sm" data-copy="${esc(g.chain)}">Kopiraj popis za ${esc(g.name)}</button>
      </section>`).join("") + missing(plan);
  } else if (panelTab === "items") {
    body.innerHTML = `<ul class="items">${plan.rows.map((r) => `<li>
        <div class="it-main">
          <a href="#" data-go="${r.it.id}" class="nm">${esc(r.it.name)}</a>
          <div class="muted small">${esc(r.it.label || "")}</div>
          <div class="small">${r.best ? `Najjeftinije: <strong>${esc(r.best.chain_name)}</strong> ${eur(r.best.price)}` : '<span class="muted">Trenutno nema cijene</span>'}</div>
        </div>
        <div class="stepper" role="group" aria-label="Količina">
          <button class="ghost sm" data-dec="${r.it.id}" aria-label="Manje">−</button>
          <span>${r.it.qty}</span>
          <button class="ghost sm" data-inc="${r.it.id}" aria-label="Više">+</button>
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
  $("#panel-foot").innerHTML = `
    <div><span class="muted small">Ukupno, svaki proizvod gdje je najjeftiniji</span><strong>${eur(plan.total)}</strong></div>
    ${saving && saving.total > plan.total + 0.005 ? `<div class="small muted">U jednoj trgovini najmanje ${eur(saving.total)} (${esc(saving.name)})</div>` : ""}`;

  body.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => {
    const g = plan.groups.find((x) => x.chain === b.dataset.copy);
    const txt = `${g.name} – popis za kupnju\n` + g.items.map((r) =>
      `☐ ${r.it.qty}× ${r.it.name}${r.it.label ? ` (${r.it.label})` : ""} – ${eur(r.best.price * r.it.qty)}`).join("\n") +
      `\nUkupno: ${eur(g.total)}`;
    navigator.clipboard.writeText(txt).then(() => toast("Popis kopiran"), () => {
      const ta = document.createElement("textarea"); ta.value = txt; ta.className = "copy-fallback";
      b.after(ta); ta.select(); toast("Označeno – kopiraj ručno");
    });
  }));
}

function missing(plan) {
  const m = plan.rows.filter((r) => !r.best);
  return m.length ? `<section class="shop muted"><header><h3>Bez cijene</h3></header><ul>${m.map((r) =>
    `<li><span class="q">${r.it.qty}×</span><span class="nm">${esc(r.it.name)}</span></li>`).join("")}</ul></section>` : "";
}

function openPanel(open) {
  $("#panel").hidden = !open;
  $("#fab").setAttribute("aria-expanded", open);
  if (open) renderPanel();
}

// ===================================================================== search dropdown
let sugTimer = null, sugSeq = 0, sugItems = [], sugActive = -1;

function closeSug() { $("#sug").hidden = true; sugActive = -1; qEl.setAttribute("aria-expanded", "false"); }

async function updateSug() {
  const q = qEl.value.trim();
  if (q.length < 2) return closeSug();
  const seq = ++sugSeq;
  let items;
  try { items = await data.suggest(q, 8); } catch { items = []; }
  if (seq !== sugSeq) return;
  sugItems = items; sugActive = -1;
  const box = $("#sug");
  box.innerHTML = items.length ? items.map((p, i) => `
    <li role="option" id="sug-${i}" data-i="${i}">
      <div class="s-main"><div class="s-name">${esc(p.name)}</div>
        <div class="muted small">${esc([p.brand, p.qty].filter(Boolean).join(" · "))}</div></div>
      <div class="s-price"><strong>${p.min_price != null ? "od " + eur(p.min_price) : ""}</strong>
        <div class="muted small">${shopsN(p.chains)}</div></div>
      <button class="add" data-add="${i}" aria-label="Dodaj na popis: ${esc(p.name)}" title="Dodaj na popis">+</button>
    </li>`).join("") : `<li class="none muted">Nema proizvoda za „${esc(q)}”</li>`;
  box.hidden = false;
  qEl.setAttribute("aria-expanded", "true");
}

function setActive(i) {
  const lis = [...$("#sug").querySelectorAll("li[data-i]")];
  if (!lis.length) return;
  sugActive = (i + lis.length) % lis.length;
  lis.forEach((li, k) => li.classList.toggle("active", k === sugActive));
  qEl.setAttribute("aria-activedescendant", `sug-${sugActive}`);
  lis[sugActive].scrollIntoView({ block: "nearest" });
}

function choose(p) { closeSug(); qEl.blur(); go(p.id); }

qEl.addEventListener("input", () => { clearTimeout(sugTimer); sugTimer = setTimeout(updateSug, STATIC ? 60 : 150); });
qEl.addEventListener("focus", () => { if (qEl.value.trim().length >= 2) updateSug(); });
qEl.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); if ($("#sug").hidden) updateSug(); else setActive(sugActive + 1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); setActive(sugActive - 1); }
  else if (e.key === "Enter") { e.preventDefault(); const p = sugItems[sugActive >= 0 ? sugActive : 0]; if (p && !$("#sug").hidden) choose(p); }
  else if (e.key === "Escape") closeSug();
});
$("#sug").addEventListener("mousedown", (e) => e.preventDefault());   // zadrži fokus u polju
$("#sug").addEventListener("click", (e) => {
  const add = e.target.closest("[data-add]");
  if (add) { basket.add(sugItems[Number(add.dataset.add)]); return; }
  const li = e.target.closest("li[data-i]");
  if (li) choose(sugItems[Number(li.dataset.i)]);
});
document.addEventListener("click", (e) => { if (!e.target.closest(".search")) closeSug(); });
$("#search-form").addEventListener("submit", (e) => e.preventDefault());

// ===================================================================== views
function renderHome(meta) {
  const ex = ["mlijeko", "nutella", "kava", "jaja", "coca cola", "banane"];
  view.innerHTML = `
    <section class="hero">
      <h1>Gdje je najjeftinije?</h1>
      <p class="muted">Upiši proizvod ili barkod gore, odaberi ga s popisa i usporedi cijene u trgovinama.
        Dodaj proizvode na popis za kupnju i vidi gdje se koji isplati kupiti.</p>
      <div class="chips">${ex.map((e) => `<button class="chip" data-q="${esc(e)}">${esc(e)}</button>`).join("")}</div>
      ${meta?.updated ? `<p class="muted small">Cijene ažurirane: ${esc(fmtDate(meta.updated))}</p>` : ""}
    </section>`;
  view.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => {
    qEl.value = b.dataset.q; qEl.focus(); updateSug();
  }));
}

const fmtDate = (d) => new Date(d).toLocaleDateString("hr-HR", { day: "numeric", month: "long", year: "numeric" });

async function renderProduct(id) {
  view.innerHTML = `<p class="muted">Učitavam…</p>`;
  let s;
  try { s = await data.summary(id); } catch (e) { view.innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  const b = s.chains[0]?.price;
  const inList = basket.find(s.id);
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
      return `<div class="chain-card ${i === 0 ? "best" : ""}">
        <div class="cname"><span>${esc(c.chain_name)}</span>
          ${i === 0 ? '<span class="badge">najjeftinije</span>' : `<span class="muted small">${diff < 0.5 ? "ista cijena" : `+${diff.toFixed(0)} %`}</span>`}</div>
        <div><span class="big">${eur(c.price)}</span>
          ${c.regular > c.price ? `<span class="old">${eur(c.regular)}</span>` : ""}</div>
        <div class="small muted">${c.deal_stores ? `<span class="badge deal">akcija</span> ` : ""}
          ${c.min < c.max ? `${eur(c.min)} – ${eur(c.max)} · ` : ""}${stores(c.stores)}</div>
      </div>`; }).join("")}</div>
      <p class="muted small">Prikazana je uobičajena cijena (medijan) u poslovnicama trgovca, uključujući akcije.</p>`
    : `<div class="empty">Ovaj proizvod trenutno nema cijenu ni u jednoj trgovini.</div>`}
    ${s.history.some((h) => h.points.length > 1) ? `
      <h2>Povijest cijene</h2>
      <div class="panel-box"><div class="chart-box"><canvas id="chart" aria-label="Graf povijesti cijene"></canvas></div></div>` : ""}`;
  $("#add-btn").addEventListener("click", () => { basket.add(s); renderProduct(s.id); });
  drawHistory(s.history);
}

const PALETTE = ["#0b6e4f", "#d1495b", "#2e86de", "#c98a12", "#7b5ea7", "#00798c", "#8d6a4f", "#e07a5f"];

function drawHistory(series) {
  const canvas = $("#chart");
  if (!canvas || !window.Chart) return;
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
        const color = PALETTE[i % PALETTE.length];
        return { label: s.chain_name, data: vals, borderColor: color,
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
  $("#back").hidden = current == null;
  if (current == null) return renderHome(META);
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
$("#panel").addEventListener("click", (e) => {
  const t = e.target.closest("[data-tab]");
  if (t) { panelTab = t.dataset.tab; store.set("cijene.tab", panelTab); return renderPanel(); }
  const inc = e.target.closest("[data-inc]"), dec = e.target.closest("[data-dec]");
  if (inc) basket.setQty(inc.dataset.inc, basket.find(inc.dataset.inc).qty + 1);
  if (dec) basket.setQty(dec.dataset.dec, basket.find(dec.dataset.dec).qty - 1);
  if (e.target.id === "clear") basket.clear();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#panel").hidden) openPanel(false); });

(async function init() {
  renderFab();
  try { META = await data.meta(); CONFIG = META; } catch { META = null; }
  if (META?.updated) $("#updated").textContent = `Cijene od ${fmtDate(META.updated)}`;
  route();
})();
