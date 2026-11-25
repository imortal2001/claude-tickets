// Claude Tickets — the dashboard's script (served at /dashboard.js). Loaded at the end of <body>.
// The team's workflow, from workflow.json via /api/state (applyWorkflow): Jira status names by role, the board's order.
let ORDER = [], STATUS_RANK = {}, STATUS_TONE = {}, REPORT_RE = /(?!)/;
// Board sections the owner collapsed, by section id (owner 2026-11-24). Remembered in this browser only.
const COLLAPSED = new Set((() => { try { return JSON.parse(localStorage.getItem("ct-collapsed") || "[]"); } catch (e) { return []; } })());
const ST = () => CFG.status || {};
const roleOf = (status) => Object.keys(ST()).find((role) => ST()[role] === status);
// The board section of a Jira status, named by role so the CSS edge colours work for any team's status names.
const jiraSecId = (status) => "sec-jira-" + (roleOf(status) || status.replace(/\W+/g, "-"));
function applyWorkflow() {
  const s = ST();
  ORDER = CFG.order || [];
  STATUS_RANK = Object.fromEntries([...ORDER, s.released].filter(Boolean).map((name, i) => [name, i]));
  STATUS_TONE = {[s.review]: "info", [s.dev]: "warn", [s.blocked]: "bad", [s.released]: "ok"};
  // A run's final report: its first line holds one of the workflow's report words ("is migrated", …); no words = none.
  const words = (CFG.reportWords || []).filter(Boolean).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  REPORT_RE = words.length ? new RegExp("\\b(" + words.join("|") + ")\\b") : /(?!)/;
  $("hideToggle").hidden = !CFG.hideMarker;
  $("hideLabel").textContent = `Hide ${CFG.hideLabel || ""}`;
}
// Small line icons (owner 2026-11-20): drawn, so they sit dead centre in their boxes — the text glyphs (◷ ⌛) came
// from a fallback font and rode low.
const svg = (d, extra = "") => `<svg class="svgi" viewBox="0 0 16 16" aria-hidden="true" ${extra}>${d}</svg>`;
const IC = {
  alert: svg('<path d="M8 4.2v4.6" /><circle cx="8" cy="11.4" r=".9" fill="currentColor" stroke="none"/>'),
  check: svg('<path d="M3.6 8.3l2.9 2.9 5.9-6.2" />'),
  clock: svg('<circle cx="8" cy="8" r="5.6" /><path d="M8 4.9V8l2.2 1.5" />'),
  chevron: svg('<path d="M4.5 6.2 8 9.8l3.5-3.6" />'),
  hourglass: svg('<path d="M4.5 2.6h7M4.5 13.4h7M5.2 2.6c0 3 5.6 2.8 5.6 5.4S5.2 10.4 5.2 13.4M10.8 2.6c0 1.2-.8 2-1.8 2.6M10.8 13.4c0-3-5.6-2.8-5.6-5.4" />'),
  arrow: svg('<path d="M3.5 8h8.5M8.8 4.7L12.1 8l-3.3 3.3" />'),
  play: svg('<path d="M5.4 3.8v8.4L12 8z" fill="currentColor" stroke="none"/>'),
  stop: svg('<rect x="4.6" y="4.6" width="6.8" height="6.8" rx="1" fill="currentColor" stroke="none"/>'),
  dots: svg('<circle cx="4.5" cy="8" r="1" fill="currentColor" stroke="none"/><circle cx="8" cy="8" r="1" fill="currentColor" stroke="none"/><circle cx="11.5" cy="8" r="1" fill="currentColor" stroke="none"/>'),
  question: svg('<path d="M6.2 6.1a1.9 1.9 0 1 1 2.6 1.8c-.5.2-.8.6-.8 1.1v.6" /><circle cx="8" cy="11.6" r=".85" fill="currentColor" stroke="none"/>'),
};
let CFG = {};   // workspace values (Jira site, channels, reviewer) from /api/state
let data = null, logSession = null, logOffset = 0, logTimer = null;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const ago = (secs) => secs == null ? "never" : secs < 90 ? `${Math.round(secs)}s ago` : secs < 5400 ? `${Math.round(secs / 60)} min ago` : `${Math.round(secs / 3600)} h ago`;
const mins = (since) => since ? `${Math.max(1, Math.round((Date.now() / 1000 - since) / 60))} min` : "";

