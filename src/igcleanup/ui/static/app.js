const GROUP_TITLES = {
  inactive: "Inactive for over a year",
  never_posted: "Has never posted",
  gone: "Account no longer exists",
  error: "Couldn't check",
  kept: "Keeping",
};
const GROUP_HELP = {
  inactive: "Their newest post is older than your inactivity setting. Ticked by default.",
  never_posted: "Accounts with no posts at all, such as friends who only watch. Unticked by default so you decide.",
  gone: "Instagram no longer shows this account: deleted or deactivated. Ticked by default.",
  error: "The tool could not read this profile. Nothing is guessed; check it yourself before deciding.",
  kept: "Accounts you chose to keep. They are never unfollowed, even after a new scan.",
};
const DONE_TODAY = "Done for today, come back tomorrow.";
const CURRENT_VERBS = { scan: "Checking", unfollow: "Unfollowing", restore: "Following" };
const JOB_TITLES = { scan: "Scanning your following", unfollow: "Unfollowing", restore: "Restoring from backup" };
const THEMES = ["light", "dark", "neon", "phosphor"];
const THEME_NAMES = { light: "Light", dark: "Dark", neon: "Neon", phosphor: "Phosphor" };
const APP_TITLE = "Instagram Cleanup";

const $ = (id) => document.getElementById(id);

// ---- Theme ------------------------------------------------------------------
function applyTheme(name) {
  if (!THEMES.includes(name)) name = "light";
  document.documentElement.dataset.theme = name;
  $("theme").textContent = `Theme: ${THEME_NAMES[name]}`;
  try { localStorage.setItem("theme", name); } catch (e) { /* private mode: fine */ }
}
function loadTheme() {
  let saved = "light";
  try { saved = localStorage.getItem("theme") || "light"; } catch (e) { /* ignore */ }
  applyTheme(saved);
}
$("theme").onclick = () => {
  const current = document.documentElement.dataset.theme || "light";
  applyTheme(THEMES[(THEMES.indexOf(current) + 1) % THEMES.length]);
};

// ---- Dialogs ----------------------------------------------------------------
// The app window is browser-automated, which auto-dismisses native popups; use in-page dialogs.
function askDialog(text, { cancel = true } = {}) {
  return new Promise((resolve) => {
    const dlg = $("ask");
    $("ask-text").textContent = text;
    $("ask-no").hidden = !cancel;
    const done = (value) => { dlg.close(); $("ask-yes").onclick = null; $("ask-no").onclick = null; dlg.oncancel = null; resolve(value); };
    $("ask-yes").onclick = () => done(true);
    $("ask-no").onclick = () => done(false);
    dlg.oncancel = (e) => { e.preventDefault(); done(false); };
    dlg.showModal();
  });
}
const notify = (text) => askDialog(text, { cancel: false });
const ERROR_TEXT = {
  not_logged_in: "Please log in first.",
  job_paused: "Finish or resume the paused job first.",
  not_found: "That backup file no longer exists.",
  nothing_selected: "Nothing is selected.",
  busy: "Something else is running.",
};
const errorText = (e) => ERROR_TEXT[e.message] || "Something else is running.";
const askConfirm = (text) => askDialog(text);

let state = null;
let review = null;
let lastEvent = null;
let welcomeReturnsToSettings = false;
let termsAccepted = false;  // asked on every launch, on purpose

async function api(method, path, body) {
  const r = await fetch(path, {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || r.statusText), { data });
  return data;
}

function show(view) {
  document.querySelectorAll("section[data-view]").forEach((s) =>
    s.classList.toggle("active", s.dataset.view === view));
  if (view !== "progress") document.title = APP_TITLE;
}
function activeView() {
  const a = document.querySelector("section.active");
  return a ? a.dataset.view : null;
}

function fmt(n) { return Number(n).toLocaleString(); }
function plural(n, word) { return `${n} ${word}${n === 1 ? "" : "s"}`; }
function timeLeft(seconds) {
  if (seconds < 60) return `about ${plural(Math.max(1, Math.round(seconds)), "second")} left`;
  const mins = Math.round(seconds / 60);
  if (mins < 60) return `about ${plural(mins, "minute")} left`;
  return `about ${plural(Math.round(mins / 60), "hour")} left`;
}
function helpIcon(text) {
  const s = document.createElement("span");
  s.className = "help";
  s.dataset.tip = text;
  s.textContent = "?";
  return s;
}

