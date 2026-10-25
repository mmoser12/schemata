// Schema: the clean columns every file is mapped onto. Edit, or let Schemata suggest from the files.
import { api, enc, P } from "../api.js";
import { badge, button, clear, debounce, h, icon, iconButton, openDialog, toast, toastError } from "../ui.js";

const KIND_HELP = {
  id: "Identifier (sample ID, well, specimen). Checked for blanks and duplicates.",
  datetime: "Date / time. Each file's format is detected and normalized.",
  quantity: "A measured value with a unit. Converted to the unit you choose.",
  number: "A unitless number (pH, absorbance, counts). Range-checked, not converted.",
  text: "Free text (operator, notes).",
};

let unitLabel = new Map();

function toRows(targets) {
  return Object.entries(targets).map(([name, t]) => ({
    name, kind: t.kind, unit: t.unit ? unitLabel.get(t.unit) || t.unit : "", synonyms: (t.synonyms || []).join(", "),
    lo: t.valid_range && t.valid_range[0] !== null ? String(t.valid_range[0]) : "",
    hi: t.valid_range && t.valid_range[1] !== null ? String(t.valid_range[1]) : "",
    description: t.description || "",
  }));
}

function toTargets(rows) {
  const out = {};
  for (const r of rows) {
    const t = { kind: r.kind, synonyms: r.synonyms.split(",").map((x) => x.trim()).filter(Boolean) };
    if (r.kind === "quantity") t.unit = r.unit;
    if ((r.kind === "quantity" || r.kind === "number") && (r.lo !== "" || r.hi !== "")) {
      t.valid_range = [r.lo === "" ? null : Number(r.lo), r.hi === "" ? null : Number(r.hi)];
    }
    if (r.description) t.description = r.description;
    out[r.name.trim()] = t;
  }
  return out;
}

