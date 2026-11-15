"use strict";

const $ = (s) => document.querySelector(s);
const nf = (d = 0) => new Intl.NumberFormat("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d });
const kwh = (v, d = 0) => `${nf(d).format(v)} kWh`;
const eur = (v, d = 0) => `${nf(d).format(v)} €`;
const date = (iso) => new Date(iso).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
const dateShort = (iso) => new Date(iso).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
const dateTime = (iso) => new Date(iso).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" });
const parseNum = (s) => Number(String(s).trim().replace(/\./g, "").replace(",", "."));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch(`api/${path}`, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok && res.status !== 409) throw new Error(body.detail || `Fehler ${res.status}`);
  return { status: res.status, body };
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), 2600);
}

// ------------------------------------------------------------ Navigation
const TITLES = { home: "Übersicht", read: "Ablesen", history: "Verlauf", settings: "Einstellungen" };

function route() {
  const view = (location.hash || "#home").slice(1);
  const name = TITLES[view] ? view : "home";
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("hidden", v.id !== `view-${name}`));
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.view === name));
  $("#title").textContent = TITLES[name];
  ({ home: loadHome, read: prepareRead, history: loadHistory, settings: loadSettings })[name]();
}
window.addEventListener("hashchange", route);

// ------------------------------------------------------------ Übersicht
const ICONS = {
  good: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="m8 12 3 3 5-6"/></svg>',
  serious: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>',
  critical: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v5M12 16h.01"/></svg>',
};

async function loadHome() {
  const { body: s } = await api("summary");
  const hasData = s.readings_count >= 2;
  $("#empty").classList.toggle("hidden", hasData);
  $("#tiles").classList.toggle("hidden", !hasData);
  $("#chart-card").classList.toggle("hidden", !hasData);
  $("#hero").classList.toggle("hidden", !(hasData && s.period));

  if (s.last_reading) {
    const age = s.last_reading.age_days;
    $("#subtitle").textContent = `Letzte Ablesung ${age < 1 ? "heute" : `vor ${Math.floor(age)} ${Math.floor(age) === 1 ? "Tag" : "Tagen"}`}`;
  }
  if (!hasData) return;

  // Hero: Abrechnungsprognose
  const p = s.period;
  if (p) {
    const b = p.balance;
    const status = b >= 0 ? "good" : b > -100 ? "serious" : "critical";
    const hero = $("#hero");
    hero.classList.remove("status-good", "status-serious", "status-critical");
    hero.classList.add(`status-${status}`);
    $("#balance-icon").innerHTML = ICONS[status];
    $("#balance").textContent = eur(Math.abs(b));
    $("#balance-label").textContent = b >= 0 ? "voraussichtliches Guthaben" : "voraussichtliche Nachzahlung";
    const fmt = (v) => (v >= 0 ? `+${eur(v)}` : `−${eur(-v)}`);
    $("#balance-range").textContent = `Spanne ${fmt(p.balance_low)} bis ${fmt(p.balance_high)} je nach Trend (14 bzw. 90 Tage)`;
    $("#period-range").textContent = `${dateShort(p.start)}–${date(p.end)}`;
    $("#p-kwh").textContent = kwh(p.kwh_projected);
    $("#p-eur").textContent = eur(p.eur_projected);
    $("#p-inst").textContent = eur(p.installments_total);
    $("#p-inst-label").textContent = p.installment_current ? `Abschläge (${eur(p.installment_current)}/Mon.)` : "Abschläge";
    $("#p-rec").textContent = p.recommended_installment != null ? `${eur(p.recommended_installment)}/Mon.` : "–";
    $("#break-even").textContent = p.break_even_rate != null
      ? `Ohne Nachzahlung bleibst du, wenn du ab jetzt im Schnitt höchstens ${kwh(p.break_even_rate, 1)} pro Tag verbrauchst.`
      : "";
  }

  // Kacheln
  const r = s.rate;
  $("#t-rate").textContent = `${nf(1).format(r.recent)} kWh`;
  $("#t-rate-sub").textContent = `pro Tag · ${s.day ? eur(s.day.eur, 2) : ""} · Ø ${nf(0).format(r.recent_days)} Tage`;
  if (s.deviation) {
    const d = s.deviation.percent;
    // Trendpfeil: ±5 % gilt als gleichbleibend
    const trend = d > 5 ? ["up", "↑", "mehr Verbrauch"] : d < -5 ? ["down", "↓", "weniger Verbrauch"] : ["flat", "=", "gleichbleibend"];
    $("#t-dev").innerHTML = `<span class="trend trend-${trend[0]}" title="${trend[2]}" aria-label="${trend[2]}">${trend[1]}</span>${d >= 0 ? "+" : "−"}${nf(0).format(Math.abs(d))} %`;
    $("#t-dev-sub").textContent = `letzte Ablesung ${nf(1).format(s.deviation.last_rate)} vs. ${nf(1).format(s.deviation.reference_rate)} kWh/Tag`;
  } else {
    $("#t-dev").textContent = "–";
    $("#t-dev-sub").textContent = "ab 3 Ablesungen";
  }
  $("#t-month-label").textContent = `Monat ${s.month.label}`;
  $("#t-month").textContent = kwh(s.month.kwh_projected);
  $("#t-month-sub").textContent = `Prognose${s.month.eur_projected != null ? ` · ${eur(s.month.eur_projected)}` : ""}${s.month.kwh_so_far != null ? ` · bisher ${kwh(s.month.kwh_so_far)}` : ""}`;
  $("#t-year").textContent = kwh(s.year.kwh);
  $("#t-year-sub").textContent = `beim aktuellen Verbrauch${s.year.eur != null ? ` · ${eur(s.year.eur)}` : ""}`;

  drawChart(s.intervals, p ? p.break_even_rate : null);
}

