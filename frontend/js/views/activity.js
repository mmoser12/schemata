// Activity: the audit trail. Every upload, mapping edit (with the previous value), approval and run.
import { api, P } from "../api.js";
import { clear, h, icon } from "../ui.js";

const KINDS = {
  project: ["Project", "folder"], file: ["Files", "file"], schema: ["Schema", "columns"], mapping: ["Mapping", "edit"],
  approve: ["Approvals", "check-circle"], run: ["Runs", "play"], step: ["Steps", "flow"], auto: ["Auto-run", "zap"],
  export: ["Exports", "download"],
};

export async function mountActivity(root, ctx) {
  const { pid } = ctx;
  let events = [];
  let filter = new Set();
  let exhausted = false;
  const list = h("div");
  const chips = h("div", { class: "filter-chips" });
  const more = h("div", { class: "row" });

  async function load(append = false) {
    const before = append && events.length ? `&before=${events[events.length - 1].id}` : "";
    const batch = await api(P(pid, `/activity?limit=200${before}`));
    exhausted = batch.length < 200;
    events = append ? [...events, ...batch] : batch;
    render();
  }

  function render() {
    clear(chips);
    const present = [...new Set(events.map((e) => e.kind))];
    present.forEach((k) => {
      const [label, ic] = KINDS[k] || [k, "info"];
      chips.append(h("button", { class: "chip", type: "button", "aria-pressed": String(filter.has(k)),
        onClick: () => { filter.has(k) ? filter.delete(k) : filter.add(k); filter = new Set(filter); render(); } },
      icon(ic), label));
    });

    clear(list);
    const shown = events.filter((e) => !filter.size || filter.has(e.kind));
    if (!shown.length) {
      list.append(h("p", { class: "muted", text: events.length ? "No activity of the selected kinds." : "Nothing has happened yet." }));
    }
    const ul = h("ol", { class: "timeline" });
    let day = null;
    for (const e of shown) {
      const d = new Date(e.ts * 1000);
      const dayLabel = d.toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
      if (dayLabel !== day) {
        day = dayLabel;
        ul.append(h("li", { class: "day", text: dayLabel }));
      }
      const [, ic] = KINDS[e.kind] || [e.kind, "info"];
      ul.append(h("li", { class: "event" },
        h("time", { datetime: d.toISOString(), text: d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) }),
        h("span", { class: "ico" }, icon(ic)),
        h("div", { class: "grow" }, h("div", { text: e.message }), details(e))));
    }
    list.append(ul);
    clear(more);
    if (!exhausted) more.append(h("button", { class: "btn sm", type: "button", text: "Load older activity", onClick: () => load(true) }));
  }

  function details(e) {
    const d = e.detail || {};
    const keys = Object.keys(d).filter((k) => d[k] !== null && d[k] !== undefined && !(Array.isArray(d[k]) && !d[k].length));
    if (!keys.length) return null;
    if ("previous" in d && "value" in d && typeof d.value !== "object") {
      const show = (v) => (v === "<auto>" ? "automatic" : v === null ? "dropped" : String(v));
      return h("div", { class: "change" }, h("span", { class: "prev", text: show(d.previous) }), " → ", h("span", { class: "now", text: show(d.value) }));
    }
    return h("details", {}, h("summary", { text: "Details" }),
      h("dl", { class: "kv small", style: { marginTop: "6px" } }, keys.flatMap((k) => [
        h("dt", { text: k.replace(/_/g, " ") }),
        h("dd", {}, typeof d[k] === "object" ? h("pre", { class: "log", text: JSON.stringify(d[k], null, 2) })
          : h("span", { class: k.includes("sha256") ? "mono small" : "", text: String(d[k]) })),
      ])));
  }

  root.append(h("div", { class: "stack" },
    h("div", { class: "section-head" },
      h("div", {}, h("h2", { text: "Activity" }),
        h("p", { class: "muted", text: "A permanent record of what happened to this project: who changed which mapping from what to what, " +
          "which file versions were approved, and every run." })),
      h("a", { class: "btn", href: `/api${P(pid, "/download/activity.csv")}`, download: "" }, icon("download"), h("span", { text: "Export CSV" }))),
    chips, list, more));
  await load();

  return {
    onSummary(next, prev) {
      if (next.updated_at !== prev.updated_at) load();
    },
  };
}
