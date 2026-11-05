const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
const fmt = (n) => {
  if (n === null || n === undefined) return "-";
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(1).replace(".0", "") + "B";
  if (a >= 1e6) return (n / 1e6).toFixed(1).replace(".0", "") + "M";
  if (a >= 1e3) return (n / 1e3).toFixed(1).replace(".0", "") + " mil";
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
};
const PLATFORM = { youtube: "YouTube", instagram: "Instagram", twitter: "X" };
const KIND_LABELS = { viral: "Virais", shorts: "Shorts", short_xray: "Raio-X", research: "Relatório" };
let lastShortsStudy = null; // estudo de Shorts aberto, usado como comparação no raio-X

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(msg);
  }
  return res.json();
}

// ---------------------------------------------------------------- abas
$$(".tab").forEach((btn) =>
  btn.addEventListener("click", () => {
    $$(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${btn.dataset.tab}`));
    if (btn.dataset.tab === "history") loadHistory();
  })
);

function showTab(name) {
  $(`.tab[data-tab="${name}"]`).click();
}

// ---------------------------------------------------------------- status
async function loadStatus() {
  const s = await api("/api/status");
  $("#status").innerHTML =
    `<span class="${s.ai ? "on" : ""}">IA: ${s.ai ? esc(s.ai_model) : "desligada"}</span>` +
    `<span class="${s.apify ? "on" : ""}">Apify: ${s.apify ? "ok" : "sem token"}</span>`;
  $$(".ai-toggle").forEach((l) => {
    l.classList.toggle("disabled", !s.ai);
    $("input", l).disabled = !s.ai;
    l.title = s.ai ? "Usa poucos tokens: a IA recebe só os dados já resumidos" : "Defina AI_API_KEY no .env para habilitar";
  });
  $$(".needs-apify").forEach((l) => {
    l.classList.toggle("disabled", !s.apify);
    $("input", l).disabled = !s.apify;
  });
  $("#apify-hint").hidden = s.apify;
}

// ---------------------------------------------------------------- jobs
async function runJob(endpoint, payload, progressEl, button) {
  button.disabled = true;
  progressEl.hidden = false;
  progressEl.innerHTML = '<div class="spin">Iniciando...</div>';
  try {
    const { job_id } = await api(endpoint, { method: "POST", body: JSON.stringify(payload) });
    while (true) {
      await new Promise((r) => setTimeout(r, 1500));
      const job = await api(`/api/jobs/${job_id}`);
      const steps = job.progress.length ? job.progress : ["Iniciando..."];
      progressEl.innerHTML = steps.map((m, i) => `<div class="${i === steps.length - 1 && job.status === "running" ? "spin" : ""}">${esc(m)}</div>`).join("");
      if (job.status === "done") { progressEl.hidden = true; return job.search_id; }
      if (job.status === "error") throw new Error(job.error);
    }
  } catch (err) {
    progressEl.innerHTML = `<div class="error">Erro: ${esc(err.message)}</div>`;
    throw err;
  } finally {
    button.disabled = false;
  }
}

function formData(form) {
  const fd = new FormData(form);
  const data = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox" && el.name === "platforms") continue;
    if (el.tagName === "BUTTON") continue;
    if (el.type === "checkbox") data[el.name] = el.checked;
    else if (el.type === "number") data[el.name] = Number(el.value);
    else data[el.name] = el.value;
  }
  if (form.elements.platforms) data.platforms = fd.getAll("platforms");
  return data;
}

// ---------------------------------------------------------------- virais
$("#viral-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = formData(e.target);
  if (!payload.platforms.length) return alert("Escolha ao menos uma rede.");
  $("#viral-result").innerHTML = "";
  try {
    const id = await runJob("/api/viral", payload, $("#viral-progress"), $("button[type=submit]", e.target));
    await openSearch(id);
  } catch {}
});

const aiBadge = (r) =>
  r.ai && r.ai.used ? `<span class="hint">IA ${r.ai.cached ? "(cache, 0 tokens)" : r.ai.usage ? `(${r.ai.usage.total_tokens ?? "?"} tokens)` : ""}</span>` : "";

function bindSubtabs(el) {
  $$(".toolbar .subtabs button", el).forEach((b) =>
    b.addEventListener("click", () => {
      $$(".toolbar .subtabs button", el).forEach((x) => x.classList.toggle("active", x === b));
      $$(".view", el).forEach((v) => (v.hidden = v.dataset.view !== b.dataset.view));
    })
  );
}

// Estudo com abas "Estudo" e "Referências" (usado em virais e em Shorts)
function renderStudy(s, el, { isShorts = false } = {}) {
  const r = s.result;
  const hookRows = isShorts ? [...(r.analysis.title_hooks || []), ...(r.analysis.opening_hooks || [])] : r.analysis.hooks || [];
  const labels = Object.fromEntries(hookRows.map((h) => [h.key, h.label]));
  const sorts = isShorts
    ? `<option value="score">Ordenar: score</option><option value="views">Views</option><option value="views_per_day">Views/dia</option>
       <option value="outlier">Views/inscritos</option><option value="wps">Palavras/s</option><option value="duration">Duração</option>`
    : `<option value="score">Ordenar: score</option><option value="views">Views</option><option value="views_per_day">Views/dia</option>
       <option value="outlier">Views/seguidores</option><option value="engagement">Engajamento</option>`;
  el.innerHTML = `
    <div class="toolbar">
      <div class="subtabs">
        <button class="active" data-view="study">Estudo</button>
        <button data-view="refs">Referências (${r.posts.length})</button>
      </div>
      <span class="spacer"></span>
      ${aiBadge(r)}
      <a href="/api/searches/${s.id}/report.md"><button class="ghost">Baixar .md</button></a>
    </div>
    <div class="view" data-view="study"><div class="card report">${s.report_html}</div></div>
    <div class="view" data-view="refs" hidden>
      <div class="filters">
        <select class="f-platform" ${isShorts ? "hidden" : ""}><option value="">Todas as redes</option>${[...new Set(r.posts.map((p) => p.platform))].map((p) => `<option value="${esc(p)}">${esc(PLATFORM[p] || p)}</option>`).join("")}</select>
        <select class="f-sort">${sorts}</select>
      </div>
      <div class="grid posts-grid"></div>
    </div>`;
  bindSubtabs(el);

  const val = (p, key) => {
    if (key === "views") return p.views ?? -1;
    if (key === "duration") return p.duration ?? -1;
    if (key === "wps") return (p.metrics.short || {}).wps ?? -1;
    return p.metrics[key] ?? -1;
  };
  const draw = () => {
    const plat = $(".f-platform", el).value;
    const key = $(".f-sort", el).value;
    const posts = r.posts.filter((p) => !plat || p.platform === plat).sort((a, b) => val(b, key) - val(a, key));
    $(".posts-grid", el).innerHTML = posts.map((p) => postCard(p, labels)).join("") || '<p class="empty">Nada aqui.</p>';
  };
  $(".f-platform", el).addEventListener("change", draw);
  $(".f-sort", el).addEventListener("change", draw);
  draw();
}

function renderViral(s) {
  renderStudy(s, $("#viral-result"));
}

function postCard(p, labels) {
  const m = p.metrics || {};
  const sh = m.short;
  const score = m.score ?? 0;
  const hooks = (p.hooks || []).map((k) => `<span class="badge">${esc(labels[k] || k)}</span>`).join("");
  const opening = sh ? sh.hook_text : p.opening;
  const shortStats = sh
    ? `<div class="shortstats">${Math.round(p.duration || 0)}s · ${sh.has_speech ? `fala em ${sh.speech_start}s · ${sh.wps} palavras/s` : sh.transcript_status === "bloqueado" ? "fala não lida (legenda bloqueada)" : "sem fala na legenda"}${sh.cta ? " · CTA" : ""}${sh.loop ? " · loop" : ""}${sh.music ? " · música" : ""}</div>`
    : "";
  const thumb = safeUrl(p.thumbnail);
  return `
    <article class="post">
      <div class="thumb ${sh ? "vertical" : ""}" style="${thumb !== "#" ? `background-image:url('${esc(thumb)}')` : ""}">
        <span class="plat">${esc(PLATFORM[p.platform] || p.platform)}</span>
        <span class="score ${score >= 75 ? "hot" : ""}">${score}</span>
      </div>
      <div class="body">
        <a class="title" href="${esc(safeUrl(p.url))}" target="_blank" rel="noopener noreferrer">${esc(p.headline || "(sem título)")}</a>
        <div class="meta">${esc(p.author)}${p.author_followers ? ` · ${fmt(p.author_followers)} seguidores` : ""}${m.age_days ? ` · há ${Math.round(m.age_days)} dias` : ""}</div>
        <div class="stats">
          <div><b>${fmt(p.views)}</b>views</div>
          <div><b>${fmt(m.views_per_day)}</b>views/dia</div>
          <div><b>${m.outlier ? m.outlier + "x" : "-"}</b>views/seg.</div>
          <div><b>${fmt(p.likes)}</b>likes</div>
          <div><b>${fmt(p.comments)}</b>coment.</div>
          <div><b>${m.engagement != null ? (m.engagement * 100).toFixed(1) + "%" : "-"}</b>engaj.</div>
        </div>
        ${shortStats}
        <div class="badges"><span class="badge fmt">${esc(sh ? sh.duration_bucket : p.format)}</span>${hooks}</div>
        ${opening ? `<div class="opening">${sh ? "0–3s: " : ""}“${esc(opening.slice(0, 200))}”</div>` : ""}
        ${sh ? `<button class="ghost xray-btn" data-url="${esc(safeUrl(p.url))}">Raio-X deste Short</button>` : ""}
      </div>
    </article>`;
}

// ---------------------------------------------------------------- relatório de tema
$("#research-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#research-result").innerHTML = "";
  try {
    const id = await runJob("/api/research", formData(e.target), $("#research-progress"), $("button[type=submit]", e.target));
    await openSearch(id);
  } catch {}
});

// ---------------------------------------------------------------- Shorts
$$("#tab-shorts .mode-switch button").forEach((b) =>
  b.addEventListener("click", () => {
    $$("#tab-shorts .mode-switch button").forEach((x) => x.classList.toggle("active", x === b));
    $$("#tab-shorts form").forEach((f) => (f.hidden = f.dataset.mode !== b.dataset.mode));
    if (b.dataset.mode === "xray") loadBenchmarks();
  })
);

function setShortsMode(mode) {
  $(`#tab-shorts .mode-switch button[data-mode="${mode}"]`).click();
}

async function loadBenchmarks(selectId) {
  const sel = $("#xray-form select[name=benchmark_id]");
  const current = selectId ?? sel.value;
  const items = await api("/api/shorts/benchmarks").catch(() => []);
  sel.innerHTML = '<option value="">Não comparar</option>' +
    items.map((i) => `<option value="${i.id}">${esc(i.query)} · ${new Date(i.created_at).toLocaleDateString("pt-BR")}</option>`).join("");
  sel.value = String(current || "");
}

$("#shorts-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#shorts-result").innerHTML = "";
  try {
    const id = await runJob("/api/shorts", formData(e.target), $("#shorts-progress"), $("button[type=submit]", e.target));
    lastShortsStudy = id;
    await openSearch(id);
  } catch {}
});

$("#xray-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = formData(e.target);
  payload.benchmark_id = payload.benchmark_id ? Number(payload.benchmark_id) : null;
  $("#shorts-result").innerHTML = "";
  try {
    const id = await runJob("/api/shorts/xray", payload, $("#shorts-progress"), $("button[type=submit]", e.target));
    await openSearch(id);
  } catch {}
});

