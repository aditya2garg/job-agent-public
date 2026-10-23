const $ = (s, r = document) => r.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) n.append(kid.nodeType ? kid : document.createTextNode(kid));
  return n;
};

const state = { verdict: "", tier: "", loc: new Set(), minsal: "", status: "", q: "", minscore: 60, selected: null, stats: null };
const WEIGHTS = { role: .22, skills: .28, semantic: .07, seniority: .10, compensation: .18, location: .15 };
const DIM_LABEL = { role: "Role fit", skills: "Skills", semantic: "Resume similarity", seniority: "Seniority", compensation: "Compensation", location: "Location" };

const color = s => (s >= 84 ? "var(--green)" : s >= 74 ? "var(--lime)" : s >= 62 ? "var(--amber)" : "var(--slate)");
const money = n => (n >= 1000 ? "$" + Math.round(n / 1000) + "k" : "$" + n);
const api = async (url, opts) => {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
};

async function loadStats() {
  const s = await api("/api/stats");
  state.stats = s;
  const v = s.by_verdict || {};
  $("#subtitle").textContent = s.last_scrape ? `Last scrape ${new Date(s.last_scrape).toLocaleString()}` : "No data yet — run a refresh";
  const box = $("#stats");
  box.replaceChildren(
    ...[
      [s.total, "tracked"],
      [v["Apply now"] || 0, "apply now"],
      [v["Apply"] || 0, "apply"],
      [s.new_24h, "new · 24h"],
      [s.llm_analyzed, "LLM-reviewed"],
    ].map(([n, l]) => el("div", { class: "stat" }, el("b", {}, (n ?? 0).toLocaleString()), el("span", {}, l)))
  );
  setRefresh(s.refresh);
}

function setRefresh(r) {
  const btn = $("#refresh");
  btn.disabled = !!r.running;
  btn.textContent = r.running ? "Refreshing…" : "Refresh jobs";
  $("#refresh-msg").textContent = r.error ? "Error: " + r.error : r.message || "";
  if (r.running) setTimeout(async () => { const s = await api("/api/stats"); setRefresh(s.refresh); if (!s.refresh.running) { await loadStats(); loadJobs(); } }, 2500);
}

async function loadJobs() {
  const list = $("#list");
  list.replaceChildren(...Array.from({ length: 6 }, () => el("div", { class: "skeleton" })));
  const p = new URLSearchParams({ min_score: state.minscore, limit: 400 });
  if (state.verdict) p.set("verdict", state.verdict);
  if (state.tier) p.set("tier", state.tier);
  if (state.loc.size) p.set("location", [...state.loc].join(","));
  if (state.minsal) p.set("min_salary", state.minsal);
  if (state.status) { p.set("status", state.status); p.set("include_hidden", "true"); }
  if (state.q) p.set("q", state.q);
  let data;
  try { data = await api("/api/jobs?" + p); } catch (e) { list.replaceChildren(el("div", { class: "empty" }, "Could not load jobs: " + e.message)); return; }
  $("#count").textContent = `${data.total.toLocaleString()} matches` + (data.total > data.jobs.length ? ` (showing top ${data.jobs.length})` : "");
  if (!data.jobs.length) {
    list.replaceChildren(el("div", { class: "empty" },
      state.stats && state.stats.total === 0 ? "Nothing scraped yet. Click “Refresh jobs” (or run `jobhunt run`)." : "No jobs match these filters. Try lowering the minimum score."));
    return;
  }
  list.replaceChildren(...data.jobs.map(card));
}

function chip(text, cls = "") { return el("span", { class: "chip " + cls }, text); }

function card(j) {
  const pay = j.salary_known ? chip(`${money(j.salary_low)}–${money(j.salary_high)}`, j.salary_high >= 220000 ? "green" : j.salary_high >= 170000 ? "amber" : "red") : chip("pay n/d");
  const tier = { preferred: ["Preferred role", "accent"], adjacent: ["Adjacent", ""], longshot: ["Long-shot", ""] }[j.role_tier] || [j.role_tier, ""];
  const locCls = ["bay_area", "austin", "remote_us"].includes(j.location_kind) ? "green" : "";
  const n = el("article", { class: "card" + (state.selected === j.id ? " sel" : "") + (["ignored", "rejected"].includes(j.status) ? " hidden-state" : ""), tabindex: "0", "data-id": j.id },
    el("div", { class: "ring", style: `--p:${j.score};--c:${color(j.score)}` }, el("span", {}, Math.round(j.score))),
    el("div", {},
      el("h3", {}, j.title),
      el("div", { class: "co" }, j.company),
      el("div", { class: "chips" }, chip(j.verdict, j.verdict === "Apply now" ? "green" : j.verdict === "Apply" ? "green" : j.verdict === "Stretch" ? "amber" : ""), chip(j.location_label, locCls), pay, chip(tier[0], tier[1]),
        j.status !== "new" ? chip(j.status, "accent") : null, j.has_llm ? chip("LLM-reviewed", "accent") : null, ...j.flags.slice(0, 2).map(f => chip(f, "red"))),
      j.pros.length ? el("div", { class: "why" }, j.pros.slice(0, 2).join(" · ")) : null));
  const open = () => select(j.id);
  n.addEventListener("click", open);
  n.addEventListener("keydown", e => { if (e.key === "Enter") open(); });
  return n;
}