// ------------------------------------------------------------ Diagramm
function niceMax(v) {
  const step = v <= 10 ? 2 : v <= 25 ? 5 : 10;
  return { max: Math.ceil((v * 1.1) / step) * step, step };
}

let lastChart = null;
window.addEventListener("resize", () => lastChart && drawChart(...lastChart));

function drawChart(intervals, refRate) {
  lastChart = [intervals, refRate];
  const el = $("#chart");
  const WINDOW_DAYS = 60;
  if (!intervals || !intervals.length) { el.innerHTML = ""; return; }
  const end = new Date(intervals[intervals.length - 1].to).getTime();
  const start = Math.max(new Date(intervals[0].from).getTime(), end - WINDOW_DAYS * 864e5);
  const data = intervals.filter((i) => new Date(i.to).getTime() > start);

  const W = Math.max(280, Math.round(el.clientWidth || 340)), H = 200, m = { t: 12, r: 6, b: 24, l: 28 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const peak = Math.max(...data.map((d) => d.rate), refRate || 0, 1);
  const { max, step } = niceMax(peak);
  const x = (t) => m.l + ((t - start) / (end - start || 1)) * iw;
  const y = (v) => m.t + ih - (v / max) * ih;

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Verbrauch in kWh pro Tag je Ableseintervall">`;
  svg += `<g class="axis">`;
  for (let v = 0; v <= max; v += step) {
    svg += `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/>`;
    svg += `<text x="${m.l - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`;
  }
  // Datumsbeschriftung: ca. 5 Marken
  const ticks = W < 420 ? 3 : 5;
  for (let i = 0; i <= ticks; i++) {
    const t = start + ((end - start) * i) / ticks;
    const anchor = i === 0 ? "start" : i === ticks ? "end" : "middle";
    svg += `<text x="${x(t)}" y="${H - 6}" text-anchor="${anchor}">${dateShort(new Date(t).toISOString())}</text>`;
  }
  svg += `</g>`;

  data.forEach((d, i) => {
    const x0 = x(Math.max(new Date(d.from).getTime(), start)) + 1;
    const x1 = x(new Date(d.to).getTime()) - 1;
    const w = Math.max(2, x1 - x0);
    const top = y(d.rate), base = y(0);
    const rr = Math.min(4, w / 2, base - top);
    const path = `M${x0},${base} V${top + rr} Q${x0},${top} ${x0 + rr},${top} H${x0 + w - rr} Q${x0 + w},${top} ${x0 + w},${top + rr} V${base} Z`;
    svg += `<path class="bar" data-i="${i}" d="${path}"/>`;
    // größere Trefferfläche für Tippen
    svg += `<rect class="hit" data-i="${i}" x="${x0 - 1}" y="${m.t}" width="${w + 2}" height="${ih}" fill="transparent"/>`;
  });

  if (refRate) {
    svg += `<line class="ref" x1="${m.l}" x2="${W - m.r}" y1="${y(refRate)}" y2="${y(refRate)}"/>`;
    svg += `<text class="ref-label" x="${m.l + 4}" y="${y(refRate) - 5}" text-anchor="start">${nf(1).format(refRate)}</text>`;
  }
  svg += `</svg>`;
  el.innerHTML = svg;

  const tip = $("#tooltip");
  const show = (ev) => {
    const i = ev.target.dataset && ev.target.dataset.i;
    el.querySelectorAll(".bar").forEach((b) => b.classList.toggle("active", b.dataset.i === i));
    if (i == null) { tip.classList.add("hidden"); return; }
    const d = data[i];
    tip.innerHTML = `<strong>${nf(1).format(d.rate)} kWh/Tag</strong><br>${dateTime(d.from)} – ${dateTime(d.to)}<br>${kwh(d.kwh, 0)} in ${nf(1).format(d.days)} Tagen`;
    tip.classList.remove("hidden");
    const px = ev.clientX ?? 0, py = ev.clientY ?? 0;
    const tw = tip.offsetWidth;
    tip.style.left = `${Math.min(window.innerWidth - tw - 8, Math.max(8, px - tw / 2))}px`;
    tip.style.top = `${Math.max(8, py - tip.offsetHeight - 14)}px`;
  };
  el.onpointermove = show;
  el.onpointerdown = show;
  el.onpointerleave = () => { tip.classList.add("hidden"); el.querySelectorAll(".bar").forEach((b) => b.classList.remove("active")); };

  $("#chart-table").innerHTML = `<table><tr><th>Zeitraum</th><th>Tage</th><th>kWh</th><th>kWh/Tag</th></tr>${
    [...intervals].reverse().map((d) => `<tr><td>${dateShort(d.from)}–${dateShort(d.to)}</td><td>${nf(1).format(d.days)}</td><td>${nf(0).format(d.kwh)}</td><td>${nf(1).format(d.rate)}</td></tr>`).join("")
  }</table>`;
}

// ------------------------------------------------------------ Ablesen
function localInputValue(d = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

async function prepareRead() {
  $("#r-ts").value = localInputValue();
  $("#r-warn").classList.add("hidden");
  $("#r-submit").textContent = "Speichern";
  delete $("#read-form").dataset.force;
  try {
    const { body: s } = await api("summary");
    if (s.last_reading) {
      const est = s.estimated_now != null ? ` · erwartet jetzt ca. ${nf(0).format(s.estimated_now)}` : "";
      $("#r-last").textContent = `Letzte Ablesung: ${nf(0).format(s.last_reading.value)} am ${dateTime(s.last_reading.ts)}${est}`;
    }
  } catch { /* offline */ }
  setTimeout(() => $("#r-value").focus(), 50);
}

$("#read-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  const value = parseNum($("#r-value").value);
  if (!Number.isFinite(value)) { toast("Bitte einen gültigen Zählerstand eingeben"); return; }
  const fd = new FormData();
  fd.append("value", value);
  fd.append("ts", new Date($("#r-ts").value).toISOString());
  fd.append("kind", $("#r-kind").value);
  if ($("#r-note").value) fd.append("note", $("#r-note").value);
  if ($("#r-photo").files[0]) fd.append("photo", $("#r-photo").files[0]);
  if (form.dataset.force) fd.append("force", "true");
  $("#r-submit").disabled = true;
  try {
    const { status, body } = await api("readings", { method: "POST", body: fd });
    if (status === 409) {
      $("#r-warn").innerHTML = `<strong>Bitte prüfen:</strong><br>${body.warnings.map(esc).join("<br>")}`;
      $("#r-warn").classList.remove("hidden");
      $("#r-submit").textContent = "Trotzdem speichern";
      form.dataset.force = "1";
      return;
    }
    form.reset();
    toast("Ablesung gespeichert");
    location.hash = "#home";
  } catch (e) {
    toast(e.message);
  } finally {
    $("#r-submit").disabled = false;
  }
});

// Wird der Wert nach einer Warnung korrigiert, muss erneut geprüft werden.
$("#r-value").addEventListener("input", () => {
  const form = $("#read-form");
  if (!form.dataset.force) return;
  delete form.dataset.force;
  $("#r-warn").classList.add("hidden");
  $("#r-submit").textContent = "Speichern";
});

// ------------------------------------------------------------ Verlauf
const KIND = { ablesung: "", versorger: "Versorger", schaetzung: "Schätzung" };

async function loadHistory() {
  const { body: rows } = await api("readings");
  const real = rows.filter((r) => r.kind !== "schaetzung");
  const ul = $("#history");
  if (!rows.length) { ul.innerHTML = `<li class="muted">Noch keine Ablesungen.</li>`; return; }
  ul.innerHTML = rows.map((r) => {
    let sub = dateTime(r.ts);
    const idx = real.findIndex((x) => x.id === r.id);
    if (idx >= 0 && idx < real.length - 1) {
      const prev = real[idx + 1];
      const days = (new Date(r.ts) - new Date(prev.ts)) / 864e5;
      if (days > 0) sub += ` · +${nf(0).format(r.value - prev.value)} kWh · ${nf(1).format((r.value - prev.value) / days)} kWh/Tag`;
    }
    if (r.note) sub += ` · ${esc(r.note)}`;
    const photo = r.photo ? ` · <a href="api/photos/${encodeURIComponent(r.photo)}" target="_blank">Foto</a>` : "";
    const badge = KIND[r.kind] ? `<span class="badge">${KIND[r.kind]}</span>` : "";
    return `<li><div><div class="main">${nf(0).format(r.value)} kWh${badge}</div><div class="sub">${sub}${photo}</div></div>
      <button class="del" data-del="readings/${r.id}">Löschen</button></li>`;
  }).join("");
}

// ------------------------------------------------------------ Einstellungen
async function loadSettings() {
  const { body: c } = await api("config");
  const list = (sel, items, fmt, table) => {
    $(sel).innerHTML = items.length
      ? items.map((i) => `<li><div>${fmt(i)}</div><button class="del" data-del="${table}/${i.id}">Löschen</button></li>`).join("")
      : `<li class="muted">Noch nichts angelegt.</li>`;
  };
  list("#list-tariffs", c.tariffs, (t) => {
    const brutto = ((t.energy_ct + t.tax_ct) * (1 + t.vat_pct / 100));
    const base = t.base_eur_year * (1 + t.vat_pct / 100);
    return `<div class="main">${nf(2).format(brutto)} ct/kWh · ${eur(base, 2)}/Jahr</div>
      <div class="sub">brutto · ab ${date(t.valid_from)}${t.name ? ` · ${esc(t.name)}` : ""}</div>`;
  }, "tariffs");
  list("#list-periods", c.periods, (p) =>
    `<div class="main">${date(p.start)} – ${date(p.end)}</div><div class="sub">Anfangsstand ${nf(0).format(p.start_value)} kWh${p.note ? ` · ${esc(p.note)}` : ""}</div>`, "periods");
  list("#list-installments", c.installments, (i) =>
    `<div class="main">${eur(i.amount, 2)} / Monat</div><div class="sub">ab ${date(i.valid_from)} · fällig am ${i.due_day}.</div>`, "installments");
  const { body: h } = await api("health");
  $("#version").textContent = `Version ${h.version}`;
}

for (const table of ["tariffs", "periods", "installments"]) {
  $(`#f-${table}`).addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const data = Object.fromEntries(new FormData(ev.target));
    for (const k of ["energy_ct", "tax_ct", "base_eur_year", "vat_pct", "start_value", "amount", "due_day"]) {
      if (k in data) data[k] = data[k] === "" ? undefined : parseNum(data[k]);
    }
    for (const k in data) if (data[k] === "" || data[k] === undefined) delete data[k];
    try {
      await api(table, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
      ev.target.reset();
      toast("Gespeichert");
      loadSettings();
    } catch (e) { toast(e.message); }
  });
}

$("#f-import").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const fd = new FormData(ev.target);
  fd.set("replace", ev.target.replace.checked ? "true" : "false");
  try {
    const { body } = await api("import", { method: "POST", body: fd });
    const n = body.imported;
    toast(`Importiert: ${n.readings} Ablesungen, ${n.tariffs} Tarife, ${n.periods} Zeiträume, ${n.installments} Abschläge`);
    ev.target.reset();
    loadSettings();
  } catch (e) { toast(e.message); }
});

document.addEventListener("click", async (ev) => {
  const del = ev.target.closest("[data-del]");
  if (!del) return;
  if (!confirm("Wirklich löschen?")) return;
  try {
    await api(del.dataset.del, { method: "DELETE" });
    toast("Gelöscht");
    route();
  } catch (e) { toast(e.message); }
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
route();
