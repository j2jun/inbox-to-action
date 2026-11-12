// Today page: renders tasks from /api/tasks and syncs every change back to Notion through the API.
"use strict";

const POLL_MS = 30_000;
const TOAST_MS = 5_000;
const $ = (id) => document.getElementById(id);
let tasks = [];
let pending = 0;
let lastMoved = null;
let menu = null;

// ---------- dates ----------
const pad = (n) => String(n).padStart(2, "0");
const isoDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
function dueDate(t) {
  if (!t.due) return null;
  if (t.has_time) return new Date(t.due);
  const [y, m, d] = t.due.split("-").map(Number);
  return new Date(y, m - 1, d);
}
function dayDiff(t) {
  const d = dueDate(t);
  return d === null ? null : Math.round((startOfDay(d) - startOfDay(new Date())) / 86_400_000);
}
function whenLabel(t) {
  const d = dueDate(t), diff = dayDiff(t);
  if (d === null) return "";
  const time = t.has_time ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
  let day = "";
  if (diff < 0 || diff > 7) day = d.toLocaleDateString([], { month: "short", day: "numeric" });
  else if (diff > 1) day = d.toLocaleDateString([], { weekday: "short" });
  return [day, time].filter(Boolean).join(" · ");
}
function addDays(n) { const d = new Date(); d.setDate(d.getDate() + n); return isoDate(d); }
function nextWeekday(target) { // 0=Sun … 5=Fri; today counts if it matches
  const d = new Date(); const ahead = (target - d.getDay() + 7) % 7; return addDays(ahead);
}

const SECTIONS = [
  ["Needs your OK", (t) => t.status === "Review", "review"],
  ["Overdue", (t) => t.due !== null && dayDiff(t) < 0, "overdue"],
  ["Today", (t) => dayDiff(t) === 0],
  ["Tomorrow", (t) => dayDiff(t) === 1],
  ["This week", (t) => dayDiff(t) >= 2 && dayDiff(t) <= 7],
  ["Later", (t) => dayDiff(t) > 7],
  ["Someday", (t) => t.due === null],
];
const SNOOZE = () => [
  ["Later today", addDays(0)], ["Tomorrow", addDays(1)], ["Friday", nextWeekday(5)],
  ["Next week", addDays(((8 - new Date().getDay()) % 7) || 7)], ["Someday (no date)", null],
];
const PRIORITY_NAMES = { P1: "Urgent", P2: "This week", P3: "Later", P4: "FYI" };