async function select(id) {
  state.selected = id;
  document.querySelectorAll(".card").forEach(c => c.classList.toggle("sel", c.dataset.id === id));
  const pane = $("#detail");
  pane.classList.add("open");
  pane.replaceChildren(el("div", { class: "skeleton" }));
  try { renderDetail(await api("/api/jobs/" + id)); } catch (e) { pane.replaceChildren(el("div", { class: "empty" }, "Failed to load: " + e.message)); }
}

function bar(label, value, weight, note) {
  return [
    el("div", { class: "bar" }, el("div", {}, label, el("small", {}, ` ×${Math.round(weight * 100)}%`)),
      el("div", { class: "track" }, el("div", { class: "fill", style: `width:${Math.max(2, value)}%;--c:${color(value + 8)}` })), el("div", {}, Math.round(value))),
    note ? el("div", { class: "bar-note" }, note) : null,
  ];
}

function skillChip(r) {
  const cls = r.proficiency >= 0.6 ? "green" : r.proficiency >= 0.35 ? "amber" : "red";
  const tag = r.section === "required" ? "req" : r.section === "nice" ? "nice" : "";
  return el("span", { class: "chip " + cls, title: `your proficiency ${Math.round(r.proficiency * 100)}% · ${r.section}` }, r.label + (tag ? ` · ${tag}` : ""));
}

function renderDetail(d) {
  const det = d.details, sk = det.skills;
  const pane = $("#detail");
  const notes = {
    role: det.role.reason,
    skills: `${sk.matched.length} strong · ${sk.partial.length} partial · ${sk.gaps.length} gaps` + (sk.critical_gaps.length ? ` · hard gaps: ${sk.critical_gaps.join(", ")}` : ""),
    seniority: det.seniority.note,
    compensation: det.salary.note,
    location: `${det.location.label}${det.location.matched ? " (" + det.location.matched + ")" : ""}`,
    semantic: "TF-IDF similarity to your resume, percentile among all scraped jobs",
  };
  const statusBtn = (s, label) => el("button", { class: "btn sm" + (d.status === s ? " on" : ""), onclick: async () => { await api(`/api/jobs/${d.id}/status`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ status: s }) }); await loadStats(); await loadJobs(); select(d.id); } }, label);

  const kids = [
    el("button", { class: "btn sm close-m", onclick: () => pane.classList.remove("open") }, "← Back"),
    el("div", { class: "d-head" },
      el("div", {}, el("h2", {}, d.title), el("div", { class: "co" }, `${d.company} · ${d.location || "location n/a"}`)),
      el("div", { class: "bigscore", style: `color:${color(d.score)}` }, el("b", {}, Math.round(d.score)), el("span", {}, d.verdict))),
    el("div", { class: "d-actions" },
      el("a", { class: "btn primary sm", href: d.url, target: "_blank", rel: "noopener" }, "Open posting ↗"),
      statusBtn("saved", "Save"), statusBtn("applied", "Applied"), statusBtn("interviewing", "Interviewing"), statusBtn("ignored", "Ignore")),
    d.flags.length ? el("div", { class: "chips" }, ...d.flags.map(f => chip("⚠ " + f, "red"))) : null,
    det.caps && det.caps.length ? el("div", { class: "hint" }, `Score capped (raw ${det.raw_weighted}): ${det.caps.join(", ")}`) : null,

    el("div", { class: "section" }, el("h4", {}, "Score breakdown"),
      el("div", { class: "bars" }, ...["role", "skills", "compensation", "location", "seniority", "semantic"].flatMap(k => bar(DIM_LABEL[k], d.breakdown[k], WEIGHTS[k], notes[k])))),

    el("div", { class: "section" }, el("h4", {}, "Why it fits"), el("ul", { class: "tight pro" }, ...d.pros.map(p => el("li", {}, p)))),
    d.cons.length ? el("div", { class: "section" }, el("h4", {}, "Watch-outs"), el("ul", { class: "tight con" }, ...d.cons.map(p => el("li", {}, p)))) : null,

    el("div", { class: "section" }, el("h4", {}, "Skills the posting asks for vs. your resume"),
      el("div", { class: "skill-grid" }, ...[...sk.matched, ...sk.partial, ...sk.gaps].slice(0, 40).map(skillChip))),

    det.evidence.length ? el("div", { class: "section" }, el("h4", {}, "Resume talking points for this job"),
      ...det.evidence.map(e => el("div", { class: "ev" }, e.statement, el("small", {}, "maps to: " + e.themes.join(", "))))) : null,

    llmSection(d.llm),

    el("div", { class: "section" }, el("details", { class: "desc" }, el("summary", {}, "Full job description"), el("pre", {}, d.description || "(none)"))),
  ];
  pane.replaceChildren(...kids);
  pane.scrollTop = 0;
}