// Native tooltips do not show reliably in the app window, so "?" marks use this popover.
let tipPinned = null;
let tipFor = null;
function placeTip() {
  const tip = $("tip");
  if (tip.hidden || !tipFor) return;
  const r = tipFor.getBoundingClientRect();
  const width = Math.min(320, window.innerWidth - 24);
  tip.style.maxWidth = width + "px";
  let left = r.left;
  if (left + width > window.innerWidth - 12) left = window.innerWidth - width - 12;
  tip.style.left = Math.max(12, left) + "px";
  tip.style.top = (r.bottom + 8) + "px";
}
function showTip(el) {
  const tip = $("tip");
  tipFor = el;
  tip.textContent = el.dataset.tip;
  tip.hidden = false;
  placeTip();
}
function hideTip() { $("tip").hidden = true; tipPinned = null; tipFor = null; }
window.addEventListener("resize", placeTip);
window.addEventListener("scroll", placeTip, true);
document.addEventListener("mouseover", (e) => {
  const el = e.target.closest && e.target.closest(".help");
  if (el && !tipPinned) showTip(el);
});
document.addEventListener("mouseout", (e) => {
  const el = e.target.closest && e.target.closest(".help");
  if (el && !tipPinned) hideTip();
});
document.addEventListener("click", (e) => {
  const el = e.target.closest && e.target.closest(".help");
  if (el) {
    e.preventDefault();
    e.stopPropagation();
    if (tipPinned === el) return hideTip();
    tipPinned = el;
    showTip(el);
  } else if (tipPinned) {
    hideTip();
  }
});

function waitingForTomorrow(job) {
  return job && job.state === "paused" && job.message === DONE_TODAY;
}

// ---- Routing ------------------------------------------------------------------
async function refresh() {
  state = await api("GET", "/api/state");
  if (!termsAccepted) return show("terms");
  if (!state.logged_in) return show("login");
  const job = state.active_job;
  if (job && job.state === "running") return renderProgress(job);
  if (state.settings && state.settings.welcome_seen === false && !job) {
    welcomeReturnsToSettings = false;
    return show("welcome");
  }
  show("home");
  renderHome();
}

$("terms-agree").onclick = () => { termsAccepted = true; refresh(); };
$("terms-decline").onclick = async () => {
  $("terms-decline").disabled = true;
  $("terms-decline").textContent = "Closing...";
  try { await api("POST", "/api/quit"); } catch (e) { /* the server is going away */ }
};
$("terms-open").onclick = () => api("POST", "/api/open_terms").catch(() => {});

$("welcome-ok").onclick = async () => {
  if (welcomeReturnsToSettings) { welcomeReturnsToSettings = false; return $("gear").click(); }
  try { await api("POST", "/api/welcome/seen"); } catch (e) { /* not fatal */ }
  refresh();
};
$("show-welcome").onclick = () => { welcomeReturnsToSettings = true; show("welcome"); };

// After starting or resuming a job, go to the live view unless a finish screen already
// took over (a job can end within milliseconds, e.g. when the daily cap is reached).
function refreshUnlessFinished() {
  if (activeView() !== "finished") refresh();
}

async function resumeJob() {
  try { await api("POST", "/api/jobs/resume"); }
  catch (e) { notify(errorText(e)); }
  refreshUnlessFinished();
}

