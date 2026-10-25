// Files: upload (drag & drop or picker), the watch folder, previews, removal.
import { api, enc, P, upload } from "../api.js";
import { badge, button, clear, confirmDialog, copyText, fmtBytes, h, icon, iconButton, openDialog, plural,
  relTime, simpleTable, toast, toastError } from "../ui.js";

const ACCEPT = ".csv,.tsv,.txt,.xlsx,.xlsm";

export async function mountFiles(root, ctx) {
  const { pid } = ctx;
  const listHost = h("div");
  const progress = h("div", { class: "progress", hidden: true }, h("div"));
  const picker = h("input", { type: "file", multiple: true, accept: ACCEPT, hidden: true,
    onChange: () => { if (picker.files.length) send([...picker.files]); picker.value = ""; } });

  const drop = h("div", { class: "dropzone", tabindex: 0, role: "button", "aria-label": "Add files: drop them here or press Enter to choose",
    onClick: () => picker.click(),
    onKeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); picker.click(); } },
    onDragover: (e) => { e.preventDefault(); drop.classList.add("over"); },
    onDragleave: () => drop.classList.remove("over"),
    onDrop: (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files.length) send([...e.dataTransfer.files]); },
  },
  icon("upload"), h("strong", { text: "Drop files here, or click to choose" }),
  h("p", { class: "muted small", text: "CSV, TSV, TXT or Excel (.xlsx). Semicolon files, decimal commas, odd encodings and " +
    "title rows above the header are handled automatically." }));

  async function send(files) {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    progress.hidden = false;
    const bar = progress.firstChild;
    try {
      const r = await upload(P(pid, "/files"), form, (frac) => { bar.style.width = `${Math.round(frac * 100)}%`; });
      if (r.saved.length) toast(`Added ${plural(r.saved.length, "file")}: ${r.saved.join(", ")}`);
      if (r.unchanged.length) toast(`${r.unchanged.join(", ")} ${r.unchanged.length === 1 ? "was" : "were"} already here, unchanged`);
      for (const rej of r.rejected) toast(`${rej.file}: ${rej.reason}`, "bad");
      await ctx.refresh();
      render();
    } catch (e) {
      toastError(e);
    } finally {
      progress.hidden = true;
      bar.style.width = "0";
    }
  }

  function render() {
    const s = ctx.summary;
    clear(listHost);
    if (!s.files.length) {
      listHost.append(h("div", { class: "card" }, h("div", { class: "empty" },
        icon("file", { class: "big" }), h("h3", { text: "No files yet" }),
        h("p", { text: "Add the spreadsheets you want to merge. They're copied into the project; originals elsewhere are never touched." }))));
      return;
    }
    const rows = s.files.map((f) => {
      const status = !f.readable ? badge("Unreadable", "bad", "error")
        : f.approved ? badge("Approved", "good", "check")
          : f.changed_since_approval ? badge("Changed", "warn", "alert", "The file changed since you approved it")
            : badge("New", "accent", "sparkle", "Not reviewed yet");
      return h("tr", { class: f.readable ? "" : "row-error" },
        h("td", {}, h("div", { class: "src-name", text: f.name }),
          h("div", { class: "faint tiny mono", title: `sha256 ${f.sha256}`, text: `sha256 ${f.sha256.slice(0, 12)}…` })),
        h("td", {}, status),
        h("td", { class: "num", text: f.rows === null ? "—" : `${f.rows.toLocaleString()} × ${f.columns}` }),
        h("td", { class: "small muted", text: f.read_as || "—" }),
        h("td", { class: "num small", text: fmtBytes(f.size) }),
        h("td", { class: "small muted nowrap", text: relTime(f.added_at), title: new Date(f.added_at * 1000).toLocaleString() }),
        h("td", { class: "nowrap" },
          iconButton("eye", `Preview ${f.name}`, () => preview(f.name), { size: "sm", disabled: !f.readable }),
          h("a", { class: "icon-btn sm", href: `/api${P(pid, `/files/${enc(f.name)}`)}`, download: "",
            "aria-label": `Download original ${f.name}`, title: "Download original" }, icon("download")),
          iconButton("trash", `Remove ${f.name}`, () => remove(f.name), { size: "sm" })));
    });
    listHost.append(h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["File", "Status", "Rows × cols", "Read as", "Size", "Added", ""].map((c, i) =>
        h("th", { class: i === 2 || i === 4 ? "num" : "", text: c })))),
      h("tbody", {}, rows))));
  }

  async function preview(name) {
    try {
      const p = await api(P(pid, `/files/${enc(name)}/preview`));
      const cols = p.columns;
      const profile = h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Column", "Type", "Empty", "Unique", "Range / examples"].map((c) => h("th", { text: c })))),
        h("tbody", {}, cols.map((c) => {
          const pr = p.profile.columns[c];
          const range = pr.inferred_type === "numeric" && pr.min !== undefined
            ? `${pr.min} … ${pr.max} (median ${pr.median})` : pr.examples.join(", ");
          return h("tr", {}, h("td", { class: "src-name", text: c }), h("td", { text: pr.inferred_type }),
            h("td", { class: "num", text: `${Math.round(pr.null_rate * 100)}%` }), h("td", { class: "num", text: pr.n_unique }),
            h("td", { class: "small muted", text: range }));
        }))));
      openDialog({
        title: name, wide: true,
        body: h("div", { class: "stack" },
          h("div", { class: "callout info" }, icon("info"), h("div", { class: "grow" },
            h("p", {}, h("strong", { text: "Read as: " }), p.read_as),
            h("p", { class: "small muted", text: `${p.profile.n_rows.toLocaleString()} rows. The generated clean.py reads it exactly this way.` }))),
          h("h3", { text: "Columns" }), profile,
          h("h3", { text: `First ${p.rows.length} rows` }), simpleTable(cols, p.rows)),
      });
    } catch (e) { toastError(e); }
  }

  async function remove(name) {
    const ok = await confirmDialog({ title: `Remove ${name}?`, confirm: "Remove file", kind: "primary danger",
      message: "The file is deleted from this project (its history stays in the activity log). The merge will need approval again." });
    if (!ok) return;
    try {
      await api(P(pid, `/files/${enc(name)}`), { method: "DELETE" });
      toast(`Removed ${name}`);
      await ctx.refresh();
      render();
    } catch (e) { toastError(e); }
  }

  const s = ctx.summary;
  const next = h("div", { class: "next-step" });
  const renderNext = () => {
    const sum = ctx.summary;
    clear(next);
    if (!sum.files.length) return;
    const [label, to] = !sum.n_targets ? ["Next: set up the schema", "schema"]
      : sum.needs_approval ? ["Next: review the mapping", "review"] : ["Next: see the results", "results"];
    next.append(button(label, { kind: "primary", iconName: "arrow", onClick: () => ctx.goto(to) }));
  };

  root.append(h("div", { class: "stack" },
    h("div", { class: "section-head" }, h("div", {}, h("h2", { text: "Raw files" }),
      h("p", { class: "muted", text: "Originals are kept exactly as uploaded. Re-uploading a file with the same name replaces it, and the change is logged." }))),
    drop, picker, progress,
    h("div", { class: "card card-pad stack", style: { gap: "8px" } },
      h("div", { class: "row" }, icon("folder", { width: 18, height: 18 }), h("strong", { text: "Watch folder" }),
        s.auto_run ? badge("Auto-run on", "accent", "zap") : badge("Auto-run off", "outline")),
      h("p", { class: "muted small", text: "You can also save or copy files straight into this folder (from an instrument PC, a sync folder, a script). " +
        "Schemata notices them within seconds. With auto-run on, files that map cleanly are merged and analyzed automatically." }),
      h("div", { class: "path-box" }, h("span", { text: s.raw_dir, title: s.raw_dir }),
        iconButton("copy", "Copy folder path", () => copyText(s.raw_dir), { size: "sm" }))),
    listHost, next));
  render();
  renderNext();

  return {
    onSummary(nextSum, prev) {
      if (JSON.stringify(nextSum.files) !== JSON.stringify(prev.files)) render();
      renderNext();
    },
  };
}
