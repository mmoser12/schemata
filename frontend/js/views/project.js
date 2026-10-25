// Project shell: header, status, workflow tabs, polling. Tab views live in their own modules.
import { api, P } from "../api.js";
import { badge, clear, confirmDialog, h, icon, plural, toast, toastError } from "../ui.js";
import { mountActivity } from "./activity.js";
import { mountFiles } from "./files.js";
import { mountPipeline } from "./pipeline.js";
import { mountResults } from "./results.js";
import { mountReview } from "./review.js";
import { mountSchema } from "./schema.js";

const TABS = [
  { id: "files", label: "Files", mount: mountFiles },
  { id: "schema", label: "Schema", mount: mountSchema },
  { id: "review", label: "Review", mount: mountReview },
  { id: "results", label: "Results", mount: mountResults },
  { id: "pipeline", label: "Pipeline", mount: mountPipeline },
  { id: "activity", label: "Activity", mount: mountActivity },
];

function defaultTab(s) {
  if (!s.files.length) return "files";
  if (!s.n_targets) return "schema";
  if (s.needs_approval) return "review";
  return s.has_results ? "results" : "pipeline";
}

function stepState(id, s) {
  const p = s.pipeline;
  switch (id) {
    case "files": return s.files.some((f) => !f.readable) ? "attn" : s.files.length ? "done" : "";
    case "schema": return s.n_targets ? "done" : s.files.length ? "attn" : "";
    case "review": return !s.files.length ? "" : s.needs_approval ? "attn" : "done";
    case "results": return s.has_results ? "done" : "";
    case "pipeline": return p.error || (s.last_run && s.last_run.failed.length) ? "attn"
      : s.has_results && !p.stale.length ? "done" : p.stale.length && s.has_results ? "attn" : "";
    default: return "";
  }
}