// botão "Raio-X deste Short" nos cards: preenche o link e compara com o estudo aberto
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".xray-btn");
  if (!btn) return;
  showTab("shorts");
  setShortsMode("xray");
  await loadBenchmarks(lastShortsStudy);
  $("#xray-form input[name=url]").value = btn.dataset.url;
  $("#xray-form").requestSubmit();
});

function retrySpeechButton(s, blocked) {
  if (!blocked) return;
  const btn = document.createElement("button");
  btn.className = "ghost";
  btn.textContent = `Tentar ler a fala de novo (${blocked} pendente${blocked > 1 ? "s" : ""})`;
  btn.addEventListener("click", async () => {
    try {
      await runJob(`/api/searches/${s.id}/retry-speech`, {}, $("#shorts-progress"), btn);
      await openSearch(s.id);
    } catch {}
  });
  $("#shorts-result .toolbar .spacer").after(btn);
}

function renderShorts(s) {
  setShortsMode(s.params.mode === "channel" ? "xray" : "niche");
  lastShortsStudy = s.id;
  renderStudy(s, $("#shorts-result"), { isShorts: true });
  retrySpeechButton(s, s.result.analysis.summary.transcript_blocked);
}

function renderXray(s) {
  setShortsMode("xray");
  const r = s.result;
  const total = Math.max(...r.timeline.map((p) => p.end), 1);
  const bar = r.features.has_speech
    ? `<div class="timeline">${r.timeline.map((p) => `
        <div class="ph ${esc(p.phase)}" style="flex:${Math.max(p.end - p.start, total * 0.08)}">
          <b>${esc(p.phase)} · ${p.start}–${p.end}s</b><span>${esc(p.text || "(sem fala)")}</span>
        </div>`).join("")}</div>`
    : "";
  $("#shorts-result").innerHTML = `
    <div class="toolbar">
      <span class="spacer"></span>
      ${aiBadge(r)}
      <a href="/api/searches/${s.id}/report.md"><button class="ghost">Baixar .md</button></a>
    </div>
    ${bar}
    <div class="card report">${s.report_html}</div>`;
  retrySpeechButton(s, r.features.transcript_status === "bloqueado" ? 1 : 0);
}