function toast(msg) { const t = $("toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 4200); }

// One /api/state request at a time (code review 2026-11-20): a slow answer (Slack reads) no longer piles up
// requests from the 5-second timer.
let loading = false;
async function load() {
  if (loading) return;
  loading = true;
  try {
    const r = await fetch("/api/state", {cache: "no-store"});
    data = await r.json();
    CFG = data.cfg || CFG;
    applyWorkflow();
    $("offline").style.display = "none";
    render();
    renderTabs();
    syncRunPick();
    syncReplyHint();
  } catch (e) { $("offline").style.display = "block"; }
  finally { loading = false; }
}

// Model and effort of the next run (owner request 2026-11-17). Filled once; a select being used is left alone.
function pickOf(s, which) { return which === "run" ? s : (s[which] || s); }
// The ticket(s) running now: the main run, and a conflict fix running alongside it (owner 2026-11-25).
const isRunning = (key) => !!data && !!key && [data.running, data.running_side].some((r) => r && r.key === key);
// While this chat's ticket is running, what you type is an add-on the session gets after its next step (2026-11-19).
function syncReplyHint() {
  const key = ($("logtitle").textContent || "").split(" · ")[0];
  $("quick").hidden = !threadChat || !threadRef || isRunning(key);
  if (!threadChat || !data) return;
  const working = isRunning(key);
  $("replytext").placeholder = working ? "Add info while Claude works — it gets it after its next step…" : "Reply to Claude…";
  $("replyhint").textContent = working ? "↩ to send · ⇧↩ new line · added to the running session without stopping it"
                                       : "↩ to send · ⇧↩ new line · continues this ticket's Claude session";
  const qc = branchPrompts(data.tickets.find((t) => t.key === key)), el = $("qconflict");
  if (el.dataset.sig !== qc) { el.dataset.sig = qc; el.innerHTML = qc; }   // a refresh never swaps a button mid-click
}
// Quick prompts for the ticket's PR branch, first in the row; like the others they are sent on click, and they run
// alongside another ticket's run, in the ticket's own worktree (data-side, owner 2026-11-25):
// - merge conflicts: "What conflicts?" (answered, nothing changed); the fix is the card's "Resolve Now →"
//   in Conflict to fix (owner 2026-11-25: one button for it, not two);
// - a failed asset build: "Rebuild assets" (usually: the branch lacks main's front-end changes);
// - a main PR without conflicts: "Merge main" (the reviewer's "take main in").
function branchPrompts(t) {
  if (!t) return "";
  const all = [(t.pr || {}).main, ...((t.pr || {}).stg || [])].filter(Boolean);
  const btn = (label, q, bad = true) => `<button type="button"${bad ? ' class="bad"' : ""} data-side="1" data-q="${esc(q)}">${label}</button>`;
  const assets = all.filter((p) => p.assets && p.assets.state === "failed").map((p) => btn("Rebuild assets",
    `Rebuild the assets of PR #${p.number} (into ${p.base || "its base branch"}): its asset build failed (${p.assets.url || "see the PR's checks"}). ` +
    "Read why first. If it says the branch lacks the base branch's front-end changes, take the base branch in: in a git " +
    "worktree of the PR branch, not the main checkout, merge it into the PR branch (no rebase, no force-push), check only " +
    "what the merge touched (no full test suites), and push; the push rebuilds the assets. If it failed for another " +
    "reason, change nothing: tell me why and how you would fix it.")).join("");
  const main = (t.pr || {}).main;
  const merge = main && main.mergeable !== "CONFLICTING" ? btn("Merge main",
    `Merge main into PR #${main.number}'s branch: in a git worktree of the PR branch, not the main checkout, merge main ` +
    "into it (no rebase, no force-push), resolve any conflicts as the runbook says, check only what the merge touched " +
    "(lint, and the tests for conflicted code files; no full test suites), and push. Then tell me what came in and what you ran.", false) : "";
  return conflictPrompts(all) + assets + merge;
}
function conflictFixText(prs) {
  const which = prs.map((p) => `PR #${p.number} (into ${p.base || "its base branch"})`).join(" and ");
  return `Fix the merge conflicts in ${which}: in a git worktree of the PR branch, not the main checkout, merge the base branch into the PR branch (no rebase, no force-push), resolve them as the runbook says, check only what the merge touched (lint, and the tests for conflicted code files; no full test suites), and push. Then tell me what you resolved and what you ran.`;
}
async function conflictFix(key) {
  const t = data.tickets.find((x) => x.key === key);
  const prs = t ? [(t.pr || {}).main, ...((t.pr || {}).stg || [])].filter((p) => p && p.mergeable === "CONFLICTING") : [];
  if (!prs.length) return toast("The PR has no merge conflict now.");
  openThread(`${CFG.report}|${t.chat_ts || "new:" + key}`, `${key} · Chat with Claude`);   // the ticket's chat shows it
  await postReply(conflictFixText(prs), true);
}
function conflictPrompts(all) {
  const prs = all.filter((p) => p.mergeable === "CONFLICTING");
  if (!prs.length) return "";
  const which = prs.map((p) => `PR #${p.number} (into ${p.base || "its base branch"})`).join(" and ");
  const ask = `${which} ${prs.length > 1 ? "have" : "has"} merge conflicts. Which files conflict, and why (what changed on the base branch)? Don't change anything yet; tell me how you would resolve them.`;
  // data-side: it starts at once alongside another ticket's run (owner 2026-11-25)
  return `<button type="button" class="bad" data-side="1" data-q="${esc(ask)}">What conflicts?</button>`;
}
function syncRunPick() {
  const s = data && data.run_settings;
  $("runpick").hidden = !s;
  if (!s) return;
  for (const row of document.querySelectorAll(".pickrow")) {
    const [m, e] = row.querySelectorAll("select"), v = pickOf(s, row.dataset.pick);
    if (!m.options.length) m.innerHTML = Object.entries(s.models).map(([id, n]) => `<option value="${esc(id)}">${esc(n)}</option>`).join("");
    if (!e.options.length) e.innerHTML = s.efforts.map((x) => `<option value="${esc(x)}">${esc(x[0].toUpperCase() + x.slice(1))}</option>`).join("");
    if (document.activeElement !== m) m.value = v.model;
    if (document.activeElement !== e) e.value = v.effort;
  }
  const short = (v) => `${(s.models[v.model] || v.model).replace(/ [\d.]+$/, "")} ${v.effort}`;
  // The summary covers the ticket runs; the watch has its own row (Sonnet by default) and is left out of it.
  document.querySelector('.pickrow[data-pick="watch"]').hidden = !s.watch;
  const all = ["run", "fix", "review"].map((w) => short(pickOf(s, w)));
  $("runsum").textContent = all.every((x) => x === all[0]) ? `Models: ${all[0]}` : `Models: ${all.join(" / ")}`;
}
async function saveRunPick(row) {
  const [m, e] = row.querySelectorAll("select"), v = {model: m.value, effort: e.value};
  const body = row.dataset.pick === "run" ? v : {[row.dataset.pick]: v};
  try {
    const r = await fetch("/api/settings", {method: "POST", headers: {"Content-Type": "application/json", "X-Dashboard": "1"},
      body: JSON.stringify(body)});
    const j = await r.json();
    toast(j.message || (j.ok ? "Saved" : "Not saved"));
    if (j.run_settings && data) { data.run_settings = j.run_settings; syncRunPick(); }
  } catch (e) { toast("The bridge is not answering"); }
}
document.querySelectorAll(".pickrow select").forEach((el) => el.addEventListener("change", () => { el.blur(); saveRunPick(el.closest(".pickrow")); }));
document.addEventListener("click", (ev) => { const d = $("runpick"); if (d.open && !d.contains(ev.target)) d.open = false; });
function modelTag(r) {
  if (!r || !r.model) return "";
  const n = ((data && data.run_settings && data.run_settings.models) || {})[r.model] || r.model.replace(/^claude-/, "");
  return `<span class="modeltag" title="Model and effort of this run">${esc(n)}${r.effort ? " · " + esc(r.effort) : ""}</span>`;
}

function health() {
  const h = data.health, w = h.watch || {}, now = data.now;
  const pollOk = h.jira_poll_age != null && h.jira_poll_age < 300;
  const watchAge = w.last_run ? now - w.last_run : null;
  const watchOk = watchAge != null && watchAge < 2 * 3600 && !/fail/i.test(w.last_line || "");
  const pill = (r, label) => `<span class="pill"><span class="dot run"></span>${label}: <b>${esc(r.key)}</b> ${esc(r.kind || "")} · ${mins(r.since)}</span>`;
  const side = data.running_side && data.running_side.key ? pill(data.running_side, "Alongside") : "";
  const run = (data.running.key ? pill(data.running, "Running") : `<span class="pill"><span class="dot"></span>Nothing running</span>`) + side;
  const q = data.queued.length ? `<span class="pill">Queued: ${esc(data.queued.join(", "))}</span>` : "";
  $("health").innerHTML = `<span class="group">` +
    `<span class="pill bridge" title="Slack bridge"><span class="dot ok"></span>Bridge</span>` +
    `<span class="pill" title="New-ticket check every minute"><span class="dot ${pollOk ? "ok" : "bad"}"></span>Jira check <span class="num">${ago(h.jira_poll_age)}</span></span>` +
    `<span class="pill" title="${esc(w.last_line || "")}"><span class="dot ${watchOk ? "ok" : "warn"}"></span>Feedback watch <span class="num">${ago(watchAge)}</span></span>` +
    run + q + `</span>`;
  sidebar();
}

function prChips(pr) {
  const out = [];
  if (pr && pr.main) {
    const m = pr.main.mergeable, cls = m === "MERGEABLE" ? "ok" : m === "CONFLICTING" ? "bad" : "";
    const word = m === "MERGEABLE" ? "mergeable" : m === "CONFLICTING" ? "conflicts" : "checking";
    out.push(`<a class="chip ${cls}" href="${esc(pr.main.url)}" target="_blank">PR #${pr.main.number} · ${word}</a>`);
  } else out.push(`<span class="chip">no PR</span>`);
  for (const s of (pr && pr.stg) || []) out.push(`<a class="chip info" href="${esc(s.url)}" target="_blank">stg #${s.number} open</a>`);
  // A failed asset build (owner 2026-11-25): it blocks the merge, and the chat's "Rebuild assets" fixes it.
  for (const p of [pr && pr.main, ...((pr && pr.stg) || [])].filter((p) => p && p.assets && p.assets.state === "failed"))
    out.push(`<a class="chip bad" href="${esc(p.assets.url || p.url)}" target="_blank" title="The PR's asset build failed: open the run. Chat → Rebuild assets fixes it.">⚠ #${p.number} assets build failed</a>`);
  return out.join("");
}

function runLine(t) {
  const r = t.run;
  if (r.state === "running") return [`<b>${r.mode === "compact" ? "🗜" : "🛠"} ${({fix: "Fixing", reply: "Continuing", review: "Reviewing", chat: "Chatting", compact: "Compacting the session"})[r.mode] || "Migrating"}</b>`, mins(r.since), r.session_name && `session ${esc(r.session_name)}`].filter(Boolean).join(" · ") + (r.model ? " " + modelTag(r) : "");
  if (r.state === "queued") return `<b>⏳ Queued</b>`;
  if (!r.state) return t.status === ST().open ? "Not started" : "No Claude run yet";
  const icon = {finished: "✅", stopped: "⏹", failed: "⚠️", ended_turn: "ℹ️", queued: "⏳"}[r.state] || "•";
  return `${icon} Last ${({fix: "fix", reply: "run", review: "review", chat: "chat"})[r.mode] || "migrate"}: ${esc(r.state === "ended_turn" ? "ended without finishing" : r.state)}${r.when ? " · " + esc(r.when) : ""}${r.model ? " " + modelTag(r) : ""}`;
}

// Fix Now → / Review → sit at the bottom-right of the card, rightmost, after Chat (owner request 2026-11-16). Fix Now only when
// there is something to fix; a ticket waiting for the reviewer gets Review → (a report-only re-review of its PR).
function mainAction(t) {
  const r = t.run, k = esc(t.key);
  if (["running", "queued"].includes(r.state)) return "";
  const resumable = r.session_id && ["stopped", "failed", "ended_turn"].includes(r.state);
  // Resume → is rightmost too (owner request 2026-11-17).
  // Only a run that was cut off — Stop, the app closing, an error — offers Resume →; one that ends its turn early
  // is resumed by the bridge itself (owner request 2026-11-17).
  const resume = r.session_id && ["stopped", "failed"].includes(r.state)
    ? `<button class="primary" data-act="resume" data-key="${k}" title="Continue this session where it stopped">Resume →</button>` : "";
  if (t.feedback.open)
    return `<button class="${resume ? "" : "primary"}" data-act="fix" data-key="${k}">Fix Now →</button>${resume}`;
  if (t._conflict && conflicted(t))   // Conflict to fix: the conflict prompt, never Fix Now
    return `<button class="${resume ? "" : "primary"}" data-conflictfix="${k}" title="Merge main in the ticket's worktree, resolve, push — alongside any running ticket">Resolve Now →</button>${resume}`;
  if (resume) return resume;
  // A finished run waiting on the owner's merge / reply has nothing to fix, even while Jira still says Dev In Progress
  // (e.g. Jira refused In Review until its "v2 PR" field is filled) — owner report 2026-11-17.
  if (t.status === ST().dev && !resumable && !(t.reply && t.reply.needed))
    return `<button class="primary" data-act="fix" data-key="${k}">Fix Now →</button>`;
  if (t.status === ST().review)
    return `<button class="soft" data-act="review" data-key="${k}" title="Claude re-reviews the PR and reports findings — no code, Jira or Slack changes">Review →</button>`;
  // Migrate → is a main action too, so it lines up with Review → / Resume → on the right (owner 2026-11-20).
  if (!resumable && t.status === ST().open && !isHidden(t))
    return `<button class="primary" data-act="migrate" data-key="${k}">Migrate →</button>`;
  return "";
}

function buttons(t) {
  const r = t.run, k = esc(t.key), b = [];
  if (r.state === "running") {
    b.push(`<button class="primary" data-log="${esc(r.session_id)}" data-name="${esc(r.session_name || t.key)}">Watch live</button>`);
    b.push(`<button class="danger" data-act="stop" data-key="${k}">Stop</button>`);
  } else if (r.state === "queued") {
    b.push(`<button class="danger" data-act="stop" data-key="${k}">Remove from queue</button>`);
  } else {
    // Resume →, Migrate → and the other main actions sit at the bottom-right, in mainAction.
    if (r.session_id) {
      b.push(`<button data-log="${esc(r.session_id)}" data-name="${esc(r.session_name || t.key)}" title="Open this run's session transcript">History</button>`);
    }
  }
  return b.join("");
}

// When the ticket went to the reviewer: the owner's last message in its review thread, or the List's "Sent for review"
// time when the thread can't be read. null when neither says (the ticket still waits, just without a clock).
const waitSince = (t) => (t.review_turn && t.review_turn.owner_last && parseFloat(t.review_turn.ts)) || null;
function waitAge(ts) {
  const h = (Date.now() / 1000 - parseFloat(ts)) / 3600;
  return h < 1 ? `${Math.max(1, Math.round(h * 60))} min` : h < 48 ? `${Math.round(h)} h` : `${Math.round(h / 24)} days`;
}
// The team review thread opens from the card's own lines (owner request 2026-11-16: no Thread button).
const threadAttrs = (t) => t.review_thread
  ? `data-thread="${esc(t.review_thread.channel)}|${esc(t.review_thread.ts)}" data-name="${esc(t.key)} review thread"` : "";
function replyTodo(t) {
  if (!(t.reply && t.reply.needed))   // in Fixed: reply only because the reviewer's conflict ask is fixed (2026-11-25)
    return `✅ Conflict fixed → PR #${t.pr.main.number} merges cleanly; ${t.review_thread
      ? `<button class="rowlink act" ${threadAttrs(t)} title="Open the review thread">reply in the review thread</button>`
      : "reply in the review thread"}`;
  const stg = (t.pr && t.pr.stg) || [];
  const what = t.reply.mode === "fix" ? "Fixed" : "Migrated";
  const merge = stg.length ? `merge + deploy staging PR ${stg.map((s) => `<a href="${esc(s.url)}" target="_blank">#${s.number}</a>`).join(", ")}, then ` : "";
  const reply = t.reply.has_thread && t.review_thread
    ? `<button class="rowlink act" ${threadAttrs(t)} title="Open the review thread">reply in the review thread</button>`
    : `post the review request in ${esc(CFG.reviewName || "the review channel")}`;
  return `✅ ${what} ${esc(t.reply.finished)} → ${merge}${reply}`;
}
// A conflict ask's chip opens the reviewer's message, or the thread (owner 2026-11-25).
function conflictChip(t) {
  const title = 'title="The reviewer asked to fix the merge conflict. Opens the review thread."';
  return t.review_thread ? `<button class="chip bad" ${threadAttrs(t)} ${title}>💬 Conflict ask</button>`
    : `<a class="chip bad" href="${esc(t.conflict.link || "#")}" target="_blank" ${title}>💬 Conflict ask</a>`;
}
function card(t) {
  const r = t.run;
  const fb = !t.feedback.open ? ""
    : t.feedback.unchecked ? `<button class="chip fb" ${threadAttrs(t)} title="The reviewer posted after the last feedback watch. The watch starts right away and confirms whether it asks for changes (it clears this if not). Opens the review thread.">💬 New reviewer reply${t.feedback.unchecked_time ? " " + esc(t.feedback.unchecked_time.split(" ").pop()) : ""} · ${t.feedback.checking ? "checking now…" : "not checked yet"}</button>`
    : t.review_thread ? `<button class="chip fb" ${threadAttrs(t)} title="The reviewer requested changes; no reply or fix from us yet. Opens the review thread.">💬 Feedback Review</button>`
    : `<a class="chip fb" href="${esc(t.feedback.link)}" target="_blank" title="The reviewer requested changes; no reply or fix from us yet. Opens the reviewer's message.">💬 Feedback Review</a>`;
  // A thread with no line of its own on the card (no feedback, no reply due, not waiting) gets a small chip.
  const thChip = t.review_thread && !t.feedback.open && !t._reply && !t._wait
    ? `<button class="chip info" ${threadAttrs(t)} title="Open the review thread">💬 Thread</button>`
    : !t.review_thread && t.related_thread   // no thread of its own: it was discussed in another ticket's thread
    ? `<button class="chip info" data-thread="${esc(t.related_thread.channel)}|${esc(t.related_thread.ts)}" data-name="${esc(t.related_thread.owner)} review thread"
        title="This ticket has no review thread of its own; it is discussed in ${esc(t.related_thread.owner)}'s thread">💬 In ${esc(t.related_thread.owner)}'s thread</button>` : "";
  const w = t.elsewhere.find((s) => s.state === "working") || t.elsewhere[0];
  const elsewhere = w
    ? `<div class="line">🖥 ${w.state === "working" ? "<b>Working now</b> in your Claude session" : "Recent Claude session"}: <b>${esc(w.title)}</b> · last activity ${esc(w.age_min)} min ago</div>` : "";
  // An OPEN ticket Claude has never touched only offers Migrate →. One with a run — e.g. a migration still at
  // its plan step, when Jira stays OPEN until the go — keeps Chat (owner 2026-11-20: NJ-102794 had no Chat).
  const untouched = t.status === ST().open && !r.state && !(t.convs || []).length;
  // Clicking the card itself (not a button or link) opens the team review thread (owner request 2026-11-16).
  return `<div class="card ${r.state === "running" ? "running" : ""}${t.key === openKey ? " open" : ""}" data-key="${esc(t.key)}" ${threadAttrs(t)} ${t.review_thread ? 'title="Click to open the review thread"' : ""}><div class="body">
    <div class="title"><a class="key" href="${CFG.jira}/browse/${esc(t.key)}" target="_blank">${esc(t.key)}</a>
      <span class="summary" title="${esc(t.jira_summary || t.summary)}">${esc(t.summary)}</span></div>
    <div class="meta">${t._moved ? `<span class="chip status">Jira: ${esc(t.status)}</span>` : ""}${prChips(t.pr)}${fb}${t._conflict && t.conflict && t.conflict.open ? conflictChip(t) : thChip}${
      t._wait && waitSince(t) ? `<span class="chip wait" title="Waiting for the reviewer since ${esc(t.review_turn.time)}">${IC.clock}${waitAge(waitSince(t))} wait</span>` : ""}</div>
    ${r.state === "running" && r.activity ? `<div class="activity">${esc(r.activity)}</div>` : ""}
    ${r.state !== "running" && w && w.state === "working" && w.last ? `<div class="activity">${esc(w.last)}</div>` : ""}
    <div class="details">
      <div><span class="i ${r.state === "finished" ? "ok" : ""}">${({finished: IC.check, running: IC.play, queued: IC.dots, stopped: IC.stop, failed: IC.alert, ended_turn: IC.question})[r.state] || IC.dots}</span><span>${runLine(t).replace(/^(✅|⏹|⚠️|ℹ️|⏳|•)\s*/, "")}</span></div>
      ${t._reply ? `<div class="todo"><span class="i ok">${IC.check}</span><span>${replyTodo(t).replace(/^✅\s*/, "")}</span></div>` : ""}
      ${t._conflict ? `<div class="todo conflict"><span class="i">${IC.alert}</span><span>${conflicted(t)
        ? (t.conflict && t.conflict.open ? "The reviewer asks to fix the merge conflict: Resolve Now → (no Fix Now needed)"
          : `PR #${t.pr.main.number} conflicts with main, so it can't merge: Resolve Now →`)
        : "GitHub is still checking whether the PR merges cleanly"}</span></div>` : ""}
      ${t._wait ? `<div class="waitline"><span class="i">${IC.hourglass}</span>${t.review_thread ? `<button class="rowlink" ${threadAttrs(t)} title="Open the review thread">` : "<span>"}${
        waitSince(t) ? `Sent for review ${esc(t.review_turn.time)} · waiting ${waitAge(waitSince(t))} for the reviewer` : `${esc(ST().review)} · waiting for the reviewer`}${t.review_thread ? "</button>" : "</span>"}</div>` : ""}
      ${t.next_step ? `<div class="next"><span class="i">${IC.arrow}</span><span><span class="lbl">Next:</span> <span class="val">${esc(t.next_step)}</span></span></div>` : ""}
    </div>
    ${elsewhere}
    </div>
    <div class="side"><div class="actions">${buttons(t)}</div><div class="bottom">${untouched ? "" : `
      <button class="chat" data-thread="${CFG.report}|${esc(t.chat_ts || "new:" + t.key)}" data-name="${esc(t.key)} · Chat with Claude"
        title="${t.chat_ts ? "Your chat with Claude about this ticket" : "Start a chat with Claude about this ticket"}">Chat</button>`}${mainAction(t)}</div></div>
  </div>`;
}

let tab = (() => { try { return localStorage.getItem("ct-tab") || "board"; } catch (e) { return "board"; } })();
let table = null, sortBy = {col: "status", dir: 1};
const TCOLS = [["key", "Ticket"], ["summary", "Summary"], ["status", "Jira status"], ["kind", "Kind"], ["pr", "PR"],
  ["pr_state", "PR state"], ["deadline", "Deadline"], ["waiting", "Waiting on"], ["next", "Next step"], ["thread", "Thread"]];
async function loadTable() {
  try { table = await (await fetch("/api/table", {cache: "no-store"})).json(); } catch (e) { table = {error: "offline", rows: []}; }
  if (tab === "table") render();
}
function tchip(v, kind) {
  const cls = kind === "pr_state" ? ({Mergeable: "ok", Conflicts: "bad", Checking: "warn"}[v] || "")
    : kind === "waiting" ? (v === CFG.reviewer ? "info" : {You: "warn", "New API (blocked)": "bad"}[v] || "")
    : kind === "status" ? (STATUS_TONE[v] || "") : "";
  return v ? `<span class="tchip ${cls}">${esc(v)}</span>` : "";
}
function renderTable() {
  if (!table) { $("list").innerHTML = '<div class="empty">Loading the table…</div>'; loadTable(); return; }
  if (table.error) { $("list").innerHTML = `<div class="note">Could not read the Slack List: <code>${esc(table.error)}</code></div>`; return; }
  const q = $("q").value.trim().toLowerCase(), hide = $("hideSkip").checked;
  const today = new Date().toISOString().slice(0, 10);
  let rows = table.rows.filter((r) => (!hide || r.kind !== "Next.js") &&
    (!q || `${r.key} ${r.summary} ${r.next} ${r.status} ${r.waiting}`.toLowerCase().includes(q)));
  const val = (r, c) => c === "status" ? (STATUS_RANK[r.status] ?? 9) : c === "deadline" ? (r.deadline || "9999") : c === "thread" ? (r.thread ? 0 : 1) : (r[c] || "").toString().toLowerCase();
  rows.sort((a, b) => { const x = val(a, sortBy.col), y = val(b, sortBy.col); return (x > y ? 1 : x < y ? -1 : a.key.localeCompare(b.key)) * sortBy.dir; });
  const head = TCOLS.map(([c, label]) => `<th class="c-${c}" data-sort="${c}" scope="col">${label}${sortBy.col === c ? `<span class="arrow">${sortBy.dir > 0 ? "▲" : "▼"}</span>` : ""}</th>`).join("");
  const body = rows.map((r) => `<tr data-key="${esc(r.key)}">
    <td class="key c-key">${r.ticket_url ? `<a href="${esc(r.ticket_url)}" target="_blank">${esc(r.key)}</a>` : esc(r.key)}</td>
    <td class="c-summary">${esc(r.summary)}</td>
    <td class="c-status">${tchip(r.status, "status")}</td>
    <td class="c-kind">${tchip(r.kind)}</td>
    <td class="c-pr">${r.pr_url ? `<a class="${({Mergeable: "ok", Conflicts: "bad", Checking: "warn"})[r.pr_state] || ""}" href="${esc(r.pr_url)}" target="_blank" title="${esc(r.pr_state)}">${esc(r.pr || "PR")}</a>` : esc(r.pr)}</td>
    <td class="c-pr_state">${tchip(r.pr_state, "pr_state")}</td>
    <td class="c-deadline ${r.deadline && r.deadline < today && ![ST().review, ST().released].includes(r.status) ? "overdue" : ""}">${esc(r.deadline)}</td>
    <td class="c-waiting">${tchip(r.waiting, "waiting")}</td>
    <td class="next c-next">${mrkdwn(r.next)}</td>
    <td class="c-thread">${r.thread ? `<button data-thread="${esc(r.thread.channel)}|${esc(r.thread.ts)}" data-name="${esc(r.key)} review thread">Open</button>` : `<span class="tchip">${esc(r.thread_text || "—")}</span>`}</td>
  </tr>`).join("");
  $("list").innerHTML = `<div class="tbl-meta">${rows.length} of ${table.rows.length} tickets · the Slack List “My Jira tickets”, kept in sync by the automation · click a column to sort</div>
    <div class="tbl-wrap"><table class="tickets"><thead><tr>${head}</tr></thead><tbody>${body || '<tr><td colspan="10" class="empty">No tickets match.</td></tr>'}</tbody></table></div>`;
}
// Each menu (Board, Table, Report) remembers where you left it; coming back puts that ticket where it was
// (owner request 2026-11-16). Clicking the menu you are already on still goes to the top.
const scrollMem = {};
let pendingScroll = null;
function setTab(name) {
  const same = name === tab;
  if (!same) scrollMem[tab] = {a: listAnchor(), top: $("list").scrollTop};
  tab = name; try { localStorage.setItem("ct-tab", name); } catch (e) {}
  document.querySelectorAll("#side .menu button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  pendingScroll = same ? null : scrollMem[name] || null;
  $("list").scrollTop = 0; lastAnchor = null;
  render();
  if (name === "table") loadTable();
  if (name === "today") loadToday();
}
function applyScrollMem(m) {
  const L = $("list");
  L.scrollTop = m.top;
  const c = m.a && L.querySelector(`[data-key="${CSS.escape(m.a.key)}"]`);
  if (c) L.scrollTop += (c.getBoundingClientRect().top - L.getBoundingClientRect().top) - m.a.off;
  lastAnchor = listAnchor();
}
// ---- Today: what we did on the day, as the lines for the daily report channel (owner request 2026-11-16)
const pad2 = (n) => String(n).padStart(2, "0");
const localDay = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
let todayDate = localDay(new Date()), todayData = null, todayLoading = false;
async function loadToday() {
  todayLoading = true; if (tab === "today") render();
  const want = todayDate;   // a slower answer for a day you have already left must not replace the current one
  let got;
  try { got = await (await fetch(`/api/today?date=${want}`, {cache: "no-store"})).json(); }
  catch (e) { got = {error: "offline"}; }
  if (want !== todayDate) return;
  todayData = got;
  todayLoading = false; if (tab === "today") render();
}
function shiftDay(n) {
  if (n === 0) { todayDate = localDay(new Date()); todayData = null; $("list").scrollTop = 0; lastAnchor = null; loadToday(); return; }
  const d = new Date(todayDate + "T12:00:00"); d.setDate(d.getDate() + n);
  if (localDay(d) > localDay(new Date())) return;
  todayDate = localDay(d); todayData = null; $("list").scrollTop = 0; lastAnchor = null; loadToday();
}
function renderToday() {
  const isToday = todayDate === localDay(new Date());
  // ‹ title ›, with the title in a fixed-width slot so the arrows never move when the date changes.
  const nav = `<div class="today-head"><button class="dayarrow" data-day="-1" aria-label="Previous day">‹</button>
    <h2>Report · ${esc(todayData && todayData.label || todayDate)}</h2>
    <button class="dayarrow" data-day="1" aria-label="Next day" ${isToday ? "disabled" : ""}>›</button>
    ${isToday ? "" : '<button class="soft" data-day="0">Back to today</button>'}</div>`;
  if (!todayData) { $("list").innerHTML = nav + '<div class="empty">Collecting the day — runs, Jira and PRs…</div>'; return; }
  if (todayData.error) { $("list").innerHTML = nav + `<div class="note">Could not build the report: <code>${esc(todayData.error)}</code></div>`; return; }
  const lines = todayData.lines || [];
  const sent = todayData.posted ? `Sent to ${CFG.reportName} ✓` : isToday ? `Goes to ${CFG.reportName} at 17:40 (weekdays)` : "";
  const rep = `<section class="rep"><div class="rep-top"><b>${esc(todayData.day)} REPORT</b>
      <span class="muted">Paste into today's thread in ${esc(CFG.dailyName || "")}${sent ? " · " + esc(sent) : ""}</span>
      <button class="primary" id="copyrep" ${lines.length ? "" : "disabled"}>Copy</button></div>
    <pre id="replines">${lines.length ? esc(lines.join("\n")) : isToday ? "Nothing to report yet." : "Nothing to report for this day."}</pre></section>`;
  const errs = (todayData.errors || []).length ? `<div class="note">Some sources could not be read: ${esc(todayData.errors.join(", "))}</div>` : "";
  const cards = todayData.tickets.map((r) => `<div class="card day" data-key="${esc(r.key)}"><div class="body">
      <div class="title"><a class="key" href="${CFG.jira}/browse/${esc(r.key)}" target="_blank">${esc(r.key)}</a>
        <span class="summary" title="${esc(r.summary)}">${esc(r.summary)}</span></div>
      <div class="meta">${r.label ? `<span class="chip ok">${esc(r.label)}</span>` : '<span class="chip">not in the report</span>'}${r.status ? `<span class="chip status">Jira: ${esc(r.status)}</span>` : ""}</div>
      <ul class="events">${r.events.map((e) => `<li><span class="t">${esc(e.time)}</span><span>${esc(e.icon)}</span><span>${mrkdwn(e.text)}</span></li>`).join("")}</ul>
    </div></div>`).join("");
  $("list").innerHTML = nav + statCards() + rep + errs + (cards ? `<h2 class="day-sub">What happened · ${todayData.tickets.length} tickets</h2>` + cards
    : '<div class="empty">No activity on your tickets this day.</div>');
}
// Stat cards above the daily report (owner 2026-11-20), all from live data: runs over 7 days, the run model,
// how long tickets have waited on the reviewer, and PRs with merge conflicts.
function statCards() {
  if (!data) return "";
  const st = data.stats || {}, rs = data.run_settings || {}, models = rs.models || {};
  const {rows, c} = visibleClassified(), waits = c.waitingReview.filter(waitSince).map((t) => (Date.now() / 1000 - waitSince(t)) / 3600);
  const avg = waits.length ? waits.reduce((a, b) => a + b, 0) / waits.length : 0, longest = waits.length ? Math.max(...waits) : 0;
  const bad = rows.filter(conflicted);
  const by = st.by_mode || {}, parts = ["fix", "migrate", "review"].filter((m) => by[m]).map((m) => `${by[m]} ${m}`).join(" · ");
  const pct = st.runs ? Math.round(st.finished / st.runs * 100) : 0;
  const card = (l, v, cls, sub, subcls = "") => `<div class="stat"><div class="l">${l}</div><div class="v ${cls}">${v}</div><div class="s ${subcls}">${sub}</div></div>`;
  return `<div class="stats">${
    card(`Claude runs (${st.days || 7} days)`, st.runs ?? "–", "", st.runs ? `${pct}% finished · ${esc(parts)}` : "none yet", st.runs && pct === 100 ? "ok" : "")}${
    card("Run model", esc(models[rs.model] || rs.model || "–"), "accent", `${esc(rs.effort || "")} effort · review ${esc((models[(rs.review || {}).model] || "").split(" ")[0])} ${esc((rs.review || {}).effort || "")}`)}${
    card("Waiting on the reviewer", waits.length ? `${avg.toFixed(1)}h` : "–", "info", waits.length ? `average of ${waits.length} · longest ${Math.round(longest)}h · ${esc(CFG.reviewerCalled || CFG.reviewer || "")}` : "nothing waiting")}${
    card("PRs with conflicts", `${bad.length} PR${bad.length === 1 ? "" : "s"}`, bad.length ? "bad" : "ok", bad.length ? bad.map((t) => "#" + t.pr.main.number).join(", ") : "every PR merges cleanly")}</div>`;
}
async function copyReport() {
  const text = (todayData && todayData.lines || []).join("\n");
  try { await navigator.clipboard.writeText(text); }
  catch (e) { const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
  toast("Copied — paste it in the report thread");
}

// Every view (Board, Table, Report) keeps the ticket you are looking at in place when it re-renders.
function render() {
  const a = pendingScroll ? null : listAnchor();
  renderView();
  // The remembered spot is applied once the view has its tickets (Table / Report may still be loading).
  if (pendingScroll && $("list").querySelector("[data-key]")) { applyScrollMem(pendingScroll); pendingScroll = null; }
  else restoreAnchor(a);
}
// The board's buckets (also counted in the sidebar). Same rules as before the 2026-11-20 redesign.
function classify(rows) {
  const isLive = (t) => ["running", "queued"].includes(t.run.state) || t.elsewhere.some((s) => s.state === "working");
  // Conflict to fix (owner 2026-11-25): a PR into main that conflicts, asked about or not, can't merge until it is
  // fixed; one the reviewer asked about stays here until GitHub says it merges cleanly. Once it does, an asked one
  // needs your reply ("Fixed: reply"); an unasked one simply goes back to waiting for the reviewer.
  const asked = (t) => !!(t.conflict && t.conflict.open);
  const clean = (t) => !!(t.pr && t.pr.main && t.pr.main.mergeable === "MERGEABLE");
  // A Blocked ticket's conflict waits until it is unblocked: it stays in its Blocked group (owner 2026-11-25).
  const conflictAsk = (t) => !isLive(t) && !t.feedback.open && t.status !== ST().blocked
    && (conflicted(t) || (asked(t) && !clean(t)));
  const conflictFixed = (t) => !isLive(t) && !t.feedback.open && asked(t) && clean(t);
  const replyDue = (t) => !isLive(t) && !t.feedback.open && !conflictAsk(t) && ((t.reply && t.reply.needed) || conflictFixed(t));
  // Every Jira In Review ticket that isn't being worked on, needing changes or needing a reply waits on the reviewer
  // (owner 2026-11-23: no separate "In Review" group, also when the review thread can't be read).
  const reviewWait = (t) => t.status === ST().review && !isLive(t) && !t.feedback.open && !conflictAsk(t) && !replyDue(t);
  return {isLive, conflictAsk, replyDue, reviewWait,
    live: rows.filter(isLive), waiting: rows.filter((t) => !isLive(t) && t.feedback.open),
    conflicts: rows.filter(conflictAsk), toReply: rows.filter(replyDue), waitingReview: rows.filter(reviewWait)};
}
const conflicted = (t) => t.pr && t.pr.main && t.pr.main.mergeable === "CONFLICTING";
// The visible tickets and their buckets, worked out once per poll and shared by the sidebar and the stat cards.
let visMemo = null;
function visibleClassified() {
  const hide = $("hideSkip").checked;
  if (!visMemo || visMemo.data !== data || visMemo.hide !== hide) {
    const rows = visibleTickets();
    visMemo = {data, hide, rows, c: classify(rows)};
  }
  return visMemo;
}
// A ticket whose summary holds the workflow's hide marker (e.g. "[NEXTJS]"): hidden by the "Hide …" toggle and never
// offered Migrate →.
const isHidden = (t) => !!CFG.hideMarker && (t.jira_summary || t.summary || "").toUpperCase().includes(CFG.hideMarker.toUpperCase());
function visibleTickets() {
  const hide = $("hideSkip").checked;
  return (data ? data.tickets : []).filter((t) => !hide || !isHidden(t));
}
function sidebar() {
  if (!data) return;
  const {rows, c} = visibleClassified();
  const withPr = rows.filter((t) => t.pr && t.pr.main), ok = withPr.filter((t) => t.pr.main.mergeable === "MERGEABLE").length;
  const bad = withPr.filter(conflicted).length, pct = withPr.length ? Math.round(ok / withPr.length * 100) : 0;
  $("cnt-board").textContent = c.live.length + c.waiting.length + c.conflicts.length + c.toReply.length + c.waitingReview.length || "";
  $("cnt-table").textContent = rows.length;
  // One line per status, in board order (owner 2026-11-20); a click jumps to that section on the board. The review
  // status has no line (owner 2026-11-24): its tickets are all in the sections above, under Waiting for review at
  // the least. Like "Other", it shows only if a ticket ever lands there.
  const rest = rows.filter((t) => !c.isLive(t) && !t.feedback.open && !c.conflictAsk(t) && !c.replyDue(t) && !c.reviewWait(t));
  const jiraCount = (st) => rest.filter((t) => (ORDER.includes(t.status) ? t.status : "Other") === st).length;
  const line = (sec, color, label, n, pulse) => `<button type="button" class="qline${n ? "" : " zero"}" data-goto-sec="${sec}">
      <span class="dot${pulse && n ? " run" : ""}" style="background:${color}"></span><span>${esc(label)}</span><b>${n}</b></button>`;
  $("qsummary").innerHTML = `<div class="sh">Queue summary <i>Live</i></div><div class="qlist">${
    line("sec-live", "var(--accent)", "In progress", c.live.length, true)}${
    line("sec-fb", "var(--accent)", "Action needed", c.waiting.length)}${
    line("sec-conflict", "var(--bad)", "Conflict to fix", c.conflicts.length)}${
    line("sec-done", "var(--ok)", "Reply needed", c.toReply.length)}${
    line("sec-wait", "var(--info)", "Waiting for review", c.waitingReview.length)}</div>
    <div class="qsub">By Jira status</div><div class="qlist">${
    [...ORDER, "Other"].map((st) => [st, jiraCount(st)]).filter(([st, n]) => n || (st !== "Other" && st !== ST().review))
      .map(([st, n]) => line(jiraSecId(st), st === ST().blocked ? "var(--bad)" : st === ST().open ? "var(--faint)" : "var(--muted)", st, n)).join("")}</div>
    <div class="qlist qfoot">${line("", "var(--bad)", "Merge conflicts", bad)}</div>
    <div class="ratio"><span>Mergeable ratio</span><i>${pct}% (${ok}/${withPr.length})</i></div>
    <div class="bar" role="img" aria-label="${ok} of ${withPr.length} PRs mergeable"><span style="width:${pct}%;background:var(--ok)"></span><span style="width:${withPr.length ? bad / withPr.length * 100 : 0}%;background:var(--bad)"></span></div>`;
}
function setCollapsed(id, on) {
  if (on === COLLAPSED.has(id)) return;
  on ? COLLAPSED.add(id) : COLLAPSED.delete(id);
  try { localStorage.setItem("ct-collapsed", JSON.stringify([...COLLAPSED])); } catch (e) {}
  const sec = $(id), btn = sec && sec.querySelector("h2 .collapse");   // no redraw: just this section
  if (sec) sec.classList.toggle("collapsed", on && !$("q").value.trim());   // as renderView: a search shows every match
  if (btn) {
    btn.setAttribute("aria-expanded", String(!on));
    btn.title = on ? "Expand" : "Collapse";
    btn.setAttribute("aria-label", btn.getAttribute("aria-label").replace(/^(Expand|Collapse)/, btn.title));
  }
}
document.addEventListener("click", (ev) => {
  const t = ev.target.closest("[data-collapse]");
  if (t) { setCollapsed(t.dataset.collapse, !COLLAPSED.has(t.dataset.collapse)); return; }
  const q = ev.target.closest("[data-goto-sec]");
  if (q && q.dataset.gotoSec) {
    const go = () => { setCollapsed(q.dataset.gotoSec, false); const el = $(q.dataset.gotoSec); if (el) el.scrollIntoView({block: "start", behavior: "smooth"}); };
    if (tab !== "board") { setTab("board"); setTimeout(go, 50); } else go();   // setTab also moves the menu highlight
    return;
  }
  const g = ev.target.closest("[data-goto]");
  if (g) { const el = document.querySelector(`.card[data-key="${g.dataset.goto}"]`); if (el && el.closest("section.collapsed")) setCollapsed(el.closest("section").id, false);
    if (el) { el.scrollIntoView({block: "center", behavior: "smooth"}); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1200); } }
});
document.addEventListener("keydown", (ev) => {
  if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "k") { ev.preventDefault(); $("q").focus(); $("q").select(); }
});
function renderView() {
  if (tab === "table") { if (data) health(); renderTable(); return; }
  if (tab === "today") { if (data) health(); renderToday(); return; }
  health();
  const q = $("q").value.trim().toLowerCase(), hide = $("hideSkip").checked;
  const rows = data.tickets.filter((t) =>
    (!hide || !isHidden(t)) &&
    (!q || (t.key + " " + t.summary).toLowerCase().includes(q)));
  const c = classify(rows), {isLive, conflictAsk, replyDue, reviewWait} = c;
  // 1) In progress: Claude is working on it now. 2) Feedback waiting: a reviewer asked for changes and nothing has
  // been done on our side yet ("Action needed: changes requested"). 3) Everything else, grouped by its Jira status.
  // In start order (owner 2026-11-25): the runs, first started on top; then the queued ones in queue order; then
  // tickets only worked on in another Claude session.
  const queuePos = (t) => { const i = (data.queued || []).findIndex((l) => l.split(" ")[0] === t.key); return i < 0 ? Infinity : i; };
  const liveRank = (t) => (t.run.state === "running" ? 0 : t.run.state === "queued" ? 1 : 2);
  const live = c.live.map((t) => ({...t, _moved: true}))
    .sort((a, b) => liveRank(a) - liveRank(b) || (a.run.since || Infinity) - (b.run.since || Infinity)
      || queuePos(a) - queuePos(b) || a.key.localeCompare(b.key));
  const waiting = c.waiting.map((t) => ({...t, _moved: true})).sort((a, b) => a.key.localeCompare(b.key));
  const conflicts = c.conflicts.map((t) => ({...t, _moved: true, _conflict: true})).sort((a, b) => a.key.localeCompare(b.key));
  const toReply = c.toReply.map((t) => ({...t, _moved: true, _reply: true})).sort((a, b) => a.key.localeCompare(b.key));
  // The reviewer's turn: the newest message in the review thread is ours.
  const waitingReview = c.waitingReview.map((t) => ({...t, _moved: true, _wait: true}))
    .sort((a, b) => (waitSince(a) || Infinity) - (waitSince(b) || Infinity));
  const groups = {};
  for (const t of rows.filter((x) => !isLive(x) && !x.feedback.open && !conflictAsk(x) && !replyDue(x) && !reviewWait(x))) (groups[ORDER.includes(t.status) ? t.status : "Other"] ||= []).push(t);
  const names = [...ORDER, "Other"].filter((g) => groups[g]);
  // The chevron at the right end of a section's header collapses it to that header (owner 2026-11-24).
  // While the filter box has text, every section shows its matches, collapsed or not.
  const isFolded = (id) => COLLAPSED.has(id) && !$("q").value.trim();
  const head = (ico, title, n, id) => `<h2>${ico}${esc(title)} <span class="n">${n}</span><button type="button" class="collapse"
    data-collapse="${esc(id)}" aria-expanded="${!isFolded(id)}" title="${isFolded(id) ? "Expand" : "Collapse"}"
    aria-label="${isFolded(id) ? "Expand" : "Collapse"} ${esc(title)}">${IC.chevron}</button></h2>`;
  const secCls = (id) => (isFolded(id) ? " collapsed" : "");
  // Every section has the same two states (owner 2026-11-20): empty = one compact dashed row with its message
  // (In progress adds "Queue next"), otherwise its header and cards.
  const section = (cls, id, ico, title, items, emptyText, extra = "") => items.length
    ? `<section class="${cls}${secCls(id)}" id="${id}">${head(ico, title, items.length, id)}${items.map(card).join("")}</section>`
    : `<section class="${cls} empty-sec" id="${id}"><h2>${ico}${esc(title)} <span class="n">0</span><span class="idle">· ${esc(emptyText)}</span>${extra}</h2></section>`;
  const nextUp = waiting[0] || toReply[0];
  const queueNext = !live.length && nextUp
    ? `<button class="rowlink queue-next" data-goto="${esc(nextUp.key)}" title="Jump to that card">Queue next: ${waiting[0] ? "fix" : "reply"} ${esc(nextUp.key)} →</button>` : "";
  const liveSection = section("live", "sec-live", `<span class="dot${live.length ? " run" : ""}"></span>`, "In progress", live,
    "Nothing is being worked on right now.", queueNext);
  const fbSection = section("fb", "sec-fb", `<span class="ico">${IC.alert}</span>`, "Action needed: changes requested", waiting,
    "No requested changes need action.");
  const conflictSection = section("conflict", "sec-conflict", `<span class="ico">${IC.alert}</span>`, "Conflict to fix", conflicts,
    "No merge conflicts to fix.");
  const doneSection = section("done", "sec-done", `<span class="ico">${IC.check}</span>`, "Fixed: reply to the reviewer", toReply,
    "No replies due — every finished fix has been answered.");
  const waitSection = section("wait", "sec-wait", `<span class="ico">${IC.clock}</span>`, "Waiting for review", waitingReview,
    "Nothing is waiting on the reviewer.");
  const jira = names.length
    ? '<div class="jira-label">By Jira status</div>' +
      names.map((g) => `<section id="${esc(jiraSecId(g))}" class="${secCls(jiraSecId(g)).trim()}">${head("", g, groups[g].length, jiraSecId(g))}${groups[g].sort((a, b) => a.key.localeCompare(b.key)).map(card).join("")}</section>`).join("")
    : "";
  const anchor = listAnchor();
  $("list").innerHTML = liveSection + fbSection + conflictSection + doneSection + waitSection +
    (jira || (live.length || waiting.length || conflicts.length || toReply.length || waitingReview.length ? "" : `<div class="empty">No tickets match.</div>`));
  restoreAnchor(anchor);
}

// Keep the card you are looking at in place. Opening / closing / resizing the side panel narrows or widens the list,
// cards above wrap to more or fewer lines, and with a fixed scrollTop the view used to slide (it looked like a jump up).
let lastAnchor = null;
// The same for the side panel (thread, chat, history): keep the message you are reading in place through refreshes
// and resizes; at the bottom, stay at the bottom.
function panelAnchor() {
  const B = $("logbody");
  if (!B || !B.clientHeight) return null;
  if (B.scrollHeight - B.scrollTop - B.clientHeight < 40) return {end: true};
  // Anchor on the first message that starts inside the panel (the one being read), not a half-hidden one above it.
  const box = B.getBoundingClientRect(), items = [...B.querySelectorAll(".msg, .c-row, .ln")];
  let i = items.findIndex((it) => { const r = it.getBoundingClientRect(); return r.top >= box.top - 2 && r.top < box.bottom; });
  if (i < 0) i = items.findIndex((it) => it.getBoundingClientRect().bottom > box.top + 4);
  return i < 0 ? null : {i, off: items[i].getBoundingClientRect().top - box.top};
}
function panelRestore(a) {
  if (!a) return;
  const B = $("logbody");
  if (a.end) { B.scrollTop = B.scrollHeight; return; }
  const it = B.querySelectorAll(".msg, .c-row, .ln")[a.i];
  if (!it) return;
  const d = (it.getBoundingClientRect().top - B.getBoundingClientRect().top) - a.off;
  if (Math.abs(d) > 1) B.scrollTop += d;
}
// Runs a layout change (panel open / close / resize) and puts the card you were looking at back where it was.
// Works without ResizeObserver, which a hidden or background window may not deliver.
function withAnchor(fn, key = openKey) {
  const a = cardAnchor(key) || listAnchor() || lastAnchor;
  fn();
  restoreAnchor(a);
}
// The card the panel belongs to (the one just clicked), when it is on screen: that is where the eye is.
function cardAnchor(key) {
  if (!key) return null;
  const L = $("list"), c = L.querySelector(`[data-key="${CSS.escape(key)}"]`);
  if (!c) return null;
  const lr = L.getBoundingClientRect(), r = c.getBoundingClientRect();
  if (r.bottom < lr.top || r.top > lr.bottom) return null;
  return {key, off: r.top - lr.top};
}
function listAnchor() {
  const L = $("list"), top = L.getBoundingClientRect().top;
  if (L.scrollTop < 2) return null;            // at the very top: stay at the top
  for (const c of L.querySelectorAll("[data-key]")) {
    const r = c.getBoundingClientRect();
    if (r.bottom > top + 4) return {key: c.dataset.key, off: r.top - top};
  }
  return null;
}
function restoreAnchor(a) {
  if (!a) return;
  const L = $("list"), c = L.querySelector(`[data-key="${CSS.escape(a.key)}"]`);
  if (!c) return;
  const delta = (c.getBoundingClientRect().top - L.getBoundingClientRect().top) - a.off;
  if (Math.abs(delta) > 1) L.scrollTop += delta;
  lastAnchor = listAnchor();
}
$("list").addEventListener("scroll", () => { lastAnchor = listAnchor(); }, {passive: true});
new ResizeObserver(() => restoreAnchor(lastAnchor)).observe($("list"));

// Attached files open in an in-app viewer (owner 2026-11-20), like Slack's preview. The frame is sandboxed without
// same-origin, so a report's own scripts (language toggles) run but cannot reach this app.
function openFile(id, name) {
  const url = `/api/file?id=${encodeURIComponent(id)}`;
  if (!/\.(html?|pdf|png|jpe?g|gif|webp|txt|md)$/i.test(name || "")) { window.open(url, "_blank"); return; }
  const v = $("viewer");
  $("vw-name").textContent = name || "Attachment";
  $("vw-open").onclick = () => window.open(url, "_blank");
  const frame = $("vw-frame");
  v.showModal();
  if (/\.html?$/i.test(name || "")) {
    // Fetched here and handed over as srcdoc: no separate frame request for the app's web view to block. The
    // sandbox attribute (no allow-same-origin) still keeps the report's scripts away from this app.
    frame.removeAttribute("src");
    frame.srcdoc = '<p style="font:14px -apple-system,sans-serif;color:#777;padding:24px">Loading…</p>';
    fetch(url, {cache: "no-store"}).then((r) => r.ok ? r.text() : Promise.reject(r.status))
      .then((html) => { if (v.open) frame.srcdoc = html; })
      .catch((e) => { frame.srcdoc = `<p style="font:14px -apple-system,sans-serif;color:#b3261e;padding:24px">Could not load the file (${String(e)}).</p>`; });
  } else {
    frame.removeAttribute("srcdoc"); frame.src = url;
  }
}
$("vw-close").onclick = () => $("viewer").close();
$("viewer").addEventListener("close", () => { const f = $("vw-frame"); f.removeAttribute("srcdoc"); f.src = "about:blank"; });
$("viewer").addEventListener("click", (e) => { if (e.target === $("viewer")) $("viewer").close(); });
// A styled confirm (owner 2026-11-20) instead of the system dialog. Resolves true on OK; Esc / Cancel / backdrop = false.
function askConfirm({kicker = "", title, key = "", text = "", quote = "", facts = [], ok = "OK", danger = false, pick = null}) {
  const d = $("confirm");
  if (d.open) return Promise.resolve(false);   // a double-click: the first dialog is already asking
  $("cf-kicker").textContent = kicker; $("cf-kicker").hidden = !kicker;
  $("cf-title").innerHTML = (key ? `<span class="key">${esc(key)}</span>` : "") + esc(title);
  $("cf-text").textContent = text; $("cf-text").hidden = !text;
  $("cf-quote").textContent = quote; $("cf-quote").hidden = !quote;
  $("cf-facts").innerHTML = facts.map((f) => `<span class="chip">${esc(f)}</span>`).join("");
  $("cf-ok").textContent = ok; $("cf-ok").className = danger ? "danger-solid" : "primary";
  // Model and effort for this run (owner 2026-11-20): start on the Models ▾ pick; a change applies to this run only.
  const rs = data && data.run_settings, def = rs && pick ? (pick === "run" ? rs : rs[pick]) : null;
  $("cf-pick").hidden = !def;
  if (def) {
    $("cf-model").innerHTML = Object.entries(rs.models).map(([id, n]) => `<option value="${esc(id)}">${esc(n)}</option>`).join("");
    $("cf-effort").innerHTML = rs.efforts.map((x) => `<option value="${esc(x)}">${esc(x[0].toUpperCase() + x.slice(1))}</option>`).join("");
    $("cf-model").value = def.model; $("cf-effort").value = def.effort;
    const note = () => {
      const changed = $("cf-model").value !== def.model || $("cf-effort").value !== def.effort;
      $("cf-pick").classList.toggle("changed", changed);
      $("cf-note").textContent = changed ? "This run only — your Models ▾ defaults stay as they are."
                                         : "Your Models ▾ default. Change it here for this run only.";
    };
    $("cf-model").onchange = $("cf-effort").onchange = note; note();
  }
  return new Promise((resolve) => {
    d.returnValue = "cancel";
    d.onclose = () => {
      if (d.returnValue !== "ok") return resolve(false);
      if (!def) return resolve(true);
      const m = $("cf-model").value, e = $("cf-effort").value;
      resolve(m === def.model && e === def.effort ? {} : {model: m, effort: e});
    };
    d.onclick = (e) => { if (e.target === d) d.close("cancel"); };   // a click on the backdrop
    d.showModal();
    (danger ? $("cf-cancel") : $("cf-ok")).focus();                 // Stop: Enter does not stop by accident
  });
}
function runFacts() {
  return data && data.running && data.running.key ? [`queues after ${data.running.key}`] : [];
}
const CONFIRM = {
  migrate: (key) => ({kicker: "Start a migration", title: "Migrate?", key, ok: "Migrate",
    text: `Claude reviews the ticket's scope first and posts a plan report, then migrates it. Jira moves to ${ST().dev} once the plan is a go.`, facts: runFacts(), pick: "run"}),
  fix: (key) => ({kicker: "Fix review feedback", title: "Fix now?", key, ok: "Fix now",
    text: "Claude fixes the newest reviewer feedback on the PR branch, re-reviews the change and pushes it.", facts: runFacts(), pick: "fix"}),
  review: (key) => ({kicker: "Report-only review", title: "Review?", key, ok: "Review",
    text: "Claude re-reviews the PR and reports what it finds. It changes no code, Jira or Slack.", facts: runFacts(), pick: "review"}),
  resume: (key) => ({kicker: "Continue a run", title: "Resume?", key, ok: "Resume",
    text: "Continues the same session from where it stopped, with everything it had done so far.", facts: runFacts(), pick: "run"}),
  stop: (key) => ({kicker: "Stop the run", title: "Stop?", key, ok: "Stop", danger: true,
    text: "The run stops now. Its work so far stays, and large sessions are compacted. Resume → continues it later."}),
};
async function act(action, key) {
  let choice = {};
  if (CONFIRM[action]) {
    const res = await askConfirm(CONFIRM[action](key));
    if (!res) return;
    if (typeof res === "object") choice = res;   // {model, effort} when changed for this run
  }
  try {
    const r = await fetch("/api/action", {method: "POST", headers: {"Content-Type": "application/json", "X-Dashboard": "1"},
      body: JSON.stringify({action, key, ...choice})});
    const j = await r.json();
    const how = choice.model ? ` · ${((data.run_settings || {}).models || {})[choice.model] || choice.model} ${choice.effort}` : "";
    toast((j.message || (j.ok ? "Done" : "Failed")) + (j.ok ? how : ""));
    // Migrate → / Fix Now → / Review → open the new run's chat in the side panel straight away (owner request 2026-11-17).
    if (j.ok && j.ts && ["migrate", "fix", "review", "resume"].includes(action)) openThread(`${CFG.report}|${j.ts}`, `${key} · Chat with Claude`);
  } catch (e) { toast("The bridge did not answer."); }
  setTimeout(load, 800);
}

// The card whose thread / chat / history is open in the side panel is highlighted (owner request 2026-11-16).
let openKey = null;
function markOpen(name) {
  const m = /NJ-\d+/.exec(name || "");
  openKey = m ? m[0] : null;
  document.querySelectorAll(".card[data-key]").forEach((c) => c.classList.toggle("open", c.dataset.key === openKey));
}
function openLog(sid, name) {
  markOpen(name);
  closeDecision(); clearReply();
  $("quick").hidden = true;
  threadRef = null; threadChat = false; $("logslack").hidden = true; $("lognew").hidden = true; $("ctabs").hidden = true; $("logbody").className = ""; $("reply").classList.remove("on");
  threadInfo(null);
  logSession = sid; logOffset = 0;
  $("logtitle").textContent = name + " · " + sid.slice(0, 8);
  $("logbody").innerHTML = "";
  withAnchor(() => $("main").classList.add("with-log"));
  clearInterval(logTimer); pollLog(); logTimer = setInterval(pollLog, 2000);
}
async function pollLog() {
  if (!logSession) return;
  try {
    const r = await fetch(`/api/log?session=${encodeURIComponent(logSession)}&offset=${logOffset}`, {cache: "no-store"});
    const j = await r.json();
    if (!j.lines) return;
    const body = $("logbody"), atEnd = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
    body.insertAdjacentHTML("beforeend", j.lines.map((l) =>
      `<div class="r"><span class="t">${esc(l.t)}</span><span class="${l.kind}">${l.kind === "tool" ? "→ " : ""}${esc(l.text)}</span></div>`).join(""));
    logOffset = j.offset;
    if (atEnd || logOffset && j.lines.length > 200) body.scrollTop = body.scrollHeight;
  } catch (e) {}
}
// Slack shortcodes this team uses, shown as Slack shows them (the text sent to Slack keeps the shortcode).
const EMOJI = {"man-bowing": "🙇‍♂️", "woman-bowing": "🙇‍♀️", "bow": "🙇", "sun": "☀️", "sunny": "☀️", "pray": "🙏",
  "arrow_forward": "▶️", "white_check_mark": "✅", "heavy_check_mark": "✔️", "warning": "⚠️", "thread": "🧵",
  "hammer_and_wrench": "🛠️", "speech_balloon": "💬", "clipboard": "📋", "thumbsup": "👍", "+1": "👍", "ok_hand": "👌",
  "eyes": "👀", "tada": "🎉", "smile": "😄", "sweat_smile": "😅", "bulb": "💡", "memo": "📝", "x": "❌", "rocket": "🚀",
  "raised_hands": "🙌", "relieved": "😌", "sparkles": "✨", "fire": "🔥", "point_right": "👉", "new": "🆕"};
function mrkdwn(raw) {
  let s = esc(raw);
  s = s.replace(/:([a-z0-9_+\-]+):/g, (m, name) => EMOJI[name] || m);
  s = s.replace(/```([\s\S]*?)```/g, (_, c) => `<pre>${c}</pre>`);
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  // A label may hold any escaped character (legacy&#39;s, &amp;); it ends only at the closing &gt;.
  s = s.replace(/&lt;@U[A-Z0-9]+\|((?:[^&]|&(?!gt;))+?)&gt;/g, '<span class="mention">@$1</span>');
  s = s.replace(/&lt;(https?:(?:[^|&\s]|&amp;)+)\|((?:[^&]|&(?!gt;))+?)&gt;/g, '<a href="$1" target="_blank">$2</a>');
  s = s.replace(/&lt;(https?:(?:[^&\s]|&amp;)+)&gt;/g, '<a href="$1" target="_blank">$1</a>');
  s = s.replace(/\[([^\]\n]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank">$1</a>');
  // bare URLs (local runs write plain text, not Slack <url|text>); skip ones already inside a tag
  s = s.replace(/(^|[^"'>=\w])(https?:\/\/(?:[^\s<&]|&amp;)+?)(?=[.,;:!?)\]]*(?:\s|$|<|&quot;|&lt;|&gt;))/g,
                '$1<a href="$2" target="_blank">$2</a>');
  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
  s = s.replace(/(^|\s)\*([^*\n]+)\*/g, "$1<b>$2</b>");
  s = s.replace(/(^|\n)&gt;\s?([^\n]*)/g, "$1<blockquote>$2</blockquote>");
  return s;
}
let threadRef = null, threadChat = false, shownDecision = new Set(), pendingDecision = null;
const isReview = (ref) => ref.startsWith(`${CFG.review}|`);

function openThread(ref, name) {
  markOpen(name);
  closeDecision(); pendingDecision = null;
  logSession = null; clearInterval(logTimer);
  threadRef = ref; threadChat = !isReview(ref); $("logtitle").textContent = name;
  $("lognew").hidden = !name.endsWith(" · Chat with Claude");
  renderTabs();
  $("reply").classList.add("on");
  $("replytext").placeholder = threadChat ? "Reply to Claude…" : "Reply in the review thread…";
  $("replyhint").textContent = threadChat ? "↩ to send · ⇧↩ new line · continues this ticket's Claude session"
                                          : `@ to mention · ↩ to send · ⇧↩ new line · posts as you in ${CFG.reviewName}`;
  $("logbody").className = threadChat ? "chat" : "thread";
  threadInfo(name.split(" ")[0]);
  $("logbody").innerHTML = '<div class="empty">Loading…</div>'; delete $("logbody").dataset.sig;
  withAnchor(() => $("main").classList.add("with-log"));
  pollThread(true); logTimer = setInterval(() => pollThread(false), threadChat ? 5000 : 20000);
  clearReply();
  syncReplyHint();
}

// The review thread's ticket strip (owner 2026-11-20): key, summary, Jira status, PR and wait / feedback state.
function threadInfo(key) {
  const el = $("tinfo"), t = key && data && data.tickets.find((x) => x.key === key);
  el.classList.toggle("on", !!t);
  if (!t) { el.innerHTML = ""; return; }
  const wait = t.review_turn && t.review_turn.owner_last ? `<span class="chip wait">${IC.clock}${waitAge(t.review_turn.ts)} wait</span>` : "";
  const fb = t.feedback && t.feedback.open ? `<span class="chip fb">💬 Feedback to fix</span>` : "";
  const r = t.run || {}, run = threadChat && r.state
    ? `<span class="chip${r.state === "running" ? " fb" : ""}">${esc(({fix: "Fix", migrate: "Migrate", review: "Review", reply: "Run", chat: "Chat", compact: "Compacting"})[r.mode] || "Run")} · ${esc(r.state === "ended_turn" ? "waiting for you" : r.state)}${r.model ? " · " + esc((((data.run_settings || {}).models || {})[r.model] || r.model)) + (r.effort ? " " + esc(r.effort) : "") : ""}</span>` : "";
  el.innerHTML = `<div class="row"><a class="key" href="${CFG.jira}/browse/${esc(t.key)}" target="_blank">${esc(t.key)}</a>
      <span class="tsum" title="${esc(t.jira_summary || t.summary)}">${esc(t.summary)}</span></div>
    <div class="meta"><span class="chip status">Jira: ${esc(t.status)}</span>${prChips(t.pr)}${run}${threadChat ? "" : fb + wait}</div>`;
}
// "Option 1 (recommended): …" lines plus 'Reply "a" or "b"' -> a decision the dialog can offer.
function parseDecision(text) {
  const opts = [], q = [];
  for (const line of text.split("\n")) {
    const m = line.match(/^\s*(?:[-*•]\s*)?\**\s*Option\s*(\d+)\s*\**\s*(\(recommended\))?\s*\**\s*[:.\-–—]\s*(.+)$/i);
    if (m) opts.push({n: m[1], rec: !!m[2], text: m[3].trim()});
    else if (!opts.length) q.push(line);
  }
  if (opts.length < 2) return null;
  const at = text.search(/\breply\b/i);
  const quoted = at < 0 ? [] : [...text.slice(at).matchAll(/["“]([^"”\n]{1,60})["”]/g)].map((x) => x[1]);
  opts.forEach((o, i) => { o.reply = quoted.length === opts.length ? quoted[i] : `Option ${o.n}`; });
  return {question: q.join("\n").trim(), options: opts};
}

// The run behind the open chat while it is running or queued, else null.
function threadRun() {
  if (!data || !threadRef) return null;
  const ts = threadRef.split("|")[1];
  const t = data.tickets.find((x) => x.run.thread_ts === ts && ["running", "queued"].includes(x.run.state));
  return t ? t.run : null;
}
const threadWorking = () => !!threadRun();

// What a running session is waiting on, under "Claude is working…" (owner request 2026-11-20: a long wait for
// helper agents looked like a stuck chat). A queued note is read only when the current step ends, so it says that too.
function waitingHtml(run) {
  const w = (run && run.waiting) || {};
  const calls = w.calls || [];
  if (!calls.length && !w.notes) return "";
  const now = Date.now() / 1000, plural = (n, s) => `${n} ${s}${n === 1 ? "" : "s"}`;
  const helpers = calls.filter((c) => c.tool === "Agent" || c.tool === "Task");
  const oldest = Math.min(...calls.map((c) => c.since || now));
  const what = !calls.length ? ""
    : helpers.length === calls.length ? `Waiting for ${plural(helpers.length, "helper")}: ${calls.map((c) => c.what).join(" · ")}`
    : `Running: ${calls.map((c) => c.what).join(" · ")}`;
  const parts = [what, calls.length ? `for ${mins(oldest)}` : "", w.helpers_at ? `helpers active ${ago(now - w.helpers_at)}` : ""].filter(Boolean);
  const note = w.notes ? `${plural(w.notes, "note")} queued: Claude reads ${w.notes === 1 ? "it" : "them"} when this step finishes.` : "";
  return `<span class="wait">${esc(parts.join(" · "))}</span>${note ? `<span class="wait">${esc(note)}</span>` : ""}`;
}

function chatHtml(msgs) {
  const last = msgs[msgs.length - 1];
  const decision = last && last.bot && !last.mine ? parseDecision(last.text) : null;
  const hhmm = (t) => esc((t || "").split(" ").pop());
  let day = "";
  const dayDiv = (m) => { const d = (m.time || "").split(" ")[0]; if (!d || d === day) return ""; day = d; return `<div class="tday">${esc(d)}</div>`; };
  const long = (html, text) => text.split("\n").length > 14 || text.length > 1100
    ? `<div class="c-text c-long">${html}</div><button type="button" class="c-more" data-more>Show more</button>` : `<div class="c-text">${html}</div>`;
  const rows = msgs.map((m) => dayDiv(m) + (() => {
    const files = m.files.length ? `<div class="files">${m.files.map((f) => `<button data-file="${esc(f.id)}">📎 ${esc(f.name)}</button>`).join("")}</div>` : "";
    if (m.mine) return `<div class="c-row me"><div class="c-msg"><div class="c-text">${mrkdwn(m.text)}</div>${files}<div class="c-meta">You · ${hhmm(m.time)}</div></div>${
      m.photo ? `<img class="c-avatar me" src="${esc(m.photo)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}</div>`;
    // The app's own notes (session started, add-on delivered, compacted, interrupted…): one quiet line each.
    if (m.who === "Claude Tickets" || /^(🛠|🔎|▶|⏹|↻|📎|✓|🗜|ℹ️|⚠️)/u.test(m.text) && m.text.length < 400 && !/\n/.test(m.text.trim())) {
      const ic = (m.text.match(/^(\p{Extended_Pictographic}\uFE0F?|[✓↻▶⏹])/u) || ["•"])[0];
      return `<div class="c-sys"><span class="ic">${esc(ic)}</span><span class="tx">${mrkdwn(m.text.slice(ic === "•" ? 0 : ic.length).trim())}</span><span class="when">${hhmm(m.time)}</span></div>`;
    }
    const choose = m === last && decision ? `<button class="primary c-choose" data-decide="${esc(m.ts)}">Choose an option…</button>` : "";
    const report = REPORT_RE.test((m.text.split("\n")[0] || ""));
    return `<div class="c-row"><img class="c-avatar" src="/avatar.png" alt=""><div class="c-msg${report ? " report" : ""}"><div class="c-head"><b>${esc(m.who)}</b>${
      report ? '<span class="tag">Report</span>' : ""}<span class="when">${hhmm(m.time)}</span></div>
      ${long(mrkdwn(m.text), m.text)}${files}${choose}</div></div>`;
  })()).join("");
  const run = threadRun();
  const typing = run ? `<div class="typing"><img class="c-avatar" src="/avatar.png" alt=""><span class="dots"><i></i><i></i><i></i></span><span class="what">Claude is working…${waitingHtml(run)}</span></div>` : "";
  return {html: (rows || '<div class="empty">No messages yet.</div>') + typing, decision, last};
}

function pinnedHtml(j) {
  const p = j.pinned;
  if (!p) return "";
  return `<details class="pin"${j.empty ? " open" : ""}><summary>📌 Last run report · <code>${esc(p.session)}</code> · ${esc(p.status || "")} ${esc(p.when)}</summary>
    <div class="c-text">${mrkdwn(p.text)}</div></details>`;
}
function emptyChatHtml(j, ts) {
  const key = esc(ts.split(":")[1]);
  if (j.empty === "fresh")
    return `<div class="empty">New chat for ${key} — no history.<br>Your first message starts a blank session <code>${key}-chat</code>.</div>`;
  if (j.fork)
    return `<div class="empty">Chat with Claude about ${key}.<br>Your first message continues the last session
      <code>${esc(j.fork.session)}</code> (${esc(j.fork.when)}) — Claude picks up where it left off.<br>
      Use <b>＋ New chat</b> for a blank session.</div>`;
  return `<div class="empty">No chat for ${key} yet.<br>Your first message starts a chat session <code>${key}-chat</code>.</div>`;
}
// Tabs over the chat: each of the ticket's sessions (runs and chats, newest first), so an earlier one can be
// reopened and continued after a New chat.
function renderTabs() {
  const el = $("ctabs");
  const name = $("logtitle").textContent;
  if (!threadRef || !threadChat || !name.endsWith(" · Chat with Claude") || !data) { el.hidden = true; return; }
  const key = name.split(" · ")[0], ts = threadRef.split("|")[1];
  const t = data.tickets.find((x) => x.key === key);
  const convs = (t && t.convs) || [];
  const label = {fix: "Fix", migrate: "Migrate", chat: "Chat", review: "Review"};
  const st = {running: '<span class="st dot live" title="Claude is working in this session"></span>',
              queued: '<span class="st dot" title="Queued"></span>', ended_turn: '<span class="st" title="Waiting for your answer">❓</span>',
              stopped: '<span class="st">⏹</span>', failed: '<span class="st">⚠️</span>'};
  // ❓ only on the newest migrate / fix run that stopped with a question: a later session has taken over from an
  // older one's, and a chat simply waits for your next message after every answer.
  const mi = {fix: "🛠", migrate: "⇢", chat: "💬", review: "🔍"};
  const tabs = convs.map((c, i) => `<button role="tab" class="${c.ts === ts ? "on" : ""}" aria-selected="${c.ts === ts}"
      data-tab-ts="${esc(c.ts)}" title="${esc(c.session)}${i === 0 ? " · latest" : ""}"><span class="tm">${mi[c.mode] || "▸"}</span><b>${label[c.mode] || "Run"}</b><span class="tw">${esc((c.when || "").slice(5))}</span>${c.state === "ended_turn" && (i > 0 || c.mode === "chat") ? "" : (st[c.state] || "")}</button>`);
  if (/^(new|fresh):/.test(ts)) tabs.unshift(`<button role="tab" class="on" aria-selected="true">＋ New chat</button>`);
  el.innerHTML = tabs.join("");
  el.hidden = tabs.length < 2 && !convs.length;
}
// A quick prompt is sent on click (owner request 2026-11-24; it used to fill the box). Only in the chat with Claude,
// never in the team thread, so no confirmation; one at a time, and what is typed in the box stays there.
$("quick").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-q]"), q = $("quick");
  if (!b || !threadRef || q.getAttribute("aria-busy") === "true") return;
  q.setAttribute("aria-busy", "true");
  try { await postReply(b.dataset.q, !!b.dataset.side); } finally { q.removeAttribute("aria-busy"); }
});
$("logbody").addEventListener("click", (e) => {
  const b = e.target.closest("[data-more]");
  if (!b) return;
  const t = b.previousElementSibling, open = t.classList.toggle("open");
  b.textContent = open ? "Show less" : "Show more";
});
$("ctabs").onclick = (e) => {
  const b = e.target.closest("button[data-tab-ts]");
  if (!b || threadRef.endsWith("|" + b.dataset.tabTs)) return;
  openThread(`${CFG.report}|${b.dataset.tabTs}`, $("logtitle").textContent);
};
$("lognew").onclick = () => {
  const key = $("logtitle").textContent.split(" · ")[0];
  openThread(`${CFG.report}|fresh:${key}`, `${key} · Chat with Claude`);
};

async function pollThread(first) {
  if (!threadRef) return;
  const ref = threadRef, [channel, ts] = ref.split("|");
  try {
    const j = await (await fetch(`/api/thread?channel=${channel}&ts=${ts}`, {cache: "no-store"})).json();
    // The panel may have switched (thread → chat, another ticket) while this was loading: never draw a stale answer.
    if (threadRef !== ref) return;
    const body = $("logbody");
    if (j.error === "needs_user_token") {
      body.innerHTML = `<div class="note">To read the team's review threads here, the dashboard needs <b>your</b> Slack user token
        (the Claude Report bot is not in that channel). Setup: Slack app settings → OAuth &amp; Permissions →
        User Token Scopes: <code>groups:history</code>, <code>channels:history</code>, <code>files:read</code>, <code>users:read</code>, <code>chat:write</code> →
        Reinstall → save the <b>User OAuth Token</b> to <code>~/.claude/secrets/slack-user-token</code>.</div>`;
      return;
    }
    if (j.error) { body.innerHTML = `<div class="note">Slack said: <code>${esc(j.error)}</code></div>`; return; }
    $("logslack").hidden = !j.link; $("logslack").dataset.href = j.link || "";
    const atEnd = body.scrollHeight - body.scrollTop - body.clientHeight < 60;
    if (threadChat) {
      const c = chatHtml(j.messages);
      const sig = JSON.stringify([ts, j.messages.length, threadWorking(), !!j.pinned]);
      // first: the panel was just (re)opened and shows "Loading…" — always draw, even when nothing changed
      if (first || body.dataset.sig !== sig) {
        const pa = first || atEnd ? null : panelAnchor();
        body.innerHTML = j.empty ? emptyChatHtml(j, ts) : c.html; body.dataset.sig = sig;
        if (first || atEnd) body.scrollTop = body.scrollHeight; else panelRestore(pa);
      } else {
        // Only the working line's wait details moved on (minutes, helper activity): refresh that line alone, so a
        // message opened with "Show more" stays open.
        const line = body.querySelector(".typing .what"), fresh = "Claude is working…" + waitingHtml(threadRun());
        if (line && line.dataset.v !== fresh) { line.innerHTML = fresh; line.dataset.v = fresh; }
      }
      threadInfo((($("logtitle").textContent || "").split(" ")[0]));
      pendingDecision = c.decision ? {...c.decision, ts: c.last.ts} : null;
      if (pendingDecision && !shownDecision.has(c.last.ts) && !threadWorking()) { shownDecision.add(c.last.ts); openDecision(); }
      return;
    }
    const pa = first || atEnd ? null : panelAnchor();
    let day = "";
    const initials = (n) => (String(n).replace(/^FDC\./, "").split(/[-.\s(]+/).filter((w) => /^[A-Za-z]/.test(w)).slice(0, 2).map((w) => w[0]).join("") || "?").toUpperCase();
    const size = (b) => !b ? "" : b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`;
    body.innerHTML = j.messages.map((m, i) => {
      const d = (m.time || "").split(" ")[0], rev = !m.mine && !!CFG.reviewer && (m.who || "").toLowerCase().includes(CFG.reviewer.toLowerCase()), fbk = rev && i > 0 && (m.files.length || /feedback/i.test(m.text));
      const divider = d && d !== day ? `<div class="tday">${esc(d)}</div>` : ""; day = d || day;
      const av = m.photo   // Slack profile photo; the initials stay underneath if it fails to load
        ? `<span class="av" aria-hidden="true">${esc(initials(m.who))}<img src="${esc(m.photo)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()"></span>`
        : `<span class="av" aria-hidden="true">${esc(initials(m.who))}</span>`;
      return divider + `<div class="msg${m.mine ? " me" : ""}${rev ? " reviewer" : ""}${fbk ? " feedback" : ""}">${av}<div>
        <div class="mh"><span class="who">${esc(m.who)}</span>${m.mine ? '<span class="tag">You</span>' : rev ? '<span class="tag">Reviewer</span>' : ""}${fbk ? '<span class="tag fbk">Feedback</span>' : ""}
          ${i === 0 ? '<span class="tag">Thread start</span>' : ""}<span class="when">${esc((m.time || "").split(" ").pop())}</span></div>
        <div class="body">${mrkdwn(m.text)}</div>
        ${m.files.length ? `<div class="files">${m.files.map((f) => `<button data-file="${esc(f.id)}" title="Open ${esc(f.name)}"><span class="fi">${esc((f.type || (f.name || "").split(".").pop() || "file").slice(0, 4).toUpperCase())}</span><span><span class="fn">${esc(f.name)}</span><span class="fs">${esc(size(f.size))}</span></span></button>`).join("")}</div>` : ""}
      </div></div>`;
    }).join("") || '<div class="empty">No messages.</div>';
    threadInfo((($("logtitle").textContent || "").split(" ")[0]));   // review thread
    if (first || atEnd) body.scrollTop = body.scrollHeight; else panelRestore(pa);
  } catch (e) { $("logbody").innerHTML = '<div class="note">The bridge did not answer.</div>'; }
}

// ---- decision dialog
let chosen = null;
function openDecision() {
  if (!pendingDecision) return;
  const d = pendingDecision;
  $("dtitle").textContent = $("logtitle").textContent.replace(/ · (Chat with Claude|Run report)$/, "");
  $("dq").innerHTML = mrkdwn(d.question);
  const order = [...d.options].sort((a, b) => b.rec - a.rec);
  chosen = order[0];
  $("dopts").innerHTML = order.map((o, i) => `<button type="button" class="opt ${i === 0 ? "on" : ""}" role="radio" aria-checked="${i === 0}" data-opt="${esc(o.n)}">
      <span class="radio"></span><span><b>Option ${esc(o.n)}${o.rec ? ' <span class="badge">Recommended</span>' : ""}</b><br>${mrkdwn(o.text)}
      <br><span class="reply">Sends: “${esc(o.reply)}”</span></span></button>`).join("");
  $("dothertext").value = "";
  $("decide").hidden = false;
  $("logbody").scrollTop = $("logbody").scrollHeight;
  $("decide").querySelector(".sheet").scrollTop = 0;
  $("dsend").focus({preventScroll: true});
}
// The reply box belongs to one thread: it is emptied whenever another thread opens or the panel closes,
// so text written for one ticket can never be sent into another ticket's thread.
function clearReply() { $("replytext").value = ""; $("replytext").style.height = ""; mentionMap = {}; if (typeof hidePop === "function") hidePop(); }
function closeDecision() { $("decide").hidden = true; }
$("dopts").onclick = (e) => {
  const b = e.target.closest(".opt"); if (!b || !pendingDecision) return;
  chosen = pendingDecision.options.find((o) => o.n === b.dataset.opt);
  for (const x of $("dopts").children) { x.classList.toggle("on", x === b); x.setAttribute("aria-checked", x === b); }
};
$("dcancel").onclick = closeDecision;
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || e.defaultPrevented || e.isComposing) return;   // Esc that cancels an IME conversion
  if (!$("decide").hidden) { closeDecision(); return; }
  // Esc with an unsent reply in the box only leaves the box; it never closes the panel and loses the draft
  if (document.activeElement === $("replytext") && $("replytext").value.trim()) { $("replytext").blur(); return; }
  // Esc closes the side panel, unless a dialog or the suggestion popup has it (they handle Esc themselves)
  if (document.querySelector("dialog[open]") || pop) return;
  if ($("main").classList.contains("with-log")) $("logclose").click();
});
$("dsend").onclick = async () => {
  const other = $("dothertext").value.trim();
  const text = other || (chosen && chosen.reply);
  if (!text) return;
  $("dsend").disabled = true;
  const ok = await postReply(text);
  $("dsend").disabled = false;
  if (ok) closeDecision();
};

// ---- @mentions (team review threads): Slack-style picker; "@Name" becomes <@ID> when sent
let people = null, peopleAt = 0, peopleLoading = null, mentionMap = {}, pop = null;
const clean = (s) => (s || "").replace(/[\u200b-\u200d\ufeff]/g, "").trim();
// An empty list (the read failed, e.g. Slack cut the member list short on 2026-11-24) is asked for again after 30 s,
// instead of leaving the picker empty until the window reloads.
function loadPeople() {
  if (people && (people.length || Date.now() - peopleAt < 30000)) return Promise.resolve(people);
  return peopleLoading ||= (async () => {   // keystrokes while it loads share this one request
    try { const j = await (await fetch("/api/people", {cache: "no-store"})).json(); people = (j.people || []).map((p) => ({...p, name: clean(p.name), real: clean(p.real)})); }
    catch (e) { people = []; }
    peopleAt = Date.now(); peopleLoading = null;
    return people;
  })();
}
// ---- :emoji: suggestions (owner 2026-11-20), Slack's names: in the review thread ":name:" is inserted (Slack shows it),
// in the chat with Claude the emoji itself.
let emojiList = null;
async function loadEmoji() {
  if (emojiList) return emojiList;
  try {
    const rows = await (await fetch("/emoji.json")).json();
    emojiList = rows.map(([name, ch, ...alias]) => ({name, ch, alias}));
    for (const e of emojiList) for (const n of [e.name, ...e.alias]) if (!EMOJI[n]) EMOJI[n] = e.ch;   // render them too
  } catch (e) { emojiList = []; }
  return emojiList;
}
loadEmoji();
function emojiMatches(q) {
  const out = [];
  for (const e of emojiList || []) {
    const names = [e.name, ...e.alias], hit = names.find((n) => n.startsWith(q)) || names.find((n) => n.includes(q));
    if (hit) out.push({...e, hit, rank: hit === q ? 0 : hit.startsWith(q) ? 1 : 2});
  }
  return out.sort((a, b) => a.rank - b.rank || a.hit.length - b.hit.length).slice(0, 8);
}
function hidePop() { pop = null; $("mention").hidden = true; }
async function updatePop() {
  if (!threadRef) return hidePop();
  const ta = $("replytext"), caret = ta.selectionStart, before = ta.value.slice(0, caret);
  const em = before.match(/(?:^|[\s(])(:([a-z0-9_+\-]{2,30}))$/i);
  if (em) {
    await loadEmoji();
    const items = emojiMatches(em[2].toLowerCase());
    if (!items.length) return hidePop();
    const keep = pop && pop.kind === "emoji" && pop.items[pop.idx] ? items.findIndex((x) => x.name === pop.items[pop.idx].name) : -1;
    pop = {kind: "emoji", start: caret - em[1].length, end: caret, items, idx: keep >= 0 ? keep : 0};
    return renderPop();
  }
  if (threadChat) return hidePop();
  const m = before.match(/(?:^|\s)@([^\s@]{0,40})$/);
  if (!m) return hidePop();
  const q = m[1].toLowerCase(), list = await loadPeople();
  const hit = (p) => p.name.toLowerCase().includes(q) || p.real.toLowerCase().includes(q);
  const items = list.filter(hit).sort((a, b) => (b.name.toLowerCase().startsWith(q) - a.name.toLowerCase().startsWith(q))).slice(0, 8);
  if (!items.length) return hidePop();
  pop = {kind: "mention", start: caret - m[1].length - 1, end: caret, items, idx: pop && pop.items[pop.idx] && items.indexOf(pop.items[pop.idx]) >= 0 ? items.indexOf(pop.items[pop.idx]) : 0};
  renderPop();
}
function renderPop() {
  if (pop.kind === "emoji") {
    $("mention").innerHTML = '<div class="mhead">Emoji matching <code>:' + esc($("replytext").value.slice(pop.start + 1, pop.end)) + '</code></div>' +
      pop.items.map((e, i) => `<button type="button" class="mi emo ${i === pop.idx ? "on" : ""}" role="option" aria-selected="${i === pop.idx}" data-i="${i}">
        <span class="eg">${e.ch}</span><b>:${esc(e.hit)}:</b><span>${e.hit !== e.name ? ":" + esc(e.name) + ":" : ""}</span></button>`).join("");
    $("mention").hidden = false;
    return;
  }
  $("mention").innerHTML = '<div class="mhead">Mention someone</div>' + pop.items.map((p, i) => `<button type="button" class="mi ${i === pop.idx ? "on" : ""}" role="option" aria-selected="${i === pop.idx}" data-i="${i}">
      ${p.avatar ? `<img src="${esc(p.avatar)}" alt="">` : '<span class="ph"></span>'}<b>${esc(p.name)}</b><span>${esc(p.real && p.real !== p.name ? p.real : "")}</span></button>`).join("");
  $("mention").hidden = false;
}
function chooseEmoji(e) {
  const ta = $("replytext"), v = ta.value;
  const insert = (threadChat ? e.ch : `:${e.hit}:`) + " ";
  ta.value = v.slice(0, pop.start) + insert + v.slice(pop.end);
  const pos = pop.start + insert.length;
  hidePop(); ta.focus(); ta.setSelectionRange(pos, pos); ta.dispatchEvent(new Event("input"));
}
const choosePop = (i) => (pop.kind === "emoji" ? chooseEmoji : choosePerson)(pop.items[i]);
function choosePerson(p) {
  const ta = $("replytext"), v = ta.value;
  const insert = `@${p.name} `;
  ta.value = v.slice(0, pop.start) + insert + v.slice(pop.end);
  mentionMap[p.name] = p.id;
  const pos = pop.start + insert.length;
  hidePop(); ta.focus(); ta.setSelectionRange(pos, pos); ta.dispatchEvent(new Event("input"));
}
$("mention").addEventListener("mousedown", (e) => { const b = e.target.closest(".mi"); if (b && pop) { e.preventDefault(); choosePop(+b.dataset.i); } });
$("replytext").addEventListener("keyup", (e) => { if (!["ArrowUp", "ArrowDown", "Enter", "Tab", "Escape"].includes(e.key)) updatePop(); });
$("replytext").addEventListener("click", updatePop);
$("replytext").addEventListener("blur", () => setTimeout(hidePop, 150));
$("replytext").addEventListener("keydown", (e) => {
  if (!pop) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); pop.idx = (pop.idx + (e.key === "ArrowDown" ? 1 : -1) + pop.items.length) % pop.items.length; renderPop(); }
  else if ((e.key === "Enter" && !e.isComposing) || e.key === "Tab") { e.preventDefault(); e.stopImmediatePropagation(); choosePop(pop.idx); }
  else if (e.key === "Escape") { e.preventDefault(); hidePop(); }
}, true);
function applyMentions(text) {
  for (const [name, id] of Object.entries(mentionMap)) text = text.split(`@${name}`).join(`<@${id}>`);
  return text;
}

// ---- replies
async function postReply(text, side = false) {
  const [channel, ts] = threadRef.split("|");
  if (channel === CFG.review) text = applyMentions(text);
  try {
    const r = await fetch("/api/reply", {method: "POST", headers: {"Content-Type": "application/json", "X-Dashboard": "1"},
      body: JSON.stringify({channel, ts, text, side})});
    const j = await r.json();
    toast(j.message || (j.ok ? "Sent" : "Failed"));
    if (j.ok && j.ts) threadRef = `${channel}|${j.ts}`;   // a new chat now has its own conversation
    if (j.ok) { $("logbody").dataset.sig = ""; setTimeout(() => { load(); pollThread(false); }, 900); }
    return !!j.ok;
  } catch (err) { toast("The bridge did not answer."); return false; }
}
$("logslack").onclick = (e) => window.open(e.target.dataset.href, "_blank");
$("logclose").onclick = () => { const wasOpen = openKey; markOpen(null); closeDecision(); clearReply(); logSession = null; threadRef = null; clearInterval(logTimer); $("logslack").hidden = true; $("reply").classList.remove("on"); withAnchor(() => $("main").classList.remove("with-log"), wasOpen); };
async function sendReply(e) {
  if (e) e.preventDefault();
  if (!threadRef) return;
  const text = $("replytext").value.trim();
  if (!text) return;
  // The team thread is seen by others, so it asks first; the chat is a conversation with Claude.
  if (!threadChat && !(await askConfirm({kicker: "Team review thread", title: "Send this reply as you?", ok: "Send",
      text: `It posts in ${CFG.reviewName}, where the team can see it.`,
      quote: text.length > 600 ? text.slice(0, 600) + "…" : text}))) return;
  $("replysend").disabled = true;
  if (await postReply(text)) { $("replytext").value = ""; $("replytext").style.height = ""; }
  $("replysend").disabled = false;
}
$("reply").onsubmit = sendReply;
// Enter sends; Shift+Enter adds a line. Not while a Japanese / IME composition is being confirmed with Enter.
$("replytext").onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.altKey && !e.isComposing && e.keyCode !== 229) sendReply(e);
};
$("replytext").oninput = (e) => {
  const B = $("logbody"), end = B.scrollHeight - B.scrollTop - B.clientHeight < 40, pa = end ? null : panelAnchor();
  e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, window.innerHeight * .3) + "px";
  if (end) B.scrollTop = B.scrollHeight; else panelRestore(pa);   // a growing reply box never hides what you were reading
};

document.addEventListener("click", (e) => {
  const th = e.target.closest("th[data-sort]");
  if (th) { sortBy = sortBy.col === th.dataset.sort ? {col: th.dataset.sort, dir: -sortBy.dir} : {col: th.dataset.sort, dir: 1}; renderTable(); return; }
  if (e.target.closest("#copyrep")) { copyReport(); return; }
  const dayBtn = e.target.closest("button[data-day]");
  if (dayBtn) { shiftDay(Number(dayBtn.dataset.day)); return; }
  const tb = e.target.closest("#side .menu button");
  if (tb) { setTab(tb.dataset.tab); return; }
  const el = e.target.closest("button");
  if (!el) {
    // A click on a card's empty space or text opens its review thread — not on a link, and not after selecting text.
    const cardEl = e.target.closest(".card[data-thread]");
    if (cardEl && !e.target.closest("a") && !String(window.getSelection() || "").trim())
      openThread(cardEl.dataset.thread, cardEl.dataset.name);
    return;
  }
  if (el.dataset.act) act(el.dataset.act, el.dataset.key);
  else if (el.dataset.conflictfix) conflictFix(el.dataset.conflictfix);
  else if (el.dataset.log) openLog(el.dataset.log, el.dataset.name);
  else if (el.dataset.thread) openThread(el.dataset.thread, el.dataset.name);
  else if (el.dataset.decide) openDecision();
  else if (el.dataset.file) openFile(el.dataset.file, el.querySelector(".fn") ? el.querySelector(".fn").textContent : el.textContent.replace(/^📎\s*/, ""));
  else if (el.dataset.href) window.open(el.dataset.href, "_blank");
});
$("q").oninput = () => data && render();
$("hideSkip").onchange = () => data && render();
(function () {
  const main = $("main"), split = $("split");
  try { const w = localStorage.getItem("ct-logw"); if (w) main.style.setProperty("--logw", w); } catch (e) {}
  split.addEventListener("pointerdown", (e) => {
    e.preventDefault(); split.setPointerCapture(e.pointerId); split.classList.add("drag"); document.body.classList.add("resizing");
    const move = (ev) => {
      const r = main.getBoundingClientRect();
      const px = Math.min(Math.max(r.right - ev.clientX, 320), r.width * 0.75);
      withAnchor(() => { const pa = panelAnchor(); main.style.setProperty("--logw", px + "px"); panelRestore(pa); });
    };
    const up = () => {
      split.removeEventListener("pointermove", move); split.classList.remove("drag"); document.body.classList.remove("resizing");
      try { localStorage.setItem("ct-logw", main.style.getPropertyValue("--logw")); } catch (e) {}
    };
    split.addEventListener("pointermove", move); split.addEventListener("pointerup", up, {once: true});
  });
  split.addEventListener("dblclick", () => { main.style.removeProperty("--logw"); try { localStorage.removeItem("ct-logw"); } catch (e) {} });
})();
document.querySelectorAll("#side .menu button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
(function () {  // menu shown / hidden, remembered per viewer
  const narrow = () => window.matchMedia("(max-width: 760px)").matches;
  const set = (hidden) => {
    document.body.classList.toggle("nav-hidden", hidden);
    $("menubtn").setAttribute("aria-expanded", String(!hidden));
    try { localStorage.setItem("ct-nav-hidden", hidden ? "1" : "0"); } catch (e) {}
  };
  let saved = null; try { saved = localStorage.getItem("ct-nav-hidden"); } catch (e) {}
  set(saved === null ? narrow() : saved === "1");
  $("menubtn").onclick = () => set(!document.body.classList.contains("nav-hidden"));
  document.getElementById("side").addEventListener("click", (e) => { if (narrow() && e.target.closest(".menu button")) set(true); });
})();
if (tab === "table") loadTable();
if (tab === "today") loadToday();
setInterval(() => { if (tab === "table") loadTable(); if (tab === "today" && todayDate === localDay(new Date())) loadToday(); }, 60000);
load(); setInterval(load, 5000);
