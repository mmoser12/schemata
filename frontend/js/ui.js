// Small DOM toolkit: element builder, icons, toasts, dialogs, formatting.
// Everything user-controlled goes through textContent (never innerHTML), so
// file names and column headers can't inject markup.

const SVG_NS = "http://www.w3.org/2000/svg";

export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  applyProps(el, props);
  append(el, children);
  return el;
}

export function s(tag, props = {}, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  applyProps(el, props);
  append(el, children);
  return el;
}

function applyProps(el, props) {
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.setAttribute("class", Array.isArray(v) ? v.filter(Boolean).join(" ") : v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "text") el.textContent = v;
    else if (k === "value" && "value" in el) el.value = v;
    else if (k === "checked" || k === "selected" || k === "disabled" || k === "multiple") el[k] = Boolean(v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false || c === true) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el) {
  while (el.firstChild) el.firstChild.remove();
  return el;
}

// ---------------------------------------------------------------- icons
// Simple 24x24 stroke icons drawn for this app.
const ICONS = {
  check: "M5 12.5l4.5 4.5L19 7.5",
  "check-circle": "M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18zM8 12.5l2.8 2.8L16.5 9.5",
  alert: "M12 4L2.8 19.5h18.4zM12 10v4.5M12 17.2v.3",
  error: "M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18zM9 9l6 6M15 9l-6 6",
  info: "M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18zM12 11v5.5M12 7.8v.3",
  upload: "M12 16V4M7 9l5-5l5 5M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3",
  download: "M12 4v12M7 11l5 5l5-5M4 17v1a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-1",
  play: "M7 4.5v15l12.5-7.5z",
  refresh: "M20 11a8 8 0 0 0-14.6-4.5M4 4v4h4M4 13a8 8 0 0 0 14.6 4.5M20 20v-4h-4",
  trash: "M4 7h16M9 7V4.5h6V7M6.5 7l1 13h9l1-13M10 11v6M14 11v6",
  file: "M14 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V8zM14 3v5h5",
  table: "M4 5h16v14H4zM4 10h16M4 14.5h16M10 5v14",
  eye: "M2.5 12S6 5.5 12 5.5S21.5 12 21.5 12S18 18.5 12 18.5S2.5 12 2.5 12zM12 9a3 3 0 1 0 0 6a3 3 0 0 0 0-6z",
  undo: "M9 14L4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11",
  redo: "M15 14l5-5l-5-5M20 9H9.5a5.5 5.5 0 0 0 0 11H13",
  sun: "M12 8a4 4 0 1 0 0 8a4 4 0 0 0 0-8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  moon: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z",
  auto: "M12 3a9 9 0 1 0 0 18zM12 3a9 9 0 0 1 0 18",
  help: "M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18zM9.5 9.3a2.6 2.6 0 0 1 5 .9c0 1.7-2.5 2.3-2.5 4M12 17.2v.3",
  plus: "M12 5v14M5 12h14",
  edit: "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
  chevron: "M6 9l6 6l6-6",
  "chevron-right": "M9 6l6 6l-6 6",
  arrow: "M5 12h14M13 6l6 6l-6 6",
  folder: "M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z",
  copy: "M8 8h11v12H8zM5 16V4h11",
  clock: "M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18zM12 7.5V12l3 2",
  zap: "M13 2L4.5 13.5H12L11 22l8.5-11.5H12z",
  columns: "M4 5h16v14H4zM9.5 5v14M14.5 5v14",
  flow: "M5 4h5v5H5zM14 15h5v5h-5zM7.5 9v3.5a2 2 0 0 0 2 2H14",
  activity: "M3 12h4l3-8l4 16l3-8h4",
  code: "M8.5 7L3.5 12l5 5M15.5 7l5 5l-5 5",
  chart: "M4 20V10M10 20V4M16 20v-7M2.5 20h19",
  x: "M6 6l12 12M18 6L6 18",
  sparkle: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7L11.5 6.8M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.5-1.5",
  lock: "M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3",
  beaker: "M9 3h6M10 3v6L4.5 19A1.5 1.5 0 0 0 5.8 21h12.4a1.5 1.5 0 0 0 1.3-2L14 9V3M7.5 14h9",
};

export function icon(name, extra = {}) {
  const path = ICONS[name] || ICONS.info;
  return s("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.8",
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", ...extra },
  s("path", { d: path }));
}

export const SEV_ICON = { error: "error", warning: "alert", info: "info" };
export const SEV_LABEL = { error: "Error", warning: "Warning", info: "Note" };

export function sev(severity, text) {
  return h("span", { class: `sev ${severity}` }, icon(SEV_ICON[severity]),
    h("span", { class: "sr-label", text: text ?? SEV_LABEL[severity] }));
}

export function badge(text, kind = "", iconName = null, title = null) {
  return h("span", { class: `badge ${kind}`, title }, iconName ? icon(iconName) : null, text);
}

export function button(label, opts = {}) {
  const { kind = "", iconName = null, onClick, title, disabled, type = "button", size = "" } = opts;
  return h("button", { class: `btn ${kind} ${size}`.trim(), type, title, disabled, onClick },
    iconName ? icon(iconName) : null, label ? h("span", { text: label }) : null);
}