// ---------- safe DOM helpers ----------
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c) node.append(c);
  return node;
}
const safeLink = (url) => (/^https:\/\//i.test(url || "") ? url : null);
const safeTel = (phone) => (phone && /^\+?[\d\s().-]{7,20}$/.test(phone) ? "tel:" + phone.replace(/[^\d+]/g, "") : null);

// ---------- render ----------
function render() {
  const now = new Date();
  $("dateLabel").textContent = now.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
  const open = tasks.filter((t) => t.status === "To do");
  const calls = open.filter((t) => t.type === "Call" && t.due !== null && dayDiff(t) >= 0 && dayDiff(t) <= 7).length;
  $("summary").textContent = `${open.length} open · ${open.filter((t) => t.priority === "P1").length} urgent · ${calls} call${calls === 1 ? "" : "s"} this week`;

  const flow = $("flow");
  flow.replaceChildren();
  for (const [name, match, cls] of SECTIONS) {
    const list = tasks.filter((t) => t.status !== "Cancelled" && (cls === "review") === (t.status === "Review") && match(t)).sort((a, b) =>
      (dueDate(a) ?? Infinity) - (dueDate(b) ?? Infinity) || a.priority.localeCompare(b.priority));
    if (!list.length) continue;
    const sec = el("section", { class: `sec ${cls || ""}`, "aria-label": name },
      el("h2", {}, name, el("span", { class: "count", text: String(list.length) })));
    list.forEach((t) => sec.append(row(t)));
    flow.append(sec);
  }
  $("empty").hidden = tasks.length > 0;
  lastMoved = null;
}

function row(t) {
  const p = t.priority.toLowerCase();
  const item = el("div", { class: `item ${p} ${t.type === "Call" ? "call" : ""} ${t.status === "Done" ? "done" : ""} ${lastMoved === t.id ? "moved" : ""}`, "data-id": t.id });
  const box = el("input", { type: "checkbox", id: `c-${t.id}`, checked: t.status === "Done" });
  box.addEventListener("change", () => change(t, { done: box.checked }, box.checked ? `Done: ${t.title}` : `Reopened: ${t.title}`));
  const meta = el("div", { class: "meta" });
  const when = whenLabel(t);
  if (when) meta.append(el("span", { class: "when", text: when }));
  meta.append(el("span", { text: t.type === "Call" ? (t.platform || "Call") : t.type }));
  if (t.from) meta.append(el("span", { class: t.vip ? "vip" : "", text: (t.vip ? "★ " : "") + t.from }));
  if (t.status === "Review") {  // outside sender: show the real address so a lookalike name can't hide it
    meta.append(el("span", { class: "addr", text: t.from_addr }));
    const ok = el("button", { type: "button", class: "btn approve", text: "Approve" });
    ok.addEventListener("click", () => change(t, { approve: true }, `Added: ${t.title}`));
    const no = el("button", { type: "button", class: "btn dismiss", text: "Dismiss" });
    no.addEventListener("click", () => change(t, { approve: false }, `Dismissed: ${t.title}`));
    item.append(el("span", { class: "node", "aria-hidden": "true" }),
      el("div", { class: "line" }, el("span", { class: "t", text: t.title })), meta, el("div", { class: "acts" }, ok, no));
    return item;
  }

  const acts = el("div", { class: "acts" });
  const join = safeLink(t.join_link), tel = safeTel(t.phone);
  if (join) acts.append(el("a", { class: "btn join", href: join, target: "_blank", rel: "noopener noreferrer", "aria-label": `Join ${t.title}${t.platform ? " on " + t.platform : ""}`, text: "Join" }));
  else if (tel) acts.append(el("a", { class: "btn join", href: tel, "aria-label": `Call ${t.phone}`, text: "Call" }));
  const prio = el("button", { type: "button", class: "btn prio", "aria-haspopup": "menu", "aria-label": `Priority ${t.priority}, change`, title: t.reason || "", text: t.priority });
  prio.addEventListener("click", (e) => { e.stopPropagation(); openMenu(prio, priorityItems(t)); });
  const snooze = el("button", { type: "button", class: "btn snooze", "aria-haspopup": "menu", text: "Snooze" });
  snooze.addEventListener("click", (e) => { e.stopPropagation(); openMenu(snooze, snoozeItems(t)); });
  acts.append(prio, snooze);

  item.append(el("span", { class: "node", "aria-hidden": "true" }),
    el("div", { class: "line" }, box, el("label", { class: "t", for: `c-${t.id}`, text: t.title })), meta, acts);
  return item;
}

// ---------- menus ----------
function priorityItems(t) {
  return ["P1", "P2", "P3", "P4"].map((p) => ({
    label: `${p} ${PRIORITY_NAMES[p]}`, cls: p.toLowerCase(), current: p === t.priority,
    run: () => change(t, { priority: p }, `Priority → ${p}: ${t.title}`),
  }));
}
function snoozeItems(t) {
  return SNOOZE().filter(([, due]) => due !== (t.has_time ? null : t.due) || (due === null && t.due !== null))
    .map(([label, due]) => ({ label, run: () => change(t, { due }, `Snoozed to ${label}: ${t.title}`) }));
}
function closeMenu(focusBack) {
  if (!menu) return;
  const opener = menu.opener; menu.remove(); menu = null;
  if (focusBack) opener.focus();
}
function openMenu(button, items) {
  closeMenu();
  menu = el("div", { class: "menu", role: "menu" });
  menu.opener = button;
  for (const it of items) {
    const b = el("button", { type: "button", role: "menuitem", class: it.cls || "" });
    if (it.cls) b.append(el("span", { class: "dot", "aria-hidden": "true" }));
    b.append(it.label);
    if (it.current) b.append(el("small", { text: "current" }));
    b.addEventListener("click", () => { closeMenu(); it.run(); });
    menu.append(b);
  }
  menu.addEventListener("keydown", (e) => {
    const list = [...menu.querySelectorAll("button")], i = list.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); list[(i + 1) % list.length].focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); list[(i - 1 + list.length) % list.length].focus(); }
    else if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); closeMenu(true); }
  });
  const app = $("app"), r = button.getBoundingClientRect(), ar = app.getBoundingClientRect();
  menu.style.top = `${r.bottom - ar.top + 6}px`;
  menu.style.right = `${Math.max(ar.right - r.right, 0)}px`;
  app.append(menu);
  menu.querySelector("button").focus();
}
document.addEventListener("click", (e) => { if (menu && !menu.contains(e.target)) closeMenu(); });