function renderResearch(s) {
  const r = s.result;
  $("#research-result").innerHTML = `
    <div class="toolbar">
      <span class="spacer"></span>
      ${aiBadge(r)}
      <a href="/api/searches/${s.id}/report.md"><button class="ghost">Baixar .md</button></a>
    </div>
    <div class="card report">${s.report_html}</div>`;
}

// ---------------------------------------------------------------- histórico
async function openSearch(id) {
  const s = await api(`/api/searches/${id}`);
  history.replaceState(null, "", `#busca-${id}`); // link direto para a busca
  if (s.kind === "viral") { showTab("viral"); renderViral(s); }
  else if (s.kind === "shorts") { showTab("shorts"); renderShorts(s); }
  else if (s.kind === "short_xray") { showTab("shorts"); renderXray(s); }
  else { showTab("research"); renderResearch(s); }
}

async function loadHistory() {
  const items = await api("/api/history");
  const ul = $("#history-list");
  if (!items.length) { ul.innerHTML = '<li class="empty">Nenhuma busca ainda.</li>'; return; }
  ul.innerHTML = items.map((i) => `
    <li data-id="${i.id}">
      <span class="kind">${KIND_LABELS[i.kind] || i.kind}</span>
      <span class="q">${esc(i.query)}</span>
      <span class="date">${new Date(i.created_at).toLocaleString("pt-BR")}</span>
      <button class="ghost" data-act="open">Abrir</button>
      <button class="ghost" data-act="del">Excluir</button>
    </li>`).join("");
  $$("button", ul).forEach((b) =>
    b.addEventListener("click", async () => {
      const id = b.closest("li").dataset.id;
      if (b.dataset.act === "open") return openSearch(id);
      if (!confirm("Excluir esta busca do histórico?")) return;
      await api(`/api/searches/${id}`, { method: "DELETE" });
      loadHistory();
    })
  );
}

loadStatus().catch(() => {});
const deepLink = location.hash.match(/^#busca-(\d+)$/);
if (deepLink) openSearch(deepLink[1]).catch(() => {});
