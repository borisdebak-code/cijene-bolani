"use strict";

const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const cityEl = $("#city");
const qEl = $("#q");
let chart = null;

const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* privatni način */ } },
};

const eur = (v) => v == null ? "–" :
  Number(v).toLocaleString("hr-HR", { style: "currency", currency: "EUR" });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const qty = (p) => {
  if (p.quantity == null) return "";
  const q = Number(p.quantity);
  if (p.unit === "g" && q >= 1000) return `${q / 1000} kg`;
  if (p.unit === "ml" && q >= 1000) return `${q / 1000} l`;
  return `${q} ${p.unit ?? ""}`.trim();
};

async function api(path, params = {}) {
  const url = new URL(path, location.origin);
  for (const [k, v] of Object.entries(params)) if (v !== "" && v != null) url.searchParams.set(k, v);
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

function setLoading() { view.innerHTML = `<p class="muted">Učitavam…</p>`; }
function showError(e) { view.innerHTML = `<p class="err">Greška: ${esc(e.message)}</p>`; }

// ------------------------------------------------------------------ home
function renderHome() {
  const ex = ["mlijeko", "nutella", "kruh", "coca cola", "banane"];
  view.innerHTML = `
    <section class="hero">
      <h1>Gdje je najjeftinije?</h1>
      <p class="muted">Usporedi cijene iz službenih dnevnih cjenika trgovaca, po gradu i poslovnici.</p>
      <div class="chips">${ex.map((e) => `<a href="#/search?q=${encodeURIComponent(e)}">${e}</a>`).join("")}</div>
    </section>`;
}

// ------------------------------------------------------------------ search
async function renderSearch(q) {
  qEl.value = q;
  setLoading();
  try {
    const res = await api("/api/search", { q, city: cityEl.value });
    if (!res.length) {
      view.innerHTML = `<div class="empty">Nema rezultata za „${esc(q)}”.</div>`;
      return;
    }
    view.innerHTML = `
      <p class="muted small">${res.length} rezultata${cityEl.value ? ` · ${esc(cityEl.value)}` : ""}</p>
      <ul class="results">${res.map((p) => `
        <li><a class="result" href="#/p/${p.id}">
          <div>
            <div class="name">${esc(p.name)} ${p.on_sale ? '<span class="badge deal">akcija</span>' : ""}</div>
            <div class="muted small">${esc([p.brand, qty(p), p.ean].filter(Boolean).join(" · "))}</div>
          </div>
          <div class="price">
            ${p.min_price == null ? '<span class="muted small">nema u ponudi</span>' :
              `${eur(p.min_price)}${p.max_price > p.min_price ? `<span class="muted small"> – ${eur(p.max_price)}</span>` : ""}`}
            <div class="muted small">${p.chains || 0} trg.</div>
          </div>
        </a></li>`).join("")}
      </ul>`;
  } catch (e) { showError(e); }
}

// ------------------------------------------------------------------ product
async function renderProduct(id) {
  setLoading();
  try {
    const city = cityEl.value;
    const [p, prices] = await Promise.all([
      api(`/api/products/${id}`), api(`/api/products/${id}/prices`, { city }),
    ]);
    const best = prices.chains[0]?.min_price;
    view.innerHTML = `
      <a href="javascript:history.back()" class="small">← natrag</a>
      <h1>${esc(p.name)}</h1>
      <p class="muted">${esc([p.brand, qty(p), p.ean && `EAN ${p.ean}`, p.category].filter(Boolean).join(" · "))}</p>

      <h2>Usporedba po trgovcima${city ? ` – ${esc(city)}` : ""}</h2>
      ${prices.chains.length ? `<div class="chains">${prices.chains.map((c, i) => {
        const b = c.best_store;
        const diff = best ? ((c.min_price - best) / best) * 100 : 0;
        return `<div class="chain-card ${i === 0 ? "best" : ""}">
          <div class="cname"><span>${esc(c.chain_name)}</span>
            ${i === 0 ? '<span class="badge">najjeftinije</span>' : `<span class="muted small">${diff < 0.5 ? "ista cijena" : `+${diff.toFixed(0)}%`}</span>`}</div>
          <div><span class="big">${eur(c.min_price)}</span>
            ${b.special_price != null ? `<span class="old">${eur(b.regular_price)}</span>` : ""}</div>
          <div class="small muted">${c.is_deal ? '<span class="badge deal">akcija</span> ' : ""}
            ${c.stores > 1 ? `do ${eur(c.max_price)} · ${c.stores} poslovnica` : "1 poslovnica"}</div>
          <div class="small muted">${esc([b.address, b.city].filter(Boolean).join(", "))}</div>
        </div>`;
      }).join("")}</div>` : `<div class="empty">Nema trenutnih cijena${city ? " u ovom gradu" : ""}.</div>`}

      <h2>Povijest cijene</h2>
      <div class="panel">
        <div class="toolbar" id="range">
          ${[30, 90, 365].map((d) => `<button class="ghost" data-days="${d}" aria-pressed="${d === 90}">${d} dana</button>`).join("")}
        </div>
        <div class="chart-box"><canvas id="chart" aria-label="Graf povijesti cijene"></canvas></div>
        <p class="small muted">Najniža cijena po trgovcu po danu (uključujući akcije)${city ? `, ${esc(city)}` : ""}.</p>
      </div>

      <h2>Sve poslovnice <span class="muted small">(${prices.stores.length})</span></h2>
      <div class="table-wrap"><table>
        <thead><tr><th>Trgovac</th><th>Poslovnica</th><th class="num">Cijena</th>
          <th class="num hide-sm">Redovna</th><th class="num hide-sm">Najniža 30 d</th>
          <th class="num hide-sm">Po jed. mj.</th></tr></thead>
        <tbody id="stores-body"></tbody>
      </table></div>
      <p id="more-wrap"></p>`;

    renderStores(prices.stores, 25);
    $("#range").addEventListener("click", (ev) => {
      const b = ev.target.closest("button"); if (!b) return;
      $("#range").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
      drawHistory(id, Number(b.dataset.days));
    });
    drawHistory(id, 90);
  } catch (e) { showError(e); }
}

function renderStores(rows, limit) {
  $("#stores-body").innerHTML = rows.slice(0, limit).map((s) => `
    <tr>
      <td>${esc(s.chain_name)}</td>
      <td class="wrapcell">${esc([s.address, s.city].filter(Boolean).join(", "))}</td>
      <td class="num"><strong>${eur(s.price)}</strong> ${s.is_deal ? '<span class="badge deal">akcija</span>' : ""}</td>
      <td class="num hide-sm">${eur(s.regular_price)}</td>
      <td class="num hide-sm">${eur(s.lowest_30d)}</td>
      <td class="num hide-sm">${eur(s.unit_price)}</td>
    </tr>`).join("");
  const more = $("#more-wrap");
  if (rows.length > limit) {
    more.innerHTML = `<button class="ghost">Prikaži sve (${rows.length})</button>`;
    more.firstChild.onclick = () => renderStores(rows, rows.length);
  } else more.innerHTML = "";
}

const PALETTE = ["#0b6e4f", "#d1495b", "#2e86de", "#edae49", "#7b5ea7", "#00798c", "#8d6a4f", "#e07a5f"];

async function drawHistory(id, days) {
  const series = await api(`/api/products/${id}/history`, { days, city: cityEl.value });
  const canvas = $("#chart");
  if (!canvas || !window.Chart) return;
  const labels = [...new Set(series.flatMap((s) => s.points.map((p) => p.day)))].sort();
  const css = getComputedStyle(document.documentElement);
  const text = css.getPropertyValue("--muted").trim();
  const grid = css.getPropertyValue("--line").trim();
  if (chart) chart.destroy();
  chart = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: series.map((s, i) => {
        const m = Object.fromEntries(s.points.map((p) => [p.day, p.min]));
        const color = PALETTE[i % PALETTE.length];
        return { label: s.chain_name, data: labels.map((d) => m[d] ?? null), borderColor: color,
          backgroundColor: color, stepped: true, pointRadius: 0, pointHitRadius: 8,
          borderWidth: 2, spanGaps: false };
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

// ------------------------------------------------------------------ status
async function renderStatus() {
  setLoading();
  try {
    const st = await api("/api/status");
    const badge = (s) => !s ? '<span class="badge warn">nije pokrenut</span>' :
      `<span class="badge ${{ ok: "", partial: "warn", failed: "bad", running: "warn" }[s] ?? "warn"}">${s}</span>`;
    view.innerHTML = `
      <h1>Stanje preuzimanja</h1>
      <p class="muted">Zadnji pokušaj po adapteru. Ako trgovac promijeni format cjenika, ovdje se vidi koji je adapter pukao.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Adapter</th><th>Status</th><th>Dan</th><th class="num">Poslovnice</th>
          <th class="num">Redovi</th><th class="num">Neuspjele dat.</th><th>Greška</th></tr></thead>
        <tbody>${st.adapters.map((a) => { const r = a.last_run; return `<tr>
          <td><strong>${esc(a.name)}</strong> ${a.scheduled ? "" : '<span class="muted small">(ručno)</span>'}</td>
          <td>${badge(r?.status)}</td><td>${r?.day ?? "–"}</td>
          <td class="num">${r?.stores ?? "–"}</td><td class="num">${r?.rows_total ?? "–"}</td>
          <td class="num">${r ? `${r.files_failed}/${r.files_total}` : "–"}</td>
          <td class="wrapcell small">${esc(r?.error ?? "")}</td></tr>`; }).join("")}
        </tbody></table></div>
      <h2>Zadnje greške</h2>
      ${st.recent_errors.length ? `<div class="table-wrap"><table>
        <thead><tr><th>Adapter</th><th>Vrsta</th><th>Datoteka</th><th>Poruka</th></tr></thead>
        <tbody>${st.recent_errors.map((e) => `<tr><td>${esc(e.chain_code)}</td><td>${esc(e.kind)}</td>
          <td class="wrapcell small">${esc(e.file ?? "")}</td><td class="wrapcell small">${esc(e.message)}</td></tr>`).join("")}
        </tbody></table></div>` : '<p class="muted">Nema grešaka.</p>'}`;
  } catch (e) { showError(e); }
}

// ------------------------------------------------------------------ router
function route() {
  const h = location.hash.slice(1) || "/";
  const [path, qs] = h.split("?");
  const params = new URLSearchParams(qs);
  if (path === "/search" && params.get("q")) return renderSearch(params.get("q"));
  const m = path.match(/^\/p\/(\d+)$/);
  if (m) return renderProduct(m[1]);
  if (path === "/status") return renderStatus();
  return renderHome();
}

$("#search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = qEl.value.trim();
  if (q.length >= 2) location.hash = `#/search?q=${encodeURIComponent(q)}`;
});
cityEl.addEventListener("change", () => { store.set("city", cityEl.value); route(); });
window.addEventListener("hashchange", route);

(async function init() {
  try {
    const cities = await api("/api/cities");
    for (const c of cities) cityEl.add(new Option(`${c.city} (${c.stores})`, c.city));
    cityEl.value = store.get("city", "");
  } catch { /* nastavi bez gradova */ }
  route();
})();