// ---------- sync ----------
function setSync(state, text) {
  const s = $("sync"); s.className = `sync ${state}`; s.querySelector("span").textContent = text;
}
let toastTimer;
function toast(msg, actionLabel, action, isError = false) {
  const box = $("toast"), btn = $("toastAction");
  $("toastMsg").textContent = msg;
  box.classList.toggle("error", isError);
  btn.textContent = actionLabel;
  btn.onclick = () => { box.hidden = true; action(); };
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (box.hidden = true), isError ? TOAST_MS * 2 : TOAST_MS);
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method, credentials: "same-origin",
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) { showLogin(); throw new Error("login required"); }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.status === 204 ? null : res.json();
}

function snapshot(t) { return { status: t.status, priority: t.priority, due: t.due, has_time: t.has_time }; }
function applyLocal(t, patch) {
  if ("done" in patch) t.status = patch.done ? "Done" : "To do";
  if ("approve" in patch) t.status = patch.approve ? "To do" : "Cancelled";
  if ("review" in patch) t.status = "Review";
  if ("priority" in patch) t.priority = patch.priority;
  if ("due" in patch) { t.due = patch.due; t.has_time = false; }
}
function inverse(before, patch) {
  const inv = {};
  if ("done" in patch) inv.done = before.status === "Done";
  if ("approve" in patch) inv.review = true;
  if ("priority" in patch) inv.priority = before.priority;
  if ("due" in patch) inv.due = before.due;
  return inv;
}

async function change(t, patch, message, isUndo = false) {
  const before = snapshot(t);
  applyLocal(t, patch);
  lastMoved = "due" in patch ? t.id : null;
  render();
  pending++;
  setSync("busy", "Saving to Notion…");
  try {
    const saved = await api("PATCH", `/api/tasks/${encodeURIComponent(t.id)}`, patch);
    Object.assign(t, saved);
    setSync("", "Synced with Notion · just now");
    if (!isUndo) toast(message, "Undo", () => change(t, inverse(before, patch), `Undone: ${t.title}`, true));
  } catch (err) {
    Object.assign(t, before);
    if (err.message !== "login required") {
      setSync("error", "Not saved");
      toast("Couldn't save to Notion", "Retry", () => change(t, patch, message, isUndo), true);
    }
  } finally {
    pending--;
    render();
    document.querySelector(`.item[data-id="${CSS.escape(t.id)}"] .prio`)?.focus({ preventScroll: true });
  }
}

async function addTask(text) {
  setSync("busy", "Saving to Notion…");
  try {
    const t = await api("POST", "/api/tasks", { text });
    tasks.push(t);
    lastMoved = t.id;
    render();
    setSync("", "Synced with Notion · just now");
    toast(`Added: ${t.title}`, "Undo", () => change(t, { done: true }, "", true));
    return true;
  } catch (err) {
    if (err.message !== "login required") {
      setSync("error", "Not saved");
      toast("Couldn't add the task", "Retry", () => addTask(text), true);
    }
    return false;
  }
}

async function refresh() {
  if (pending > 0 || document.visibilityState !== "visible") return;
  try {
    const data = await api("GET", "/api/tasks");
    if (pending > 0) return;
    // Keep tasks ticked this session visible (struck through) until the next full reload.
    const doneHere = tasks.filter((t) => t.status === "Done" && !data.tasks.some((n) => n.id === t.id));
    tasks = [...data.tasks, ...doneHere];
    showMain();
    render();
    setSync("", "Synced with Notion · just now");
  } catch (err) {
    if (err.message !== "login required") setSync("error", "Offline · retrying");
  }
}

// ---------- login ----------
function showLogin() {
  $("mainView").hidden = true; $("loginView").hidden = false;
  setSync("error", "Signed out"); $("summary").textContent = "Sign in to see your tasks";
  $("password").focus();
}
function showMain() { $("loginView").hidden = true; $("mainView").hidden = false; }
$("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const res = await fetch("/login", { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: $("password").value }) });
  if (res.ok) { $("password").value = ""; $("loginError").textContent = ""; refresh(); }
  else $("loginError").textContent = "That password didn't work.";
});

$("addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("addInput"), text = input.value.trim();
  if (!text) return;
  input.value = "";
  if (!(await addTask(text))) input.value = text;
});

document.addEventListener("visibilitychange", refresh);
setInterval(refresh, POLL_MS);
refresh();