function renderHome() {
  const box = $("home-buttons");
  box.innerHTML = "";
  const note = $("home-note");
  note.textContent = "";
  const scan = state.latest_scan;
  const job = state.active_job;
  const remaining = state.targets_remaining;
  const banner = $("home-banner");
  const failed = state.last_failed;
  const isNewer = failed && (!scan || failed.id > scan.id);
  if (failed && !job && isNewer) {
    banner.hidden = false;
    banner.textContent = `The last ${failed.type} stopped: ${failed.message}. You can try again.`;
  } else {
    banner.hidden = true;
  }

  if (waitingForTomorrow(job)) {
    const label = job.type === "restore" ? "Continue restoring" : "Continue unfollowing";
    addButton(box, `${label} (${fmt(job.total - job.done)} remaining)`, resumeJob);
    note.textContent = DONE_TODAY + " Pressing Continue before then will just stop again.";
  } else if (job && job.state === "paused") {
    // Paused by the user, or by something Instagram needs from them: say which, offer Resume.
    const what = { scan: "Scan", unfollow: "Unfollowing", restore: "Restoring" }[job.type] || job.type;
    const progress = job.type === "scan"
      ? `${fmt(job.done)} of ${fmt(job.total)} checked`
      : `${fmt(job.done)} of ${fmt(job.total)} done`;
    if (job.message) {
      banner.hidden = false;
      banner.textContent = `${what} paused: ${job.message}`;
    }
    addButton(box, `Resume ${what.toLowerCase()} (${progress})`, resumeJob);
    if (job.type !== "scan" && scan && scan.state === "done") {
      addButton(box, "Review results", openReview, false, true);
    }
    addButton(box, "Stop this run", async () => {
      if (!(await askConfirm(`Stop ${what.toLowerCase()} here? Your ticks stay as they are, so you can start again later.`))) return;
      try { await api("POST", "/api/jobs/stop"); } catch (e) { notify(errorText(e)); }
      refresh();
    }, false, true);
    note.textContent = job.message ? "" : `${what} is paused. Nothing happens until you press Resume.`;
    return;
  } else if (scan && scan.state === "done" && remaining > 0 && state.account_count > 0) {
    addButton(box, `Unfollow selected accounts (${fmt(remaining)})`, startUnfollow);
  }
  const checked = state.account_count - state.unchecked;
  if (scan && scan.state === "done" && state.unchecked === 0 && state.account_count > 0) {
    addButton(box, "Review results", openReview, false, true);
    addButton(box, "Scan again", () => startScan(true), false, true);
    if (!note.textContent) note.textContent = `Last scan checked ${fmt(state.account_count)} accounts.`;
  } else if (state.account_count > 0 && state.unchecked > 0) {
    // A stopped or interrupted scan: carry on where it left off, results so far are usable.
    addButton(box, `Continue scan (${fmt(checked)} of ${fmt(state.account_count)} checked)`, () => startScan(false));
    if (checked > 0) addButton(box, "Review results", openReview, false, true);
    note.textContent = `${fmt(state.unchecked)} accounts have not been checked yet.`;
  } else {
    addButton(box, "Scan my following", () => startScan(false));
  }
}

function addButton(parent, label, onClick, primary = true, secondary = false) {
  const b = document.createElement("button");
  b.className = "big" + (secondary || !primary ? " secondary" : "");
  b.textContent = label;
  b.onclick = onClick;
  parent.appendChild(b);
  return b;
}

async function startScan(rescan) {
  const job = state && state.active_job;
  if (rescan && job && job.state === "paused" && job.type !== "scan") {
    const what = job.type === "restore" ? "restoring" : "unfollowing";
    if (!(await askConfirm(`This will stop the paused ${what} and start a fresh scan. Continue?`))) return;
  }
  try { await api("POST", "/api/scan/start", { rescan }); }
  catch (e) { notify(errorText(e)); }
  refresh();
}

// ---- Progress -----------------------------------------------------------------
function renderProgress(job) {
  show("progress");
  $("progress-title").textContent = JOB_TITLES[job.type] || job.type;
  const pct = job.total ? Math.round((job.done / job.total) * 100) : 0;
  $("progress-fill").style.width = pct + "%";
  document.title = job.state === "running" ? `${APP_TITLE}, ${pct}%` : `${APP_TITLE}, paused`;
  let text = `${fmt(job.done)} of ${fmt(job.total)} ${job.type === "scan" ? "checked" : "done"}`;
  if (job.type === "scan" && job.state === "running" && job.total > job.done) {
    text += ", " + timeLeft((job.total - job.done) * job.seconds_per_item);
  }
  const acting = job.type === "unfollow" || job.type === "restore";
  if (acting && job.state !== "done") {
    const noun = job.type === "unfollow" ? "unfollowed" : "followed again";
    text = `${fmt(job.done)} of ${fmt(job.total)} ${noun}, ${fmt(job.total - job.done)} remaining. `
      + `Today's limit: ${fmt(job.actions_today)} of ${fmt(job.daily_action_cap)} used (unfollows and follows count together).`;
  }
  $("progress-help").hidden = !acting;
  $("progress-text").textContent = text;
  const verb = CURRENT_VERBS[job.type] || "Checking";
  $("progress-message").textContent = job.message || (job.current ? `${verb} @${job.current}` : "");
  const box = $("progress-buttons");
  box.innerHTML = "";
  if (job.state === "running") {
    addButton(box, "Pause", async () => { await api("POST", "/api/jobs/pause"); }, false, true);
    addButton(box, "Back", refresh, false, true);
  } else if (job.state === "paused" && job.message !== DONE_TODAY) {
    addButton(box, "Resume", resumeJob);
    addButton(box, "Back", refresh, false, true);
  } else {
    addButton(box, "Back", refresh, false, true);
  }
}

