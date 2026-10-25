// Entry point: routing, theme, help, global shortcuts.
import { api } from "./api.js";
import { clear, h, icon, openDialog, prefs } from "./ui.js";
import { mountHome } from "./views/home.js";
import { mountProject } from "./views/project.js";

const main = document.getElementById("main");
const crumbs = document.getElementById("crumbs");
let current = null;

export const app = {
  meta: null,
  navigate(hash) {
    if (location.hash === hash) render();
    else location.hash = hash;
  },
  setCrumbs(items) {
    clear(crumbs);
    items.forEach((it, i) => {
      if (i) crumbs.append(icon("chevron-right", { width: 14, height: 14 }));
      crumbs.append(it.href ? h("a", { href: it.href, text: it.label })
        : h("span", { class: "current", "aria-current": "page", text: it.label }));
    });
  },
};

function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] === "p" && parts[1]) return { name: "project", pid: parts[1], tab: parts[2] || null };
  return { name: "home" };
}

async function render() {
  if (current && current.dispose) current.dispose();
  current = null;
  clear(main);
  const route = parseRoute();
  if (route.name === "project") current = await mountProject(main, route.pid, route.tab, app);
  else current = await mountHome(main, app);
  main.focus({ preventScroll: true });
}

// ---------------------------------------------------------------- theme
const THEMES = ["system", "light", "dark"];
const THEME_ICON = { system: "auto", light: "sun", dark: "moon" };
function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  const btn = document.getElementById("theme-btn");
  clear(btn).append(icon(THEME_ICON[theme]));
  btn.title = `Theme: ${theme} (click to change)`;
  btn.setAttribute("aria-label", `Theme: ${theme}. Click to change.`);
}
let theme = prefs.get("theme", "system");
if (!THEMES.includes(theme)) theme = "system";
applyTheme(theme);
document.getElementById("theme-btn").addEventListener("click", () => {
  theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
  prefs.set("theme", theme);
  applyTheme(theme);
});

// ---------------------------------------------------------------- help
function showHelp() {
  const step = (n, title, text) => h("div", { class: "how-step" }, h("span", { class: "n", text: n }),
    h("div", {}, h("strong", { text: title }), h("p", { class: "muted small", text })));
  const key = (k) => h("span", { class: "kbd", text: k });
  openDialog({
    title: "How Schemata works",
    wide: true,
    body: h("div", { class: "stack" },
      h("div", { class: "how" },
        step(1, "Add files", "CSV, TSV or Excel from anyone. Originals are never modified."),
        step(2, "Check the schema", "The clean columns you want, with units and valid ranges."),
        step(3, "Review & approve", "See every mapping, unit conversion and warning before anything is applied."),
        step(4, "Results & pipeline", "Get merged data, the clean.py script, figures, and incremental re-runs.")),
      h("h3", { text: "Keyboard shortcuts" }),
      h("div", { class: "shortcuts" },
        key("?"), h("span", { text: "Show this help" }),
        key("Alt + 1…6"), h("span", { text: "Switch project tabs (Files, Schema, Review, Results, Pipeline, Activity)" }),
        key("Ctrl/⌘ + Z"), h("span", { text: "Undo a mapping change (Review)" }),
        key("Ctrl/⌘ + Shift + Z"), h("span", { text: "Redo (Review)" }),
        key("Ctrl/⌘ + S"), h("span", { text: "Save the schema (Schema)" }),
        key("/"), h("span", { text: "Search the merged table (Results)" }),
        key("Esc"), h("span", { text: "Close a dialog" })),
      h("p", { class: "muted small" },
        "Everything runs on this computer. Data lives in ",
        h("code", { text: app.meta ? app.meta.data_dir : "the data folder" }),
        `. Schemata ${app.meta ? app.meta.version : ""}.`)),
  });
}
document.getElementById("help-btn").append(icon("help"));
document.getElementById("help-btn").addEventListener("click", showHelp);

function typing(e) {
  const t = e.target;
  return t instanceof HTMLElement && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));
}
document.addEventListener("keydown", (e) => {
  if (e.key === "?" && !typing(e) && !document.querySelector("dialog[open]")) {
    e.preventDefault();
    showHelp();
  }
});

window.addEventListener("hashchange", render);

(async () => {
  try {
    app.meta = await api("/meta");
  } catch (e) {
    clear(main).append(h("div", { class: "page" }, h("div", { class: "callout bad" }, icon("error"),
      h("div", { class: "grow" }, h("strong", { text: "Can't reach the Schemata server" }),
        h("p", { text: `${e.message} Start it with "python run.py" and reload this page.` })))));
    return;
  }
  render();
})();
