// Home: what Schemata does, your projects, and creating a new one.
import { api } from "../api.js";
import { badge, button, h, icon, plural, relTime, toastError } from "../ui.js";

export async function mountHome(root, app) {
  app.setCrumbs([]);
  document.title = "Schemata";
  const page = h("div", { class: "page stack" });
  root.append(page);

  let projects = [];
  try {
    projects = await api("/projects");
  } catch (e) {
    toastError(e);
  }

  const create = async (template, name) => {
    try {
      const p = await api("/projects", { method: "POST", body: { name, template } });
      app.navigate(`#/p/${p.id}`);
    } catch (e) {
      toastError(e);
    }
  };

  const sampleBtn = button("Try the sample project", { kind: "primary", iconName: "beaker", size: "lg",
    onClick: () => create("sample", "Sample: three lab members, one experiment") });

  page.append(h("section", { class: "hero" },
    h("div", { class: "stack", style: { gap: "8px", maxWidth: "720px" } },
      h("h1", { text: "Merge messy lab spreadsheets into one clean dataset" }),
      h("p", { class: "lead", text: "Schemata matches columns across files, converts units, flags what doesn't agree, and writes " +
        "the Python script that did the cleaning. When new data arrives, it re-runs only the analysis steps that changed." })),
    projects.length ? null : sampleBtn));

  if (!projects.length) {
    page.append(h("div", { class: "card card-pad" }, h("div", { class: "how" },
      howStep(1, "Add files", "CSV, TSV or Excel, from different people, instruments or years."),
      howStep(2, "Review the mapping", "Every column match and unit conversion is shown before anything is applied."),
      howStep(3, "Get clean data + clean.py", "A tidy table and a readable script anyone can re-run."),
      howStep(4, "Re-run only what changed", "Drop in new data; stale steps re-run, everything else is reused."))));
  }

  // ------------------------------------------------------------ project list
  if (projects.length) {
    page.append(h("div", { class: "section-head" }, h("h2", { text: "Your projects" })));
    page.append(h("div", { class: "project-list" }, projects.map((p) => h("a", {
      class: "card project-card", href: `#/p/${p.id}`,
    },
    h("div", { class: "row between" }, h("h3", { text: p.name }), p.auto_run ? badge("Auto-run", "accent", "zap") : null),
    p.description ? h("p", { class: "muted small ellipsis", text: p.description }) : null,
    h("div", { class: "row small muted" },
      icon("file", { width: 14, height: 14 }), h("span", { text: plural(p.n_files, "file") }),
      p.n_unapproved ? badge(`${p.n_unapproved} to review`, "warn", "alert") : null),
    h("p", { class: "faint tiny", text: `Updated ${relTime(p.updated_at)}` })))));
  }

  // ------------------------------------------------------------ new project
  const nameInput = h("input", { type: "text", id: "new-name", placeholder: "e.g. Enzyme kinetics, Sept 2026",
    maxlength: 120, autocomplete: "off" });
  const templates = [
    { id: "lab", title: "Lab measurements", text: "Starts with sample ID, timestamp, temperature, mass and pressure. Edit any time." },
    { id: "blank", title: "Start empty", text: "Upload your files first, then let Schemata suggest the columns." },
    { id: "sample", title: "Sample data", text: "Three messy files (one secretly in °F) to see everything working.", tag: "Demo" },
  ];
  const choices = h("div", { class: "choice-cards", role: "radiogroup", "aria-label": "Starting point" },
    templates.map((t, i) => h("label", { class: "choice" },
      h("input", { type: "radio", name: "template", value: t.id, checked: i === 0 }),
      h("span", {}, h("strong", { text: t.title }), h("span", { class: "muted small", text: t.text })),
      t.tag ? badge(t.tag, "accent") : null)));

  const form = h("form", { class: "card card-pad stack", onSubmit: (e) => {
    e.preventDefault();
    const template = form.querySelector("input[name=template]:checked").value;
    create(template, nameInput.value.trim() || (template === "sample" ? "Sample project" : "Untitled project"));
  } },
  h("h2", { text: projects.length ? "New project" : "Or start your own project" }),
  h("div", { class: "field" }, h("label", { for: "new-name", text: "Project name" }), nameInput),
  h("div", { class: "field" }, h("span", { class: "label", text: "Start from" }), choices),
  h("div", { class: "row" }, button("Create project", { kind: "primary", type: "submit", iconName: "plus" })));
  page.append(form);

  page.append(h("p", { class: "faint small" }, icon("lock", { width: 14, height: 14 }),
    " Everything stays on this computer. Projects are stored in ",
    h("code", { text: app.meta.data_dir }), "."));

  return {};
}

function howStep(n, title, text) {
  return h("div", { class: "how-step" }, h("span", { class: "n", text: n }),
    h("div", {}, h("strong", { text: title }), h("p", { class: "muted small", text })));
}