// ---- Finish screen --------------------------------------------------------------
async function showFinished(jobId, reason) {
  let s;
  try { s = await api("GET", `/api/jobs/${jobId}/summary`); } catch (e) { return refresh(); }
  const mark = $("finished-mark");
  const stats = $("finished-stats");
  const buttons = $("finished-buttons");
  mark.innerHTML = "";
  stats.innerHTML = "";
  buttons.innerHTML = "";
  const capped = reason === "cap";
  if (s.type === "scan") {
    $("finished-title").textContent = "Scan finished";
    $("finished-text").textContent = `Checked ${fmt(s.total)} accounts.` + (s.message ? ` ${s.message}` : "");
    const c = s.counts || {};
    for (const key of ["inactive", "never_posted", "gone", "error", "kept"]) {
      if (!(key in c) && key !== "inactive") continue;
      addStat(stats, c[key] || 0, key === "inactive" ? inactiveGroupTitle() : GROUP_TITLES[key]);
    }
    addButton(buttons, "Review results", openReview);
    addButton(buttons, "Home", refresh, false, true);
  } else {
    const noun = s.type === "unfollow" ? "unfollowed" : "followed again";
    if (capped) {
      $("finished-title").textContent = "Done for today";
      $("finished-text").textContent = `${fmt(s.done)} ${noun} so far, ${fmt(s.remaining)} to go. Come back tomorrow and press Continue.`;
    } else {
      $("finished-title").textContent = s.type === "unfollow" ? "All unfollowed" : "All followed again";
      $("finished-text").textContent = `${fmt(s.done)} of ${fmt(s.total)} ${noun}.`;
    }
    addStat(stats, s.done, noun);
    if (capped) addStat(stats, s.remaining, "still to go");
    addButton(buttons, "Home", refresh);
  }
  show("finished");
  if (capped) {
    mark.innerHTML = '<svg class="check" viewBox="0 0 100 100"><circle cx="50" cy="50" r="45"/><path d="M30 52 L45 66 L72 36"/></svg>';
  } else {
    confetti();
  }
}
function addStat(parent, n, label) {
  const d = document.createElement("div");
  d.className = "stat";
  const num = document.createElement("div"); num.className = "n"; num.textContent = fmt(n);
  const lab = document.createElement("div"); lab.className = "l"; lab.textContent = label;
  d.appendChild(num); d.appendChild(lab);
  parent.appendChild(d);
}
function confetti() {
  const canvas = $("confetti");
  const ctx = canvas.getContext("2d");
  canvas.width = window.innerWidth; canvas.height = window.innerHeight;
  canvas.hidden = false;
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#c13584";
  const colors = [accent, "#ffd166", "#06d6a0", "#118ab2", "#ef476f", "#ffffff"];
  const bits = Array.from({ length: 140 }, () => ({
    x: Math.random() * canvas.width, y: -20 - Math.random() * canvas.height * 0.5,
    vx: (Math.random() - 0.5) * 2, vy: 2 + Math.random() * 3.5,
    w: 6 + Math.random() * 6, h: 8 + Math.random() * 8,
    rot: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.2,
    color: colors[Math.floor(Math.random() * colors.length)],
  }));
  const start = performance.now();
  function frame(t) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (const b of bits) {
      b.x += b.vx; b.y += b.vy; b.rot += b.vr;
      ctx.save(); ctx.translate(b.x, b.y); ctx.rotate(b.rot);
      ctx.fillStyle = b.color; ctx.fillRect(-b.w / 2, -b.h / 2, b.w, b.h);
      ctx.restore();
    }
    if (t - start < 2600) requestAnimationFrame(frame);
    else { ctx.clearRect(0, 0, canvas.width, canvas.height); canvas.hidden = true; }
  }
  requestAnimationFrame(frame);
}