export function iconButton(name, label, onClick, extra = {}) {
  return h("button", { class: `icon-btn ${extra.size || ""}`.trim(), type: "button", "aria-label": label,
    title: label, onClick, disabled: extra.disabled }, icon(name));
}

export function spinner() {
  return h("span", { class: "spinner", role: "presentation" });
}

// ---------------------------------------------------------------- toasts
export function toast(message, kind = "", ms = 4200) {
  const host = document.getElementById("toasts");
  const close = h("button", { "aria-label": "Dismiss", onClick: () => el.remove() }, icon("x", { width: 14, height: 14 }));
  const el = h("div", { class: `toast ${kind}` },
    icon(kind === "bad" ? "error" : "check-circle"), h("div", { class: "grow", text: message }), close);
  host.append(el);
  setTimeout(() => el.remove(), kind === "bad" ? ms * 2 : ms);
}

export function toastError(err) {
  toast(err && err.message ? err.message : String(err), "bad");
}

// ---------------------------------------------------------------- dialogs
export function openDialog({ title, body, actions = [], wide = false, onClose }) {
  const dlg = h("dialog", { class: wide ? "wide" : "", "aria-label": title });
  const close = () => dlg.close();
  const footer = actions.length ? h("div", { class: "dialog-foot" }, actions.map((a) =>
    button(a.label, { kind: a.kind, iconName: a.icon, disabled: a.disabled,
      onClick: async () => { if (a.onClick) { const keep = await a.onClick(close); if (keep === true) return; } close(); } }))) : null;
  dlg.append(
    h("div", { class: "dialog-head" }, h("h2", { text: title }), iconButton("x", "Close", close)),
    h("div", { class: "dialog-body" }, body),
    footer,
  );
  dlg.addEventListener("close", () => { dlg.remove(); if (onClose) onClose(); });
  document.body.append(dlg);
  dlg.showModal();
  const first = dlg.querySelector(".dialog-body input:not([type=hidden]), .dialog-body select, .dialog-body textarea");
  if (first) first.focus();
  return { dialog: dlg, close };
}

export function confirmDialog({ title, message, confirm = "Confirm", kind = "primary" }) {
  return new Promise((resolve) => {
    let answered = false;
    openDialog({
      title,
      body: h("p", { class: "muted", text: message }),
      actions: [
        { label: "Cancel", onClick: () => { answered = true; resolve(false); } },
        { label: confirm, kind, onClick: () => { answered = true; resolve(true); } },
      ],
      onClose: () => { if (!answered) resolve(false); },
    });
  });
}

// ---------------------------------------------------------------- formatting
export function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 ** 2).toFixed(1)} MB`;
}

export function fmtSeconds(sec) {
  if (sec === null || sec === undefined) return "—";
  if (sec < 1) return `${Math.round(sec * 1000)} ms`;
  if (sec < 60) return `${sec.toFixed(sec < 10 ? 2 : 1)} s`;
  return `${Math.floor(sec / 60)} min ${Math.round(sec % 60)} s`;
}

export function fmtNum(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a !== 0 && (a < 0.001 || a >= 1e6)) return v.toExponential(2);
  return Number(v.toPrecision(4)).toLocaleString();
}

export function relTime(ts) {
  if (!ts) return "never";
  const d = Date.now() / 1000 - ts;
  if (d < 10) return "just now";
  if (d < 60) return `${Math.round(d)} s ago`;
  if (d < 3600) return `${Math.round(d / 60)} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  if (d < 86400 * 7) return `${Math.round(d / 86400)} d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export function absTime(ts) {
  return ts ? new Date(ts * 1000).toLocaleString() : "";
}

export function plural(n, word, many = `${word}s`) {
  return `${n} ${n === 1 ? word : many}`;
}

export function debounce(fn, ms) {
  let t;
  const wrapped = (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  wrapped.flush = (...args) => { clearTimeout(t); fn(...args); };
  wrapped.cancel = () => clearTimeout(t);
  return wrapped;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to clipboard");
  } catch {
    toast("Couldn't copy; select the text and copy it manually", "bad");
  }
}

// localStorage can throw (private windows, blocked storage): treat it as optional.
export const prefs = {
  get(key, fallback) {
    try { const v = localStorage.getItem(`schemata:${key}`); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(`schemata:${key}`, JSON.stringify(value)); } catch { /* optional */ }
  },
};

// Minimal CSV parser for previewing output files (handles quotes and newlines in quotes).
export function parseCsv(text, maxRows = 200) {
  const rows = [];
  let row = [], cell = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') q = false;
      else cell += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); rows.push(row); row = []; cell = "";
      if (rows.length > maxRows) break;
    } else cell += c;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows;
}

export function simpleTable(columns, rows, { numeric = () => false } = {}) {
  return h("div", { class: "table-wrap" }, h("table", { class: "data-table" },
    h("thead", {}, h("tr", {}, columns.map((c, i) => h("th", { class: numeric(i) ? "num" : "", text: c })))),
    h("tbody", {}, rows.map((r) => h("tr", {}, r.map((v, i) => h("td", { class: numeric(i) ? "num" : "", text: v })))))));
}