export async function mountSchema(root, ctx) {
  const { pid, app } = ctx;
  unitLabel = new Map(app.meta.units.flatMap((g) => g.units.map((u) => [u.unit, u.label])));
  let saved = (await api(P(pid, "/schema"))).targets;
  let rows = toRows(saved);
  let dirty = false;
  const unitDatalist = h("datalist", { id: "unit-options" }, app.meta.units.flatMap((g) =>
    g.units.map((u) => h("option", { value: u.label, label: `${g.label}: ${u.label}` }))));

  const body = h("tbody");
  const status = h("span", { class: "small muted", role: "status" });
  const saveBtn = button("Save schema", { kind: "primary", iconName: "check", onClick: () => save() });
  const errorBox = h("div");

  function markDirty() {
    dirty = JSON.stringify(toTargets(rows)) !== JSON.stringify(toTargets(toRows(saved)));
    status.textContent = dirty ? "Unsaved changes" : "All changes saved";
    saveBtn.disabled = !dirty;
  }

  const checkUnit = debounce(async (input, note, value) => {
    if (!value.trim()) { input.classList.add("invalid"); note.textContent = "A quantity needs a unit"; return; }
    try {
      const r = await api(`/units/check?u=${enc(value)}`);
      input.classList.toggle("invalid", !r.ok);
      note.textContent = r.ok ? `${r.label} · ${r.dimension}` : r.error;
    } catch { /* offline: the server validates on save anyway */ }
  }, 250);

  function renderRows() {
    clear(body);
    if (!rows.length) {
      body.append(h("tr", {}, h("td", { colspan: 6 }, h("div", { class: "empty" },
        icon("columns", { class: "big" }), h("h3", { text: "No columns yet" }),
        h("p", { text: ctx.summary.files.length ? "Let Schemata suggest columns from your files, or add them by hand."
          : "Add files first and Schemata can suggest the columns, or add them by hand." })))));
      return;
    }
    rows.forEach((r, i) => {
      const set = (k) => (e) => { r[k] = e.target.value; markDirty(); };
      const unitNote = h("div", { class: "hint tiny faint" });
      const unitInput = h("input", { type: "text", class: "compact", value: r.unit, list: "unit-options",
        "aria-label": `Unit for ${r.name}`, placeholder: "e.g. °C, mg, mM",
        onInput: (e) => { r.unit = e.target.value; markDirty(); checkUnit(unitInput, unitNote, r.unit); } });
      if (r.kind === "quantity" && r.unit) checkUnit(unitInput, unitNote, r.unit);
      const kindSel = h("select", { class: "compact", "aria-label": `Kind of ${r.name}`,
        onChange: (e) => { r.kind = e.target.value; markDirty(); renderRows(); } },
      app.meta.kinds.map((k) => h("option", { value: k, selected: k === r.kind, text: k })));
      const numeric = r.kind === "quantity" || r.kind === "number";
      body.append(h("tr", {},
        h("td", {}, h("input", { type: "text", class: "compact mono", value: r.name, "aria-label": "Column name",
          pattern: "[a-z][a-z0-9_]*", onInput: set("name") })),
        h("td", {}, kindSel, h("div", { class: "hint tiny faint", text: KIND_HELP[r.kind] })),
        h("td", {}, r.kind === "quantity" ? [unitInput, unitNote] : h("span", { class: "faint", text: "—" })),
        h("td", {}, numeric ? h("div", { class: "row", style: { flexWrap: "nowrap" } },
          h("input", { type: "number", class: "compact", value: r.lo, step: "any", placeholder: "min", "aria-label": `Minimum for ${r.name}`,
            style: { width: "90px", minWidth: "0" }, onInput: set("lo") }),
          h("span", { class: "faint", text: "…" }),
          h("input", { type: "number", class: "compact", value: r.hi, step: "any", placeholder: "max", "aria-label": `Maximum for ${r.name}`,
            style: { width: "90px", minWidth: "0" }, onInput: set("hi") })) : h("span", { class: "faint", text: "—" })),
        h("td", {}, h("input", { type: "text", class: "compact", value: r.synonyms, placeholder: "temp, t, temperature",
          "aria-label": `Other header names for ${r.name}`, onInput: set("synonyms") })),
        h("td", { class: "nowrap" },
          iconButton("chevron", "Move down", () => { if (i < rows.length - 1) { [rows[i], rows[i + 1]] = [rows[i + 1], rows[i]]; markDirty(); renderRows(); } }, { size: "sm", disabled: i === rows.length - 1 }),
          iconButton("trash", `Remove ${r.name}`, () => { rows.splice(i, 1); markDirty(); renderRows(); }, { size: "sm" }))));
    });
  }

  async function save(quiet = false) {
    clear(errorBox);
    try {
      const r = await api(P(pid, "/schema"), { method: "PUT", body: { targets: toTargets(rows) } });
      saved = r.targets;
      rows = toRows(saved);
      renderRows();
      markDirty();
      if (!quiet) toast("Schema saved. The mapping was rebuilt; check the Review tab.");
      ctx.refresh();
      return true;
    } catch (e) {
      errorBox.append(h("div", { class: "callout bad" }, icon("error"), h("div", { class: "grow" },
        h("strong", { text: "The schema wasn't saved" }), h("p", { text: e.message }))));
      if (quiet) toastError(e);
      return false;
    }
  }

  async function suggest() {
    let res;
    try {
      res = await api(P(pid, "/schema/suggest"), { method: "POST" });
    } catch (e) { toastError(e); return; }
    const existing = new Set(rows.map((r) => r.name));
    const picks = new Map(res.notes.map((n) => [n.target, !existing.has(n.target)]));
    const table = h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["", "Column", "Kind", "Unit", "Seen in your files as", "Coverage"].map((c) => h("th", { text: c })))),
      h("tbody", {}, res.notes.map((n) => h("tr", {},
        h("td", {}, h("input", { type: "checkbox", checked: picks.get(n.target), "aria-label": `Include ${n.target}`,
          onChange: (e) => picks.set(n.target, e.target.checked) })),
        h("td", {}, h("span", { class: "mono", text: n.target }), existing.has(n.target) ? h("div", {}, badge("already in schema", "outline")) : null),
        h("td", { text: n.kind }),
        h("td", { text: n.unit || "—" }),
        h("td", { class: "small" }, n.seen_as.map((x, i) => [i ? ", " : "", h("code", { text: x })])),
        h("td", {}, n.in_all_files ? badge("all files", "good") : badge(`${n.files.length} file${n.files.length === 1 ? "" : "s"}`, "outline")))))));
    const apply = (replace) => {
      const chosen = res.notes.filter((n) => picks.get(n.target)).map((n) => n.target);
      const sugg = toRows(Object.fromEntries(chosen.map((k) => [k, res.targets[k]])));
      if (replace) rows = sugg;
      else rows = [...rows, ...sugg.filter((r) => !existing.has(r.name))];
      markDirty();
      renderRows();
      toast("Suggestions added. Check them, then save.");
    };
    openDialog({
      title: "Suggested schema", wide: true,
      body: h("div", { class: "stack" },
        h("p", { class: "muted", text: "Columns that mean the same thing across files are grouped together. Units are " +
          "standardized on a common choice; you can change any of it before saving." }), table),
      actions: [
        { label: "Cancel" },
        { label: "Replace schema with selected", onClick: () => apply(true) },
        { label: "Add selected", kind: "primary", onClick: () => apply(false) },
      ],
    });
  }

  const onKey = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); if (dirty) save(); }
  };
  document.addEventListener("keydown", onKey);
  const onUnload = (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", onUnload);

  root.append(h("div", { class: "stack" },
    h("div", { class: "section-head" },
      h("div", {}, h("h2", { text: "Target schema" }),
        h("p", { class: "muted", text: "The clean columns every file is mapped onto. Units are what values get converted to; " +
          "ranges catch impossible values before they reach your analysis." })),
      h("div", { class: "btn-group" },
        button("Suggest from my files", { iconName: "sparkle", onClick: suggest, disabled: !ctx.summary.files.length }),
        button("Add column", { iconName: "plus", onClick: () => {
          let n = rows.length + 1;
          while (rows.some((r) => r.name === `column_${n}`)) n++;
          rows.push({ name: `column_${n}`, kind: "number", unit: "", synonyms: "", lo: "", hi: "", description: "" });
          markDirty(); renderRows();
          body.lastElementChild.querySelector("input").select();
        } }))),
    errorBox,
    h("div", { class: "table-wrap" }, h("table", { class: "map-table" },
      h("thead", {}, h("tr", {}, ["Column", "Kind", "Unit", "Valid range", "Other header names", ""].map((c) => h("th", { text: c })))),
      body)),
    unitDatalist,
    h("div", { class: "sticky-actions" }, h("div", { class: "row between" }, status,
      h("div", { class: "btn-group" },
        button("Discard changes", { onClick: () => { rows = toRows(saved); markDirty(); renderRows(); } }),
        saveBtn,
        button("Next: review", { iconName: "arrow", onClick: () => ctx.goto("review") }))))));
  renderRows();
  markDirty();

  return {
    async dispose() {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("beforeunload", onUnload);
      if (dirty && await save(true)) toast("Saved your schema changes");
    },
  };
}