// ---- Review ---------------------------------------------------------------------
function inactiveGroupTitle() {
  const months = (state && state.settings && state.settings.inactivity_months) || 12;
  return months === 12 ? "Inactive for over a year" : `Inactive for over ${months} months`;
}

async function openReview() {
  review = await api("GET", "/api/review");
  $("stale-banner").hidden = !review.stale;
  $("unchecked-banner").hidden = !review.unchecked;
  $("unchecked-banner").textContent = `${fmt(review.unchecked)} accounts have not been checked yet. Press Continue scan on the Home screen to finish.`;
  const titles = { ...GROUP_TITLES, inactive: inactiveGroupTitle() };
  const box = $("groups");
  box.innerHTML = "";
  for (const key of Object.keys(GROUP_TITLES)) {
    const rows = review.groups[key] || [];
    const details = document.createElement("details");
    details.className = "group";
    details.open = key === "inactive";
    const summary = document.createElement("summary");
    const label = document.createElement("span");
    label.textContent = `${titles[key]} (${fmt(rows.length)})`;
    label.appendChild(helpIcon(GROUP_HELP[key]));
    summary.appendChild(label);
    details.appendChild(summary);
    if (key !== "kept") {
      const tools = document.createElement("div");
      tools.className = "tools";
      const search = document.createElement("input"); search.type = "search"; search.placeholder = "Search";
      const all = document.createElement("button"); all.className = "small"; all.textContent = "Check all";
      const none = document.createElement("button"); none.className = "small"; none.textContent = "Uncheck all";
      all.onclick = () => setGroupSelection(details, key, true);
      none.onclick = () => setGroupSelection(details, key, false);
      search.oninput = () => {
        const q = search.value.toLowerCase();
        details.querySelectorAll(".row").forEach((r) => r.hidden = q && !r.dataset.search.includes(q));
      };
      tools.appendChild(search); tools.appendChild(all); tools.appendChild(none);
      details.appendChild(tools);
    }
    const rowsBox = document.createElement("div");
    rowsBox.className = "rows";
    for (const a of rows) rowsBox.appendChild(rowEl(a, { keep: key === "kept" ? "unkeep" : "keep" }));
    details.appendChild(rowsBox);
    box.appendChild(details);
  }
  show("review");
}
async function setGroupSelection(details, key, value) {
  const usernames = (review.groups[key] || []).map((a) => a.username);
  await api("POST", "/api/selection", { usernames, selected: value });
  details.querySelectorAll("input[type=checkbox]").forEach((c) => c.checked = value);
}

// `remote: false` keeps the checkbox local (used by the Restore screen); the Review
// screen persists every change through the API. `keep` adds a Keep or Unkeep button.
function rowEl(a, { remote = true, keep = null } = {}) {
  const row = document.createElement("div");
  row.className = "row";
  row.dataset.username = a.username;
  row.dataset.search = (a.username + " " + (a.display_name || "")).toLowerCase();

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = !!a.selected;
  if (keep === "unkeep") checkbox.disabled = true;
  if (remote) {
    checkbox.onchange = (e) =>
      api("POST", "/api/selection", { usernames: [a.username], selected: e.target.checked });
  }

  const img = document.createElement("img");
  img.referrerPolicy = "no-referrer";  // Instagram's CDN refuses images that carry our local referrer
  if (a.profile_pic_url) img.src = a.profile_pic_url; else img.style.visibility = "hidden";  // keep the grid column
  img.alt = "";
  img.loading = "lazy";

  const info = document.createElement("div");
  const link = document.createElement("a");
  link.href = "#";
  link.dataset.open = a.username;
  link.textContent = "@" + a.username;
  link.onclick = (e) => { e.preventDefault(); api("POST", `/api/open/${a.username}`); };
  const name = document.createElement("div");
  name.className = "name";
  name.textContent = a.display_name || "";
  info.appendChild(link);
  info.appendChild(name);

  const dateEl = document.createElement("div");
  dateEl.className = "date";
  const lastPost = document.createElement("div");
  lastPost.textContent = a.last_post_date ? `last post ${a.last_post_date}` : "";
  const checkedEl = document.createElement("div");
  checkedEl.textContent = a.checked_at ? `checked ${a.checked_at}` : "";
  dateEl.appendChild(lastPost);
  dateEl.appendChild(checkedEl);

  row.appendChild(checkbox);
  row.appendChild(img);
  row.appendChild(info);
  row.appendChild(dateEl);

  if (keep) {
    const btn = document.createElement("button");
    btn.className = "small";
    btn.textContent = keep === "keep" ? "Keep" : "Unkeep";
    btn.title = keep === "keep" ? "Never unfollow this account" : "Put this account back in its group";
    btn.onclick = async () => {
      await api("POST", "/api/keep", { username: a.username, kept: keep === "keep" });
      openReview();
    };
    row.appendChild(btn);
  } else {
    row.appendChild(document.createElement("span"));
  }
  return row;
}

