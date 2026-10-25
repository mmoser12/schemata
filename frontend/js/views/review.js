// Review: every column of every file, what it maps to, how values change, and what's wrong.
// Edits are saved immediately as overrides (a recipe on top of the automatic plan), with undo/redo.
import { api, P } from "../api.js";
import { badge, button, clear, confirmDialog, copyText, h, icon, iconButton, openDialog, plural, sev, toast,
  toastError } from "../ui.js";

const METHOD = {
  rule: ["exact name", "good"], fuzzy: ["similar name", "info"], llm: ["AI guess", "info"], user: ["set by you", "accent"],
};
const UNIT_SOURCE = {
  header: ["unit from header", "good"], inferred: ["unit inferred from values", "warn"],
  default: ["unit assumed", "warn"], user: ["unit set by you", "accent"],
};

const DATE_TOKENS = { "%Y": "YYYY", "%m": "MM", "%d": "DD", "%H": "hh", "%M": "mm", "%S": "ss", "%b": "Mon", "%B": "Month" };
function humanDate(f) {
  if (f === "ISO8601") return "ISO 8601 (YYYY-MM-DDThh:mm)";
  if (f === "mixed") return "Mixed (guess per value)";
  if (f === "native") return "Excel dates";
  return Object.entries(DATE_TOKENS).reduce((t, [k, v]) => t.split(k).join(v), f);
}

