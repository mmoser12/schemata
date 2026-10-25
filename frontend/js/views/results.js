// Results: the merged table (search, sort, page) with column stats and downloads.
import { api, enc, P } from "../api.js";
import { button, clear, debounce, fmtNum, h, icon, plural, relTime, toastError } from "../ui.js";

const PAGE = 100;

export async function mountResults(root, ctx) {
  const { pid, app } = ctx;
  const unitLabel = new Map(app.meta.units.flatMap((g) => g.units.map((u) => [u.unit, u.label])));
  const state = { q: "", sort: "", desc: false, offset: 0 };
  let res = null;
  const host = h("div", { class: "stack" });
  root.append(host);

  const search = h("input", { type: "search", placeholder: "Search all columns…  ( / )", "aria-label": "Search the merged table",
    onInput: debounce((e) => { state.q = e.target.value; state.offset = 0; load(); }, 200) });

  async function load() {
    const qs = `?offset=${state.offset}&limit=${PAGE}&q=${enc(state.q)}&sort=${enc(state.sort)}&desc=${state.desc}`;
    try {
      res = await api(P(pid, `/results${qs}`));
      render();
    } catch (e) { toastError(e); }
  }

  function downloads() {
    const a = (what, label, iconName, title) => h("a", { class: "btn", href: `/api${P(pid, `/download/${what}`)}`, download: "", title },
      icon(iconName), h("span", { text: label }));
    return h("div", { class: "btn-group" },
      a("merged.csv", "CSV", "download", "The merged table as produced by clean.py"),
      a("merged.xlsx", "Excel", "table", "The same table as an .xlsx workbook"),
      a("clean.py", "clean.py", "code", "The approved cleaning script"),
      a("bundle.zip", "Bundle", "folder", "Everything needed to reproduce this result, with a hash manifest"));
  }

  function render() {
    clear(host);
    const s = ctx.summary;
    if (!res.available) {
      host.append(h("div", { class: "card" }, h("div", { class: "empty" }, icon("table", { class: "big" }),
        h("h3", { text: s.pipeline.running ? "Merging…" : "No merged table yet" }),
        h("p", { text: s.pipeline.running ? "The pipeline is running; results appear here as soon as the clean step finishes."
          : s.files.length ? "Review and approve the mapping; the clean step then writes the merged table."
            : "Add files to get started." }),
        h("div", { class: "btn-group" }, s.pipeline.running ? null
          : button(s.files.length ? "Go to review" : "Add files", { kind: "primary", iconName: "arrow",
            onClick: () => ctx.goto(s.files.length ? "review" : "files") })))));
      return;
    }

    const stale = s.needs_approval || s.pipeline.stale.includes("clean");
    host.append(h("div", { class: "section-head" },
      h("div", {}, h("h2", { text: "Merged data" }),
        h("p", { class: "muted", text: `Produced by clean.py · updated ${relTime(res.modified)}` })),
      downloads()));

    if (stale) {
      host.append(h("div", { class: "callout warn" }, icon("clock"), h("div", { class: "grow" },
        h("strong", { text: "This table may be out of date" }),
        h("p", { class: "small", text: s.needs_approval ? "Files or the mapping changed. Approve the new mapping to refresh it."
          : "The raw files changed since the clean step last ran. Run the pipeline to refresh it." })),
      button(s.needs_approval ? "Review" : "Open pipeline", { onClick: () => ctx.goto(s.needs_approval ? "review" : "pipeline") })));
    }

    const numericCols = res.columns.filter((c) => res.stats[c] && res.stats[c].type === "numeric");
    host.append(h("div", { class: "stat-tiles" },
      tile("Rows", res.total.toLocaleString()),
      tile("Files merged", res.stats.source_file ? String(res.stats.source_file.unique) : "—"),
      tile("Columns", String(res.columns.length)),
      tile("Empty cells", String(res.columns.reduce((n, c) => n + (res.stats[c] ? res.stats[c].empty : 0), 0)))));

    const head = h("tr", {}, res.columns.map((c) => {
      const st = res.stats[c] || {};
      const unit = res.units[c] ? unitLabel.get(res.units[c]) || res.units[c] : "";
      const sorted = state.sort === c;
      const dir = sorted ? (state.desc ? "descending" : "ascending") : "none";
      return h("th", { class: numericCols.includes(c) ? "num" : "", "aria-sort": dir },
        h("button", { class: "sort", type: "button", onClick: () => {
          if (state.sort === c) state.desc = !state.desc; else { state.sort = c; state.desc = false; }
          state.offset = 0; load();
        } }, c, unit ? h("span", { class: "unit", text: ` (${unit})` }) : null,
        sorted ? icon("chevron", { width: 12, height: 12, style: { transform: state.desc ? "" : "rotate(180deg)" } }) : null),
        st.type === "numeric" ? h("span", { class: "stat", text: `${fmtNum(st.min)} … ${fmtNum(st.max)} · med ${fmtNum(st.median)}` })
          : st.unique !== undefined ? h("span", { class: "stat", text: `${st.unique} distinct` }) : null);
    }));
    const body = h("tbody", {}, res.rows.map((r) => h("tr", {}, r.map((v, i) =>
      h("td", { class: [numericCols.includes(res.columns[i]) ? "num" : "", v === "" ? "empty-cell" : ""], text: v === "" ? "—" : v })))));

    const from = res.filtered ? res.offset + 1 : 0;
    const to = Math.min(res.offset + PAGE, res.filtered);
    host.append(h("div", { class: "row between" },
      h("div", { class: "grow", style: { maxWidth: "420px" } }, search),
      h("span", { class: "small muted", role: "status",
        text: state.q ? `${plural(res.filtered, "match", "matches")} of ${res.total.toLocaleString()} rows` : `${res.total.toLocaleString()} rows` })));
    host.append(h("div", { class: "table-wrap tall" }, h("table", { class: "data-table" }, h("thead", {}, head), body)));
    host.append(h("div", { class: "row between" },
      h("span", { class: "small muted", text: res.filtered ? `Showing ${from}–${to}` : "No rows match" }),
      h("div", { class: "btn-group" },
        button("Previous", { size: "sm", disabled: res.offset === 0, onClick: () => { state.offset = Math.max(0, state.offset - PAGE); load(); } }),
        button("Next", { size: "sm", disabled: to >= res.filtered, onClick: () => { state.offset += PAGE; load(); } }))));
    if (document.activeElement === document.body && state.q) search.focus();
  }

  function tile(label, value) {
    return h("div", { class: "card stat-tile" }, h("div", { class: "label", text: label }), h("div", { class: "value", text: value }));
  }

  const onKey = (e) => {
    if (e.key === "/" && !(e.target instanceof HTMLElement && ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName))) {
      e.preventDefault();
      search.focus();
    }
  };
  document.addEventListener("keydown", onKey);
  await load();

  return {
    dispose() { document.removeEventListener("keydown", onKey); },
    onSummary(next, prev) {
      const lastRun = (x) => (x.last_run ? x.last_run.id : null);
      if (lastRun(next) !== lastRun(prev) || next.has_results !== prev.has_results ||
          next.needs_approval !== prev.needs_approval || next.pipeline.running !== prev.pipeline.running) load();
    },
  };
}