async function startUnfollow() {
  const fresh = await api("GET", "/api/state");
  const n = fresh.targets_remaining;
  if (n === 0) return notify("Nothing is selected.");
  $("confirm-text").textContent =
    `This will unfollow ${fmt(n)} accounts at about ${fmt(fresh.settings.daily_action_cap)} per day. A backup will be saved first.`;
  $("confirm").showModal();
}

$("confirm-no").onclick = () => $("confirm").close();
$("confirm-yes").onclick = async () => {
  $("confirm").close();
  try { await api("POST", "/api/unfollow/start"); }
  catch (e) { notify(errorText(e)); }
  refreshUnlessFinished();
};
$("unfollow-selected").onclick = startUnfollow;
$("review-back").onclick = refresh;

// ---- Settings ---------------------------------------------------------------------
let savedSettings = null;
let speedPresets = null;

function describeSpeed() {
  const p = speedPresets && speedPresets[$("speed").value];
  if (!p) return;
  $("speed-detail").textContent =
    `Waits ${p.scan_delay_seconds[0]} to ${p.scan_delay_seconds[1]} seconds between profile checks, `
    + `rests ${p.scan_rest_seconds[0]} to ${p.scan_rest_seconds[1]} seconds every ${p.scan_rest_every} or so, `
    + `and ${p.action_delay_seconds[0]} to ${p.action_delay_seconds[1]} seconds between unfollows.`;
  $("speed-warning").hidden = $("speed").value === "careful";
}
$("speed").onchange = describeSpeed;

function settingsDirty() {
  if (!savedSettings) return false;
  return String($("cap").value) !== String(savedSettings.daily_action_cap)
    || String($("months").value) !== String(savedSettings.inactivity_months)
    || String($("rescan-days").value) !== String(savedSettings.rescan_after_days)
    || $("speed").value !== savedSettings.speed;
}

async function leaveSettings() {
  if (settingsDirty() && !(await askConfirm("You changed a setting but did not save. Leave without saving?"))) return;
  savedSettings = null;
  refresh();
}

$("gear").onclick = async () => {
  if (activeView() === "settings") return leaveSettings();
  const s = await api("GET", "/api/settings");
  savedSettings = s;
  $("cap").value = s.daily_action_cap;
  $("months").value = s.inactivity_months;
  $("rescan-days").value = s.rescan_after_days;
  $("speed").value = s.speed;
  speedPresets = s.speed_presets;
  describeSpeed();
  const backups = await api("GET", "/api/backups");
  const box = $("backups");
  box.innerHTML = backups.length ? "" : "<p class='muted'>No backups yet.</p>";
  for (const b of backups) {
    const line = document.createElement("p");
    line.appendChild(document.createTextNode(backupLabel(b.name) + " "));
    const span = document.createElement("span");
    span.className = "muted";
    span.textContent = `(${fmt(b.count)} accounts)`;
    line.appendChild(span);
    line.appendChild(document.createTextNode(" "));
    const btn = document.createElement("button");
    btn.className = "small";
    btn.textContent = "Choose who to follow again";
    btn.onclick = () => openRestore(b.name);
    line.appendChild(btn);
    box.appendChild(line);
  }
  show("settings");
};