export async function mountReview(root, ctx) {
  const { pid, app } = ctx;
  const unitLabel = new Map(app.meta.units.flatMap((g) => g.units.map((u) => [u.unit, u.label])));
  const unitsByDim = new Map();
  app.meta.units.forEach((g) => g.units.forEach((u) => {
    if (!unitsByDim.has(u.dimension)) unitsByDim.set(u.dimension, []);
    unitsByDim.get(u.dimension).push(u);
  }));
  const pretty = (u) => (u ? unitLabel.get(u) || u : "");

  let data = null;
  let overrides = {};
  const undo = [];
  const redo = [];
  const collapsed = new Set();
  let showSev = new Set(["error", "warning"]);
  let saving = 0;
  let seq = 0;

  const page = h("div", { class: "stack" });
  root.append(page);
  const status = h("span", { class: "small muted", role: "status", text: "No unsaved changes" });

  async function load() {
    data = await api(P(pid, "/plan"));
    overrides = structuredClone(data.overrides);
    initCollapse();
    render();
  }

  function initCollapse() {
    const files = data.plan.files;
    if (files.length <= 3) return;
    for (const f of files) {
      const hasProblems = data.issues.some((i) => i.file === f.file && i.severity !== "info");
      if (!data.unapproved.includes(f.file) && !hasProblems) collapsed.add(f.file);
    }
  }

  // ---------------------------------------------------------------- editing
  function change(mutator) {
    undo.push(structuredClone(overrides));
    if (undo.length > 100) undo.shift();
    redo.length = 0;
    mutator();
    save();
  }

  function ov(file, col) {
    overrides[file] ||= {};
    overrides[file][col] ||= {};
    return overrides[file][col];
  }

  function tidy(file, col) {
    if (overrides[file] && overrides[file][col] && !Object.keys(overrides[file][col]).length) delete overrides[file][col];
    if (overrides[file] && !Object.keys(overrides[file]).length) delete overrides[file];
  }

  async function save() {
    const mine = ++seq;
    saving++;
    status.textContent = "Saving…";
    try {
      const next = await api(P(pid, "/overrides"), { method: "PUT", body: { overrides } });
      if (mine === seq) {
        data = next;
        render();
      }
      ctx.refresh();
    } catch (e) {
      toastError(e);
    } finally {
      saving--;
      if (!saving) status.textContent = "All changes saved";
    }
  }

  function doUndo() {
    if (!undo.length) return;
    redo.push(structuredClone(overrides));
    overrides = undo.pop();
    save();
    toast("Undone");
  }

  function doRedo() {
    if (!redo.length) return;
    undo.push(structuredClone(overrides));
    overrides = redo.pop();
    save();
  }

  // ---------------------------------------------------------------- render
  function render() {
    const focusKey = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.key : null;
    const scrollY = window.scrollY;
    clear(page);
    if (!data.plan.files.length) {
      page.append(h("div", { class: "card" }, h("div", { class: "empty" }, icon("columns", { class: "big" }),
        h("h3", { text: "Nothing to review yet" }), h("p", { text: "Add files and Schemata will propose how each column maps onto your schema." }),
        h("div", { class: "btn-group" }, button("Add files", { kind: "primary", iconName: "upload", onClick: () => ctx.goto("files") })))));
      return;
    }
    if (!Object.keys(data.plan.targets).length) {
      page.append(h("div", { class: "callout warn" }, icon("alert"), h("div", { class: "grow" },
        h("strong", { text: "Your schema has no columns yet" }),
        h("p", { text: "Every column would be dropped. Set up the schema first (Schemata can suggest it from your files)." })),
      button("Set up schema", { kind: "primary", onClick: () => ctx.goto("schema") })));
    }

    page.append(h("div", { class: "section-head" },
      h("div", {}, h("h2", { text: "Review the mapping" }),
        h("p", { class: "muted", text: "Check how each column will be cleaned. Nothing is applied until you approve; every change is logged." })),
      h("div", { class: "btn-group" },
        iconButton("undo", "Undo (Ctrl+Z)", doUndo, { disabled: !undo.length }),
        iconButton("redo", "Redo (Ctrl+Shift+Z)", doRedo, { disabled: !redo.length }),
        button("View clean.py", { iconName: "code", onClick: showScript }))));

    page.append(approvalBanner());
    page.append(issuesPanel());
    const order = [...data.plan.files].sort((a, b) =>
      Number(data.unapproved.includes(b.file)) - Number(data.unapproved.includes(a.file)));
    order.forEach((fp) => page.append(fileCard(fp)));
    page.append(actions());

    window.scrollTo(0, scrollY);
    if (focusKey) {
      const el = page.querySelector(`[data-key="${CSS.escape(focusKey)}"]`);
      if (el) el.focus({ preventScroll: true });
    }
  }

  function approvalBanner() {
    if (data.needs_approval) {
      const n = data.unapproved.length;
      return h("div", { class: "callout warn" }, icon("alert"), h("div", { class: "grow" },
        h("strong", { text: n ? `${plural(n, "file")} new or changed since the last approval` : "The mapping changed since the last approval" }),
        h("p", { class: "small", text: "Look over the highlighted rows, then approve to regenerate clean.py and run the pipeline." })));
    }
    return h("div", { class: "callout good" }, icon("check-circle"), h("div", { class: "grow" },
      h("strong", { text: "Approved" }),
      h("p", { class: "small", text: "clean.py matches this mapping. Any edit here will need a new approval." })));
  }

  function issuesPanel() {
    const counts = data.counts;
    const visible = data.issues.filter((i) => showSev.has(i.severity));
    const chip = (sevName, label) => h("button", { class: "chip", type: "button", "aria-pressed": String(showSev.has(sevName)),
      onClick: () => { showSev.has(sevName) ? showSev.delete(sevName) : showSev.add(sevName); showSev = new Set(showSev); render(); } },
    sev(sevName, `${counts[sevName]} ${label}`));
    const list = h("ul", { class: "issue-list" }, visible.slice(0, 200).map((i) => h("li", {},
      sev(i.severity, ""),
      h("div", { class: "grow" },
        h("div", { class: "small" }, i.file ? h("strong", { text: i.file }) : null, i.column ? [" · ", h("code", { text: i.column })] : null),
        h("div", { class: "small", text: i.message })),
      i.file && i.column && rowFor(i.file, i.column)
        ? h("button", { class: "link", type: "button", onClick: () => reveal(i.file, i.column), text: "Show" }) : null)));
    return h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h3", { class: "grow", text: "Issues" }),
        h("div", { class: "filter-chips" }, chip("error", counts.error === 1 ? "error" : "errors"),
          chip("warning", counts.warning === 1 ? "warning" : "warnings"), chip("info", counts.info === 1 ? "note" : "notes"))),
      visible.length ? list : h("p", { class: "card-body muted small", text: counts.error + counts.warning
        ? "No issues match the selected filters." : "No errors or warnings. Everything mapped cleanly." }));
  }

  function rowFor(file, col) {
    return (data.examples[file] || {})[col] !== undefined;
  }

  function reveal(file, col) {
    if (collapsed.has(file)) { collapsed.delete(file); render(); }
    const tr = page.querySelector(`tr[data-row="${CSS.escape(`${file}::${col}`)}"]`);
    if (!tr) return;
    tr.scrollIntoView({ behavior: "smooth", block: "center" });
    tr.classList.remove("flash");
    void tr.offsetWidth;
    tr.classList.add("flash");
    const ctl = tr.querySelector("select");
    if (ctl) ctl.focus({ preventScroll: true });
  }

  function fileCard(fp) {
    const file = fp.file;
    const isNew = data.unapproved.includes(file);
    const issues = data.issues.filter((i) => i.file === file);
    const nErr = issues.filter((i) => i.severity === "error").length;
    const nWarn = issues.filter((i) => i.severity === "warning").length;
    const isCollapsed = collapsed.has(file);
    const toggle = () => { isCollapsed ? collapsed.delete(file) : collapsed.add(file); render(); };

    const head = h("div", { class: "card-head", role: "button", tabindex: 0, "aria-expanded": String(!isCollapsed),
      onClick: toggle, onKeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } } },
    icon("chevron", { class: "chev", width: 18, height: 18 }),
    h("strong", { class: "ellipsis", text: file }),
    isNew ? badge("Needs review", "warn", "alert") : badge("Approved", "good", "check"),
    nErr ? badge(plural(nErr, "error"), "bad", "error") : null,
    nWarn ? badge(plural(nWarn, "warning"), "warn", "alert") : null,
    h("span", { class: "grow" }),
    h("span", { class: "small faint", text: data.read_options[file] ? data.read_options[file].summary : "" }));

    const cols = Object.keys(data.examples[file] || {});
    const rows = [];
    for (const col of cols) {
      const m = fp.mappings.find((x) => x.source === col) || null;
      const colIssues = issues.filter((i) => i.column === col);
      const worst = colIssues.some((i) => i.severity === "error") ? "row-error"
        : colIssues.some((i) => i.severity === "warning") ? "row-warn" : "";
      rows.push(h("tr", { class: worst, "data-row": `${file}::${col}` },
        h("td", {}, h("div", { class: "src-name", text: col }),
          h("div", { class: "examples", title: (data.examples[file][col] || []).join(", "), text: (data.examples[file][col] || []).join(", ") || "(empty)" })),
        h("td", {}, targetSelect(file, col, m, fp)),
        h("td", {}, unitControl(file, col, m)),
        h("td", {}, previewCell(file, col, m)),
        h("td", {}, howCell(file, col, m)),
      ));
      if (colIssues.some((i) => i.severity !== "info") || (m && colIssues.length && worst)) {
        rows.push(h("tr", { class: worst }, h("td", { colspan: 5, style: { paddingTop: "0" } },
          h("div", { class: "row-issues" }, colIssues.filter((i) => i.severity !== "info").map((i) =>
            h("span", { class: `sev ${i.severity}` }, sev(i.severity, "").firstChild, h("span", { text: i.message })))))));
      }
    }
    const missing = Object.keys(data.plan.targets).filter((t) => !fp.mappings.some((m) => m.target === t));
    const fileLevel = issues.filter((i) => !i.column && i.severity !== "info");

    return h("section", { class: ["card file-card", isCollapsed ? "collapsed" : ""], "aria-label": file },
      head,
      h("div", { class: "card-content" },
        fileLevel.length ? h("div", { class: "card-body stack", style: { gap: "6px" } }, fileLevel.map((i) =>
          h("span", { class: `sev ${i.severity} small` }, sev(i.severity, "").firstChild, h("span", { text: i.message })))) : null,
        h("div", { style: { overflowX: "auto" } }, h("table", { class: "map-table" },
          h("thead", {}, h("tr", {}, ["Column in file", "Maps to", "Unit / format", "Values: raw → clean", "How"].map((c) => h("th", { text: c })))),
          h("tbody", {}, rows))),
        missing.length ? h("p", { class: "card-body small faint" }, "No column in this file for: ",
          missing.map((t, i) => [i ? ", " : "", h("code", { text: t })]), ". They'll be empty for its rows.") : null));
  }

  function targetSelect(file, col, m, fp) {
    const usedBy = new Map(fp.mappings.filter((x) => x.source !== col).map((x) => [x.target, x.source]));
    const current = m ? m.target : "";
    const sel = h("select", { class: "compact", "data-key": `${file}::${col}::target`, "aria-label": `Target for ${col} in ${file}`,
      onChange: (e) => change(() => {
        const o = ov(file, col);
        o.target = e.target.value || null;
        delete o.unit;
        delete o.date_format;
      }) },
    h("option", { value: "", selected: !current, text: "— not used (dropped) —" }),
    Object.keys(data.plan.targets).map((t) => {
      const info = data.target_info[t] || {};
      const unit = info.unit_label ? ` (${info.unit_label})` : "";
      const taken = usedBy.has(t) ? ` · now from ${usedBy.get(t)}` : "";
      return h("option", { value: t, selected: t === current, text: `${t}${unit}${taken}` });
    }));
    return sel;
  }

  function unitControl(file, col, m) {
    if (!m) return h("span", { class: "faint", text: "—" });
    const info = data.target_info[m.target] || {};
    const o = (overrides[file] || {})[col] || {};
    if (info.kind === "quantity") {
      const choices = unitsByDim.get(info.dimension) || [];
      const auto = m.unit_source === "user" ? "" : m.unit;
      const sel = h("select", { class: "compact", "data-key": `${file}::${col}::unit`, "aria-label": `Unit of ${col} in ${file}`,
        onChange: (e) => change(() => { const x = ov(file, col); if (e.target.value) x.unit = e.target.value; else delete x.unit; tidy(file, col); }) },
      h("option", { value: "", selected: !o.unit, text: `Automatic${auto ? `: ${pretty(auto)}` : ""}` }),
      choices.map((u) => h("option", { value: u.unit, selected: o.unit === u.unit, text: u.label })),
      o.unit && !choices.some((u) => u.unit === o.unit) ? h("option", { value: o.unit, selected: true, text: pretty(o.unit) }) : null);
      return h("div", {}, sel, h("div", { class: "tiny faint", text: `→ ${info.unit_label}` }));
    }
    if (info.kind === "datetime") {
      const formats = [...app.meta.date_formats, "mixed"];
      return h("select", { class: "compact", "data-key": `${file}::${col}::date`, "aria-label": `Date format of ${col} in ${file}`,
        onChange: (e) => change(() => { const x = ov(file, col); if (e.target.value) x.date_format = e.target.value; else delete x.date_format; tidy(file, col); }) },
      h("option", { value: "", selected: !o.date_format, text: `Automatic${m.date_format && !o.date_format ? `: ${humanDate(m.date_format)}` : ""}` }),
      formats.map((f) => h("option", { value: f, selected: o.date_format === f, text: humanDate(f) })));
    }
    return h("span", { class: "faint small", text: info.kind === "number" ? "number" : "text" });
  }

  function previewCell(file, col, m) {
    if (!m) return h("span", { class: "faint small", text: "dropped" });
    const pairs = (data.previews[file] || {})[col] || [];
    const unit = (data.target_info[m.target] || {}).unit_label || "";
    return h("div", { class: "preview-pairs" }, pairs.map((p) => h("div", {},
      h("span", { text: p.raw }), h("span", { class: "arrow", text: "→" }),
      h("span", { class: "clean", text: p.clean }), unit && p.clean !== "(unreadable)" ? h("span", { class: "faint", text: ` ${unit}` }) : null)));
  }

  function howCell(file, col, m) {
    const o = (overrides[file] || {})[col];
    const reset = o ? iconButton("undo", `Reset ${col} to automatic`, () => change(() => { delete overrides[file][col]; tidy(file, col); }), { size: "sm" }) : null;
    if (!m) return h("div", { class: "row" }, o && "target" in o ? badge("dropped by you", "accent") : badge("no match", "outline"), reset);
    const [label, kind] = METHOD[m.method] || [m.method, ""];
    const conf = m.method === "fuzzy" ? ` ${Math.round(m.confidence * 100)}%` : "";
    const us = m.unit_source && UNIT_SOURCE[m.unit_source];
    return h("div", { class: "stack", style: { gap: "4px" } },
      h("div", { class: "row" }, badge(label + conf, `${kind} how-chip`), reset),
      us ? h("span", { title: m.unit_note }, badge(us[0], `${us[1]} how-chip`)) : null,
      m.date_format ? badge(m.date_note === "set by you" ? "format set by you" : "format detected",
        m.date_note === "set by you" ? "accent how-chip" : "good how-chip") : null);
  }

  function actions() {
    const c = data.counts;
    const approve = button(data.needs_approval ? "Approve & run" : "Approved", {
      kind: "primary", iconName: data.needs_approval ? "play" : "check", size: "lg",
      disabled: !data.needs_approval || !Object.keys(data.plan.targets).length, onClick: doApprove });
    return h("div", { class: "sticky-actions" }, h("div", { class: "row between" },
      h("div", { class: "row" }, status,
        h("span", { class: "small muted", text: `· ${data.merged_rows.toLocaleString()} rows ${data.needs_approval ? "will be" : "are"} merged` }),
        c.error ? badge(plural(c.error, "error"), "bad", "error") : null,
        c.warning ? badge(plural(c.warning, "warning"), "warn", "alert") : null),
      approve));
  }

  async function doApprove() {
    if (data.counts.error) {
      const ok = await confirmDialog({ title: `Approve with ${plural(data.counts.error, "error")}?`, confirm: "Approve anyway",
        message: "clean.py stops when validation errors are present (values out of range, missing IDs), so the clean step will " +
          "fail until they're fixed. You can fix them here, in the schema's ranges, or in the raw files." });
      if (!ok) return;
    }
    try {
      await api(P(pid, "/approve"), { method: "POST" });
      toast("Approved. Running the pipeline…");
      await ctx.refresh();
      ctx.goto("pipeline");
    } catch (e) { toastError(e); }
  }

  async function showScript() {
    try {
      const text = await api(P(pid, "/clean.py?draft=true"));
      openDialog({ title: "clean.py (from the mapping above)", wide: true,
        body: h("div", { class: "stack" },
          h("p", { class: "muted small", text: "This is the exact script approval will save. It needs only pandas (and openpyxl for Excel), " +
            "so anyone can re-run the cleaning: python clean.py raw merged.csv" }),
          h("pre", { class: "code-view", text })),
        actions: [{ label: "Copy", icon: "copy", onClick: () => { copyText(text); return true; } }, { label: "Close", kind: "primary" }] });
    } catch (e) { toastError(e); }
  }

  const onKey = (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "z" || document.querySelector("dialog[open]")) return;
    if (e.target instanceof HTMLElement && ["INPUT", "TEXTAREA"].includes(e.target.tagName)) return;
    e.preventDefault();
    if (e.shiftKey) doRedo(); else doUndo();
  };
  document.addEventListener("keydown", onKey);

  try {
    await load();
  } catch (e) {
    page.append(h("div", { class: "callout bad" }, icon("error"), h("p", { text: e.message })));
  }

  return {
    dispose() { document.removeEventListener("keydown", onKey); },
    async onSummary(next, prev) {
      // A file arrived or changed while this tab was open: reload the plan (unless mid-edit).
      if (!saving && JSON.stringify(next.files.map((f) => [f.name, f.sha256])) !==
          JSON.stringify(prev.files.map((f) => [f.name, f.sha256]))) {
        await load();
        toast("Files changed; the mapping was refreshed");
      } else if (next.needs_approval !== prev.needs_approval && !saving) {
        await load();
      }
    },
  };
}
