// Pipeline: the DAG, what's stale and why, running, steps, figures, run history.
import { api, P, upload } from "../api.js";
import { absTime, badge, button, clear, confirmDialog, fmtNum, fmtSeconds, h, icon, openDialog, parseCsv, plural, relTime,
  s, simpleTable, toast, toastError } from "../ui.js";

const STATE = {
  fresh: ["Up to date", "good", "check"], stale: ["Stale", "warn", "clock"], never_run: ["Not run yet", "warn", "clock"],
  failed: ["Failed", "bad", "error"], running: ["Running", "accent", "refresh"],
};
const KIND_LABEL = { summary: "Summary statistics", chart: "Chart", script: "Your script" };

export async function mountPipeline(root, ctx) {
  const { pid } = ctx;
  let pipe = null;
  let runs = [];
  let selected = null;
  let figureVersion = Date.now();
  const host = h("div", { class: "stack" });
  root.append(host);

  async function load() {
    try {
      [pipe, runs] = await Promise.all([api(P(pid, "/pipeline")), api(P(pid, "/runs"))]);
      render();
    } catch (e) { toastError(e); }
  }

  async function run(opts = {}) {
    try {
      pipe = await api(P(pid, "/pipeline/run"), { method: "POST", body: opts });
      toast(opts.force ? "Re-running every step…" : "Running stale steps…");
      render();
      ctx.refresh();
    } catch (e) {
      if (e.status === 409 && /approve/.test(e.message)) {
        toast(e.message, "bad");
        ctx.goto("review");
      } else toastError(e);
    }
  }

  // ---------------------------------------------------------------- render
  function render() {
    clear(host);
    const sum = ctx.summary;
    const steps = pipe.graph.steps;
    const staleN = pipe.stale.length;

    host.append(h("div", { class: "section-head" },
      h("div", {}, h("h2", { text: "Pipeline" }),
        h("p", { class: "muted", text: "Each step declares the files it reads and writes. When an input changes, only the steps " +
          "downstream of it re-run; everything else is reused." })),
      h("div", { class: "btn-group" },
        button(staleN ? `Run ${plural(staleN, "stale step")}` : "Nothing to run", {
          kind: "primary", iconName: "play", disabled: pipe.running || !staleN || sum.needs_approval, onClick: () => run() }),
        button("Re-run everything", { iconName: "refresh", disabled: pipe.running || sum.needs_approval,
          onClick: () => run({ force: true }), title: "Ignore the cache and run every step (useful for timing comparisons)" }),
        button("Add step", { iconName: "plus", onClick: () => editStep(null) }))));

    if (sum.needs_approval) {
      host.append(h("div", { class: "callout warn" }, icon("alert"), h("div", { class: "grow" },
        h("strong", { text: "Approve the mapping before running" }),
        h("p", { class: "small", text: "Files or mapping changed, so clean.py would be out of date." })),
      button("Review", { kind: "primary", onClick: () => ctx.goto("review") })));
    }
    if (pipe.running) {
      host.append(h("div", { class: "callout info" }, h("span", { class: "spinner" }), h("div", { class: "grow" },
        h("strong", { text: `Running${pipe.current_step ? `: ${pipe.current_step}` : "…"}` }),
        h("p", { class: "small", text: `Started ${relTime(pipe.started_at)} (${pipe.trigger === "auto" ? "auto-run" : "by you"}).` }))));
    } else if (pipe.error) {
      host.append(h("div", { class: "callout bad" }, icon("error"), h("div", { class: "grow" },
        h("strong", { text: "The last run crashed" }), h("p", { class: "small mono", text: pipe.error }))));
    }

    host.append(statTiles());
    host.append(h("div", { class: "card" }, h("div", { class: "card-body" }, dag(steps)),
      h("p", { class: "card-body small faint", style: { paddingTop: "0" } },
        "Select a step to see why it's stale, its files and its log. Stale steps re-run in dependency order; ",
        "if a step's output comes out byte-identical, the steps after it are skipped too.")));
    const sel = steps.find((x) => x.name === selected);
    if (sel) host.append(detail(sel));
    host.append(figures(steps));
    host.append(summaries(steps));
    host.append(history());
  }

  function statTiles() {
    const last = runs[0];
    const saved = runs.reduce((t, r) => t + r.time_saved_s, 0);
    const fresh = pipe.graph.steps.filter((x) => x.state === "fresh").length;
    const tile = (label, value, note) => h("div", { class: "card stat-tile" }, h("div", { class: "label", text: label }),
      h("div", { class: "value", text: value }), note ? h("div", { class: "tiny faint", text: note }) : null);
    return h("div", { class: "stat-tiles" },
      tile("Steps up to date", `${fresh} / ${pipe.graph.steps.length}`),
      tile("Last run", last ? fmtSeconds(last.duration_s) : "—", last ? `${relTime(last.ts)} · ran ${last.ran.length}, skipped ${last.skipped.length}` : "never"),
      tile("Time saved by caching", fmtSeconds(saved), `across ${plural(runs.length, "run")}`));
  }

  // ---------------------------------------------------------------- DAG drawing
  function dag(steps) {
    const parents = new Map(steps.map((x) => [x.name, x.depends_on]));
    const depth = new Map();
    const d = (n) => {
      if (depth.has(n)) return depth.get(n);
      const ps = parents.get(n) || [];
      const v = ps.length ? 1 + Math.max(...ps.map(d)) : 0;
      depth.set(n, v);
      return v;
    };
    steps.forEach((x) => d(x.name));
    const cols = [];
    steps.forEach((x) => { const c = depth.get(x.name); (cols[c] ||= []).push(x); });
    const NW = 210, NH = 70, GX = 70, GY = 18, PAD = 6;
    const height = Math.max(...cols.map((c) => c.length)) * (NH + GY) - GY + PAD * 2;
    const width = cols.length * (NW + GX) - GX + PAD * 2;
    const pos = new Map();
    cols.forEach((col, ci) => {
      const colH = col.length * (NH + GY) - GY;
      col.forEach((x, ri) => pos.set(x.name, { x: PAD + ci * (NW + GX), y: PAD + (height - PAD * 2 - colH) / 2 + ri * (NH + GY) }));
    });
    const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "group", "aria-label": "Pipeline steps" });
    const defs = s("defs", {}, s("marker", { id: "arrowhead", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" },
      s("path", { d: "M0,0L10,5L0,10z", fill: "var(--border-strong)" })));
    svg.append(defs);
    for (const e of pipe.graph.edges) {
      const a = pos.get(e.from), b = pos.get(e.to);
      const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x - 2, y2 = b.y + NH / 2, mx = (x1 + x2) / 2;
      const target = steps.find((x) => x.name === e.to);
      svg.append(s("path", { class: `edge ${target.state === "running" ? "hot" : ""}`, d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`,
        "marker-end": "url(#arrowhead)" }));
    }
    for (const x of steps) {
      const p = pos.get(x.name);
      const [label] = STATE[x.state] || [x.state];
      const kind = x.config ? KIND_LABEL[x.config.kind] : "Generated clean.py";
      const g = s("g", { class: `node ${selected === x.name ? "selected" : ""}`, transform: `translate(${p.x},${p.y})`, tabindex: 0,
        role: "button", "aria-label": `${x.title}: ${label}. ${x.status.reason}`, "aria-pressed": String(selected === x.name) },
      s("title", {}, `${x.title}: ${label}\n${x.status.reason}`),
      s("rect", { class: "box", width: NW, height: NH, rx: 10 }),
      s("rect", { class: `bar-${x.state}`, width: 5, height: NH - 16, x: 0, y: 8, rx: 2 }),
      s("text", { x: 16, y: 24 }, trunc(x.title, 24)),
      s("text", { class: "sub", x: 16, y: 41 }, trunc(kind, 30)),
      s("circle", { class: `state-${x.state}`, cx: 20, cy: 56, r: 4 }),
      s("text", { class: `state state-${x.state}`, x: 30, y: 60 }, x.state === "running" ? "Running…" : label));
      const pick = () => { selected = selected === x.name ? null : x.name; render(); };
      g.addEventListener("click", pick);
      g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
      svg.append(g);
    }
    return h("div", { class: "dag" }, svg);
  }

  function trunc(t, n) {
    return t.length > n ? `${t.slice(0, n - 1)}…` : t;
  }

  // ---------------------------------------------------------------- step detail
  function detail(x) {
    const [label, kind, ic] = STATE[x.state] || [x.state, "", "info"];
    const outLink = (o) => {
      const rel = o.replace(/^output\//, "");
      const exists = x.status.outputs_exist;
      return h("li", {}, o.startsWith("output/") && exists
        ? h("a", { href: `/api${P(pid, `/outputs/${rel}`)}?download=true`, download: "", text: o }) : h("code", { text: o }));
    };
    const last = x.last;
    const log = [last.stdout && h("details", {}, h("summary", { text: "Output (stdout)" }), h("pre", { class: "log", text: last.stdout })),
      last.stderr && h("details", { open: x.state === "failed" }, h("summary", { text: "Errors (stderr)" }), h("pre", { class: "log", text: last.stderr }))];
    return h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h3", { class: "grow", text: x.title }), badge(label, kind, ic),
        button("Run this step", { size: "sm", iconName: "play", disabled: pipe.running || ctx.summary.needs_approval,
          onClick: () => run({ only: [x.name], force: x.state === "fresh" }), title: "Runs this step (and anything stale before it)" }),
        x.config ? button("Edit", { size: "sm", iconName: "edit", onClick: () => editStep(x.config) }) : null,
        x.config ? button("Remove", { size: "sm", kind: "danger", iconName: "trash", onClick: () => removeStep(x.config.name) }) : null),
      h("div", { class: "card-body stack" },
        h("dl", { class: "kv" },
          h("dt", { text: "Why" }), h("dd", { text: x.status.reason }),
          h("dt", { text: "What it does" }), h("dd", { text: x.description }),
          h("dt", { text: "Command" }), h("dd", {}, h("code", { text: x.command.join(" ") })),
          h("dt", { text: "Reads" }), h("dd", {}, h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px" } }, x.inputs.map((i) => h("li", {}, h("code", { text: i }))))),
          h("dt", { text: "Writes" }), h("dd", {}, h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px" } }, x.outputs.map(outLink))),
          h("dt", { text: "Last run" }), h("dd", { text: last.ran_at ? `${absTime(last.ran_at)} · ${fmtSeconds(last.duration_s)} · exit code ${last.returncode}` : "never" })),
        log));
  }

  // ---------------------------------------------------------------- outputs
  function figures(steps) {
    const charts = steps.filter((x) => x.config && x.config.kind === "chart");
    if (!charts.length) return null;
    return h("section", { class: "stack" }, h("h3", { text: "Figures" }), h("div", { class: "figure-grid" }, charts.map((x) => {
      const svgPath = `/api${P(pid, `/outputs/figures/${x.name}.svg`)}`;
      const dataPath = `/api${P(pid, `/outputs/figures/${x.name}.csv`)}`;
      const dataHost = h("div");
      const ready = x.status.outputs_exist;
      const toggle = button("Show data", { size: "sm", iconName: "table", disabled: !ready, onClick: async () => {
        if (dataHost.firstChild) { clear(dataHost); toggle.lastChild.textContent = "Show data"; return; }
        try {
          const rows = parseCsv(await (await fetch(dataPath)).text(), 200).filter((r) => r.length > 1 || r[0]);
          dataHost.append(simpleTable(rows[0], rows.slice(1, 200)));
          toggle.lastChild.textContent = "Hide data";
        } catch (e) { toastError(e); }
      } });
      return h("figure", { class: "card figure", style: { margin: 0 } },
        ready ? h("img", { src: `${svgPath}?v=${figureVersion}`, alt: `${x.title} (chart). Use "Show data" for the values.`, loading: "lazy" })
          : h("div", { class: "empty" }, icon("chart", { class: "big" }), h("p", { text: "Run the pipeline to draw this figure." })),
        h("figcaption", { class: "card-body stack", style: { gap: "8px" } },
          h("div", { class: "row between" }, h("strong", { text: x.title }), badge(...(STATE[x.state] || [x.state, ""]).slice(0, 2))),
          h("div", { class: "btn-group" }, toggle,
            ready ? h("a", { class: "btn sm", href: `${svgPath}?download=true`, download: "" }, icon("download"), h("span", { text: "SVG" })) : null,
            ready ? h("a", { class: "btn sm", href: `${dataPath}?download=true`, download: "" }, icon("download"), h("span", { text: "Data" })) : null),
          dataHost));
    })));
  }

  function summaries(steps) {
    const items = steps.filter((x) => x.config && x.config.kind === "summary" && x.status.outputs_exist);
    if (!items.length) return null;
    const box = h("section", { class: "stack" }, h("h3", { text: "Summary tables" }));
    items.forEach((x) => {
      const holder = h("div", {}, h("div", { class: "skeleton", style: { height: "80px" } }));
      box.append(h("div", { class: "card" }, h("div", { class: "card-head" }, h("strong", { class: "grow", text: x.title }),
        h("a", { class: "btn sm", href: `/api${P(pid, `/outputs/${x.name}.csv`)}?download=true`, download: "" }, icon("download"), h("span", { text: "CSV" }))),
      h("div", { class: "card-body" }, holder)));
      fetch(`/api${P(pid, `/outputs/${x.name}.csv`)}`).then((r) => r.text()).then((text) => {
        const rows = parseCsv(text, 100).filter((r) => r.length > 1 || r[0]);
        const pretty = (v) => (v !== "" && !Number.isNaN(Number(v)) ? fmtNum(Number(v)) : v);
        clear(holder).append(simpleTable(rows[0] || [], rows.slice(1).map((r) => r.map((v, i) => (i ? pretty(v) : v))),
          { numeric: (i) => i > 0 }));
      }).catch(() => clear(holder).append(h("p", { class: "muted", text: "Couldn't load this table." })));
    });
    return box;
  }

  function history() {
    if (!runs.length) return h("section", { class: "stack" }, h("h3", { text: "Run history" }),
      h("p", { class: "muted small", text: "No runs yet." }));
    return h("section", { class: "stack" }, h("h3", { text: "Run history" }), h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["When", "Trigger", "Mode", "Ran", "Skipped", "Result", "Duration", "Time saved"].map((c, i) =>
        h("th", { class: i >= 6 ? "num" : "", text: c })))),
      h("tbody", {}, runs.slice(0, 30).map((r) => h("tr", {},
        h("td", { class: "nowrap", title: absTime(r.ts), text: relTime(r.ts) }),
        h("td", {}, r.trigger === "auto" ? badge("auto-run", "accent", "zap") : badge("you", "outline")),
        h("td", { text: r.mode === "full" ? "full re-run" : "incremental" }),
        h("td", { class: "small", text: r.ran.join(", ") || "—" }),
        h("td", { class: "small muted", text: r.skipped.length ? r.skipped.join(", ") : "—" }),
        h("td", {}, r.failed.length ? badge(`failed: ${r.failed.join(", ")}`, "bad", "error") : badge("ok", "good", "check")),
        h("td", { class: "num", text: fmtSeconds(r.duration_s) }),
        h("td", { class: "num", text: r.time_saved_s ? fmtSeconds(r.time_saved_s) : "—" })))))));
  }

  // ---------------------------------------------------------------- add / edit steps
  function configured() {
    return pipe.graph.steps.filter((x) => x.config).map((x) => x.config);
  }

  async function saveSteps(steps, msg) {
    try {
      pipe = await api(P(pid, "/pipeline/steps"), { method: "PUT", body: { steps } });
      toast(msg);
      render();
      ctx.refresh();
      return true;
    } catch (e) { toastError(e); return false; }
  }

  async function removeStep(name) {
    const ok = await confirmDialog({ title: `Remove step "${name}"?`, confirm: "Remove step", kind: "primary danger",
      message: "Its settings are removed from the pipeline. Files it already wrote stay in output/ until you delete them." });
    if (!ok) return;
    if (await saveSteps(configured().filter((x) => x.name !== name), `Removed ${name}`)) selected = null;
  }

  async function editStep(existing) {
    const schema = (await api(P(pid, "/schema"))).targets;
    const cols = Object.entries(schema);
    const numeric = cols.filter(([, t]) => t.kind === "quantity" || t.kind === "number").map(([n]) => n);
    const xish = cols.filter(([, t]) => ["quantity", "number", "datetime"].includes(t.kind)).map(([n]) => n);
    const cats = ["source_file", ...cols.filter(([, t]) => ["text", "id"].includes(t.kind)).map(([n]) => n)];
    const st = existing ? structuredClone(existing) : { name: "", kind: "chart", title: "", params: { chart: "bar", y: numeric[0], x: "source_file", agg: "median" } };
    const form = h("form", { class: "stack", onSubmit: (e) => e.preventDefault() });
    const select = (label, value, options, onChange, hint) => h("div", { class: "field" }, h("label", { text: label }),
      h("select", { onChange: (e) => onChange(e.target.value) }, options.map((o) => {
        const [v, l] = Array.isArray(o) ? o : [o, o];
        return h("option", { value: v, selected: v === value, text: l });
      })), hint ? h("span", { class: "hint", text: hint }) : null);
    const text = (label, value, onInput, attrs = {}) => h("div", { class: "field" }, h("label", { text: label }),
      h("input", { type: "text", value: value || "", onInput: (e) => onInput(e.target.value), ...attrs }),
      attrs.hint ? h("span", { class: "hint", text: attrs.hint }) : null);

    function draw() {
      clear(form);
      form.append(select("Kind", st.kind, [["chart", "Chart (bar, line or scatter)"], ["summary", "Summary statistics table"],
        ["script", "Your own Python script"]], (v) => { st.kind = v; st.params = v === "chart" ? { chart: "bar", y: numeric[0], x: "source_file", agg: "median" } : v === "summary" ? {} : { inputs: ["output/merged.csv"], outputs: [] }; draw(); }));
      form.append(text("Title", st.title, (v) => { st.title = v; if (!existing) st.name = slug(v); }, { placeholder: "e.g. Temperature over time" }));
      if (st.kind === "chart") {
        const p = st.params;
        if (!numeric.length) form.append(h("div", { class: "callout warn" }, icon("alert"), h("p", { text: "Charts need a numeric column (quantity or number) in the schema." })));
        form.append(select("Chart type", p.chart, [["bar", "Bar: one value per group"], ["line", "Line: a value over time or another number"], ["scatter", "Scatter: one number against another"]],
          (v) => { p.chart = v; p.x = v === "bar" ? "source_file" : xish[0]; draw(); }));
        form.append(select("Value (y axis)", p.y, numeric, (v) => { p.y = v; }));
        if (p.chart === "bar") {
          form.append(select("One bar per", p.x || "source_file", cats, (v) => { p.x = v; }));
          form.append(select("Summarize values with", p.agg || "median", ctx.app.meta.steps.aggs, (v) => { p.agg = v; }));
        } else {
          form.append(select("X axis", p.x, xish, (v) => { p.x = v; }));
          form.append(select("One series per", p.group_by ?? "source_file", [["source_file", "source file"], ["", "(single series)"], ...cats.slice(1)], (v) => { p.group_by = v; }));
        }
      } else if (st.kind === "summary") {
        form.append(h("p", { class: "muted small", text: "Count, mean, median, standard deviation, min and max of every numeric column, per source file." }));
      } else {
        const p = st.params;
        const scriptSel = select("Script", p.script || "", ["", ...pipe.scripts], (v) => { p.script = v; },
          "Python scripts live in this project's scripts/ folder. They're run with the same Python as Schemata.");
        const up = h("input", { type: "file", accept: ".py", onChange: async () => {
          if (!up.files.length) return;
          const fd = new FormData();
          fd.append("file", up.files[0]);
          try {
            const r = await upload(P(pid, "/scripts"), fd);
            pipe.scripts = r.scripts;
            p.script = up.files[0].name;
            toast(`Uploaded ${p.script}`);
            draw();
          } catch (e) { toastError(e); }
        } });
        form.append(scriptSel, h("div", { class: "field" }, h("label", { text: "…or upload a script" }), up));
        form.append(text("Inputs (comma-separated)", (p.inputs || []).join(", "), (v) => { p.inputs = splitList(v); },
          { hint: "Passed to the script first, in order. Usually output/merged.csv." }));
        form.append(text("Outputs (comma-separated)", (p.outputs || []).join(", "), (v) => { p.outputs = splitList(v); },
          { placeholder: "output/fit_results.csv", hint: "Passed after the inputs. Must be files under output/; the step is cached on these." }));
        form.append(h("div", { class: "callout info small" }, icon("info"), h("p", {},
          "Called as ", h("code", { text: "python scripts/<script> <inputs…> <outputs…>" }), ". The script runs on this computer with your permissions.")));
      }
    }
    draw();
    openDialog({
      title: existing ? `Edit ${existing.name}` : "Add a pipeline step",
      body: form,
      actions: [{ label: "Cancel" }, { label: existing ? "Save step" : "Add step", kind: "primary", onClick: async () => {
        if (!st.name) st.name = slug(st.title || `${st.kind}_${configured().length + 1}`);
        if (!st.title) st.title = st.name.replace(/[_-]/g, " ");
        const steps = configured().filter((x) => !existing || x.name !== existing.name);
        if (!existing) {
          let base = st.name, i = 2;
          while (steps.some((x) => x.name === st.name)) st.name = `${base}_${i++}`;
        }
        const idx = existing ? configured().findIndex((x) => x.name === existing.name) : -1;
        if (idx >= 0) steps.splice(idx, 0, st); else steps.push(st);
        const ok = await saveSteps(steps, existing ? `Saved ${st.name}` : `Added ${st.name}; it's stale until the next run`);
        if (ok) selected = st.name;
        return !ok;
      } }],
    });
  }

  function slug(t) {
    return (t || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40) || "";
  }

  function splitList(v) {
    return v.split(",").map((x) => x.trim()).filter(Boolean);
  }

  await load();

  return {
    onSummary(next, prev) {
      const changed = next.pipeline.running !== prev.pipeline.running || next.pipeline.current_step !== prev.pipeline.current_step
        || JSON.stringify(next.pipeline.stale) !== JSON.stringify(prev.pipeline.stale)
        || (next.last_run && next.last_run.id) !== (prev.last_run && prev.last_run.id)
        || next.needs_approval !== prev.needs_approval;
      if (changed) {
        if (prev.pipeline.running && !next.pipeline.running) {
          figureVersion = Date.now();
          const lr = next.last_run;
          if (lr) toast(lr.failed.length ? `Run finished with a failure in ${lr.failed.join(", ")}` : `Run finished: ran ${lr.ran.length}, skipped ${lr.skipped.length}`,
            lr.failed.length ? "bad" : "");
        }
        load();
      }
    },
  };
}