// "unfollow-backup-2026-09-14-1830.csv" -> "Backup from 2026-09-14 at 18:30"
function backupLabel(name) {
  const m = /(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})/.exec(name);
  return m ? `Backup from ${m[1]} at ${m[2]}:${m[3]}` : name;
}

let restoreName = null;

async function openRestore(name) {
  let data;
  try { data = await api("GET", `/api/backups/${encodeURIComponent(name)}`); }
  catch (e) { return notify("That backup file no longer exists."); }
  restoreName = name;
  $("restore-title").textContent = backupLabel(name);
  const box = $("restore-rows");
  box.innerHTML = "";
  for (const r of data.rows) {
    box.appendChild(rowEl({
      username: r.username, display_name: r.display_name, profile_pic_url: null,
      last_post_date: r.last_post_date, selected: true,
    }, { remote: false }));
  }
  $("restore-search").value = "";
  show("restore");
}

$("restore-search").oninput = () => {
  const q = $("restore-search").value.toLowerCase();
  $("restore-rows").querySelectorAll(".row").forEach((r) => r.hidden = q && !r.dataset.search.includes(q));
};
$("restore-check-all").onclick = () => $("restore-rows").querySelectorAll("input[type=checkbox]").forEach((c) => c.checked = true);
$("restore-uncheck-all").onclick = () => $("restore-rows").querySelectorAll("input[type=checkbox]").forEach((c) => c.checked = false);
$("restore-back").onclick = () => { restoreName = null; $("gear").click(); };
$("restore-selected").onclick = async () => {
  const usernames = Array.from($("restore-rows").querySelectorAll(".row"))
    .filter((r) => r.querySelector("input[type=checkbox]").checked)
    .map((r) => r.dataset.username);
  if (usernames.length === 0) return notify("Nothing is selected.");
  const cap = state && state.settings ? state.settings.daily_action_cap : 100;
  if (!(await askConfirm(`Follow ${fmt(usernames.length)} accounts again, at about ${fmt(cap)} per day?`))) return;
  try { await api("POST", "/api/restore/start", { name: restoreName, usernames }); }
  catch (e) { notify(errorText(e)); }
  restoreName = null;
  refreshUnlessFinished();
};
$("save-settings").onclick = async () => {
  const speed = $("speed").value;
  if (speed !== "careful" && savedSettings && speed !== savedSettings.speed) {
    const ok = await askConfirm("Faster speeds make it more likely that Instagram restricts or disables the account. Save anyway?");
    if (!ok) return;
  }
  try {
    await api("PUT", "/api/settings", {
      daily_action_cap: parseInt($("cap").value, 10),
      inactivity_months: parseInt($("months").value, 10),
      rescan_after_days: parseInt($("rescan-days").value, 10),
      speed,
    });
    savedSettings = null;
    refresh();
  } catch (e) { notify("All three numbers must be 1 or more."); }
};
$("cancel-settings").onclick = leaveSettings;
$("start-over").onclick = async () => {
  if (!(await askConfirm("Wipe the scan results and your keep list? Backups are kept. You will need to scan again from the beginning."))) return;
  try { await api("POST", "/api/reset"); } catch (e) { notify(errorText(e)); }
  savedSettings = null;
  refresh();
};

// ---- Live updates -------------------------------------------------------------------
function listen() {
  const es = new EventSource("/api/events");
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    const previous = lastEvent;
    lastEvent = ev;
    if (ev.replay && ev.state !== "running") return;  // old news after a reload: refresh() already routed
    const view = activeView();
    const watching = !view || view === "home" || view === "progress" || view === "login";
    if (ev.state === "running") {
      if (watching) renderProgress(ev);
    } else if (ev.state === "paused" && ev.message === DONE_TODAY) {
      if (watching) showFinished(ev.job_id, "cap");
    } else if (ev.state === "paused") {
      if (watching) renderProgress(ev);
    } else if (ev.state === "done") {
      const wasWatching = previous && previous.job_id === ev.job_id && view === "progress";
      if (wasWatching || watching) showFinished(ev.job_id, "done");
    } else if (view === "progress") {
      refresh();
    }
  };
  es.onerror = () => setTimeout(() => { es.close(); listen(); }, 2000);
}

loadTheme();
listen();
refresh();
setInterval(() => { if (state && !state.logged_in) refresh(); }, 3000);