function llmSection(L) {
  if (!L) {
    const avail = state.stats && state.stats.llm_available;
    return el("div", { class: "section" }, el("h4", {}, "LLM deep review"),
      el("div", { class: "hint" }, avail ? "Not reviewed yet. Run `jobhunt analyze` to deep-review your top matches." : "Optional: set ANTHROPIC_API_KEY or OPENAI_API_KEY and run `jobhunt analyze` for a critical second opinion with gap mitigations and interview prep."));
  }
  const list = (arr, cls) => el("ul", { class: "tight " + (cls || "") }, ...arr.map(x => el("li", {}, x)));
  return el("div", { class: "section" }, el("h4", {}, `LLM deep review · ${L.model}`),
    el("div", { class: "llm" },
      el("p", {}, el("b", {}, `Fit ${L.fit_score ?? "?"}/100 · ${String(L.recommendation || "").replace("_", " ")}`), " — ", L.summary || ""),
      L.role_reality_check ? el("p", { class: "muted", style: "margin-top:8px" }, el("b", {}, "Reality check: "), L.role_reality_check) : null,
      L.strengths.length ? [el("h4", { style: "margin-top:12px" }, "Strengths"), list(L.strengths, "pro")] : null,
      L.gaps.length ? [el("h4", { style: "margin-top:12px" }, "Gaps"), el("ul", { class: "tight" }, ...L.gaps.map(g => el("li", {}, el("span", { class: "sev " + g.severity }, g.severity + " "), g.gap, g.mitigation ? el("div", { class: "muted" }, "→ " + g.mitigation) : null)))] : null,
      L.red_flags.length ? [el("h4", { style: "margin-top:12px" }, "Red flags"), list(L.red_flags, "con")] : null,
      L.resume_tweaks.length ? [el("h4", { style: "margin-top:12px" }, "Resume tweaks"), list(L.resume_tweaks)] : null,
      L.interview_prep.length ? [el("h4", { style: "margin-top:12px" }, "Interview prep"), list(L.interview_prep)] : null,
      L.comp_note ? el("p", { class: "muted", style: "margin-top:10px" }, "Comp: " + L.comp_note) : null));
}

function wireSeg(id, key, multi) {
  const seg = $(id);
  seg.addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    const v = b.dataset.v;
    if (multi) { b.classList.toggle("on"); b.classList.contains("on") ? state[key].add(v) : state[key].delete(v); }
    else { seg.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); state[key] = v; }
    loadJobs();
  });
}

let t;
$("#q").addEventListener("input", e => { clearTimeout(t); t = setTimeout(() => { state.q = e.target.value.trim(); loadJobs(); }, 250); });
$("#minsal").addEventListener("change", e => { state.minsal = e.target.value; loadJobs(); });
$("#status").addEventListener("change", e => { state.status = e.target.value; loadJobs(); });
$("#minscore").addEventListener("input", e => { state.minscore = +e.target.value; $("#minscore-val").textContent = e.target.value; clearTimeout(t); t = setTimeout(loadJobs, 150); });
$("#refresh").addEventListener("click", async () => { const r = await api("/api/refresh", { method: "POST" }); setRefresh({ ...r, running: true }); });
wireSeg("#seg-verdict", "verdict"); wireSeg("#seg-tier", "tier"); wireSeg("#seg-loc", "loc", true);

(async () => { await loadStats(); await loadJobs(); })();