export async function mountProject(root, pid, tab, app) {
  let summary;
  try {
    summary = await api(P(pid));
  } catch (e) {
    root.append(h("div", { class: "page" }, h("div", { class: "empty" },
      icon("folder", { class: "big" }), h("h3", { text: e.status === 404 ? "Project not found" : "Couldn't open the project" }),
      h("p", { text: e.message }), h("div", { class: "btn-group" }, h("a", { class: "btn", href: "#/", text: "All projects" })))));
    return {};
  }
  if (!tab || !TABS.some((t) => t.id === tab)) {
    history.replaceState(null, "", `#/p/${pid}/${defaultTab(summary)}`);
    tab = defaultTab(summary);
  }

  const page = h("div", { class: "page" });
  const head = h("div", { class: "project-head" });
  const tabsEl = h("nav", { class: "tabs", "aria-label": "Project steps" });
  const content = h("div", { class: "tab-content" });
  page.append(head, tabsEl, content);
  root.append(page);

  let view = null;
  let editing = false;
  let disposed = false;

  const ctx = {
    pid,
    app,
    get summary() { return summary; },
    goto: (t) => app.navigate(`#/p/${pid}/${t}`),
    refresh: async () => {
      try {
        const next = await api(P(pid));
        const prev = summary;
        summary = next;
        renderHead();
        renderTabs();
        if (view && view.onSummary) view.onSummary(next, prev);
      } catch (e) {
        if (e.status === 404) app.navigate("#/");
      }
      return summary;
    },
  };

  function renderHead() {
    if (editing) return;
    const s = summary;
    app.setCrumbs([{ label: "Projects", href: "#/" }, { label: s.name }]);
    document.title = `${s.name} · Schemata`;
    const p = s.pipeline;
    const status = [];
    status.push(badge(plural(s.files.length, "file"), "outline", "file"));
    if (s.unapproved.length) status.push(badge(`${s.unapproved.length} to review`, "warn", "alert"));
    if (s.counts.error) status.push(badge(plural(s.counts.error, "error"), "bad", "error"));
    if (p.running) status.push(badge(`Running${p.current_step ? `: ${p.current_step}` : "…"}`, "accent", "refresh"));
    else if (p.error) status.push(badge("Last run crashed", "bad", "error"));
    else if (s.last_run && s.last_run.failed.length) status.push(badge("Last run failed", "bad", "error"));
    else if (s.has_results && !p.stale.length) status.push(badge("Up to date", "good", "check"));
    else if (s.has_results && p.stale.length) status.push(badge(`${plural(p.stale.length, "step")} stale`, "warn", "clock"));

    const auto = h("input", { type: "checkbox", role: "switch", checked: s.auto_run, "aria-describedby": "auto-help",
      onChange: async () => {
        try {
          await api(P(pid), { method: "PATCH", body: { auto_run: auto.checked } });
          toast(auto.checked ? "Auto-run on: new files that map cleanly are merged and analyzed automatically"
            : "Auto-run off");
          ctx.refresh();
        } catch (e) { toastError(e); auto.checked = !auto.checked; }
      } });

    const title = h("div", { class: "project-title" }, h("h1", { text: s.name }),
      h("button", { class: "icon-btn sm", type: "button", "aria-label": "Rename project", title: "Rename",
        onClick: startRename }, icon("edit")));

    clear(head).append(
      h("div", { class: "grow" }, title, h("div", { class: "status-line" }, status)),
      h("div", { class: "row" },
        h("label", { class: "switch", title: "Watch the raw folder and re-run automatically" }, auto, h("span", { text: "Auto-run" })),
        h("span", { id: "auto-help", hidden: true, text: "When on, files added to this project are merged and analyzed automatically if they map without warnings." }),
        h("a", { class: "btn", href: `/api/projects/${pid}/download/bundle.zip`, download: "",
          title: "Zip with raw files, clean.py, outputs, audit trail and a hash manifest" }, icon("download"), h("span", { text: "Export bundle" })),
        h("button", { class: "icon-btn", type: "button", "aria-label": "Delete project", title: "Delete project",
          onClick: deleteProject }, icon("trash"))));
  }

  function startRename() {
    editing = true;
    const input = h("input", { type: "text", value: summary.name, maxlength: 120, "aria-label": "Project name" });
    const done = async (save) => {
      if (!editing) return;
      editing = false;
      if (save && input.value.trim() && input.value.trim() !== summary.name) {
        try {
          await api(P(pid), { method: "PATCH", body: { name: input.value.trim() } });
        } catch (e) { toastError(e); }
      }
      ctx.refresh();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") done(true);
      if (e.key === "Escape") done(false);
    });
    input.addEventListener("blur", () => done(true));
    const titleEl = head.querySelector(".project-title");
    clear(titleEl).append(input);
    input.focus();
    input.select();
  }

  async function deleteProject() {
    const ok = await confirmDialog({
      title: "Delete this project?",
      message: `"${summary.name}" and everything in it (raw files, results, history) will be permanently deleted. ` +
        "Export a bundle first if you might need it.",
      confirm: "Delete project", kind: "primary danger",
    });
    if (!ok) return;
    try {
      await api(P(pid), { method: "DELETE" });
      toast("Project deleted");
      app.navigate("#/");
    } catch (e) { toastError(e); }
  }

  function renderTabs() {
    clear(tabsEl);
    TABS.forEach((t, i) => {
      const st = stepState(t.id, summary);
      const marker = t.id === "activity" ? h("span", { class: "step-n" }, icon("activity"))
        : h("span", { class: `step-n ${st}` }, st === "done" ? icon("check") : st === "attn" ? icon("alert") : String(i + 1));
      tabsEl.append(h("a", { class: "tab", href: `#/p/${pid}/${t.id}`, "aria-current": t.id === tab ? "page" : null,
        title: `Alt+${i + 1}` }, marker, t.label));
    });
  }

  renderHead();
  renderTabs();
  const def = TABS.find((t) => t.id === tab);
  view = (await def.mount(content, ctx)) || {};

  // ---------------------------------------------------------------- polling
  const onKey = (e) => {
    if (e.altKey && !e.ctrlKey && !e.metaKey && /^[1-6]$/.test(e.key)) {
      e.preventDefault();
      ctx.goto(TABS[Number(e.key) - 1].id);
    }
  };
  document.addEventListener("keydown", onKey);

  let timer = null;
  const poll = async () => {
    if (disposed) return;
    if (document.visibilityState === "visible" && !document.querySelector("dialog[open]")) await ctx.refresh();
    timer = setTimeout(poll, summary.pipeline.running ? 1200 : 4000);
  };
  timer = setTimeout(poll, 4000);

  return {
    dispose() {
      disposed = true;
      clearTimeout(timer);
      document.removeEventListener("keydown", onKey);
      if (view && view.dispose) view.dispose();
    },
  };
}

