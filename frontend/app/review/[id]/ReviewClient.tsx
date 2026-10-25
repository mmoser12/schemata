"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  type ApplyResponse,
  type ColumnMapping,
  type FilePlan,
  type Issue,
  type MergePlan,
  type PreviewResponse,
  applyPlan,
  cleanPyUrl,
  mergedCsvUrl,
} from "@/lib/api";
import { loadPreview } from "@/lib/session";

const SEVERITY_STYLE: Record<Issue["severity"], string> = {
  error: "bg-red-50 text-red-800 border-red-200 dark:bg-red-950/40 dark:text-red-300 dark:border-red-900",
  warning:
    "bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900",
  info: "bg-zinc-50 text-zinc-600 border-zinc-200 dark:bg-zinc-900 dark:text-zinc-400 dark:border-zinc-800",
};

const METHOD_STYLE: Record<string, string> = {
  rule: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300",
  fuzzy: "bg-blue-100 text-blue-800 dark:bg-blue-950/50 dark:text-blue-300",
  llm: "bg-purple-100 text-purple-800 dark:bg-purple-950/50 dark:text-purple-300",
  user: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
};

function emptyMapping(source: string): ColumnMapping {
  return {
    source,
    target: null,
    confidence: 0,
    method: "user",
    unit: null,
    unit_source: null,
    unit_note: "",
    date_format: null,
    date_note: "",
  };
}

function reassign(plan: MergePlan, fileIdx: number, source: string, newTarget: string): MergePlan {
  const files = plan.files.map((fp, i) => {
    if (i !== fileIdx) return fp;
    const existing = fp.mappings.find((m) => m.source === source);
    const mappings = fp.mappings.filter((m) => m.source !== source);
    const unmapped = fp.unmapped.filter((u) => u !== source);
    if (newTarget === "") {
      return { ...fp, mappings, unmapped: [...unmapped, source] };
    }
    const base = existing ?? emptyMapping(source);
    return { ...fp, mappings: [...mappings, { ...base, target: newTarget }], unmapped };
  });
  return { ...plan, files };
}

function setUnit(plan: MergePlan, fileIdx: number, source: string, newUnit: string): MergePlan {
  const files = plan.files.map((fp, i) => {
    if (i !== fileIdx) return fp;
    const mappings = fp.mappings.map((m) =>
      m.source === source ? { ...m, unit: newUnit || null, unit_source: "user" } : m,
    );
    return { ...fp, mappings };
  });
  return { ...plan, files };
}

function targetsUsed(fp: FilePlan): Set<string> {
  return new Set(fp.mappings.map((m) => m.target).filter((t): t is string => Boolean(t)));
}

export default function ReviewClient() {
  const id = useParams<{ id: string }>().id;
  const [preview, setPreview] = useState<PreviewResponse | null | undefined>(undefined);
  const [plan, setPlan] = useState<MergePlan | null>(null);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [applyResult, setApplyResult] = useState<ApplyResponse | null>(null);

  useEffect(() => {
    // sessionStorage is a browser API, unavailable during SSR/first paint;
    // syncing it into state after mount is the sanctioned use of an effect
    // ("synchronizing with an external system"), not derived-state-in-effect.
    /* eslint-disable react-hooks/set-state-in-effect */
    const p = loadPreview(id);
    setPreview(p);
    setPlan(p ? structuredClone(p.plan) : null);
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [id]);

  const unitConflicts = useMemo(
    () => plan?.issues.filter((i) => i.kind === "unit_conflict") ?? [],
    [plan],
  );
  const otherIssues = useMemo(
    () => plan?.issues.filter((i) => i.kind !== "unit_conflict") ?? [],
    [plan],
  );

  if (preview === undefined) {
    return <div className="mx-auto max-w-5xl px-6 py-10 text-zinc-500">Loading…</div>;
  }

  if (preview === null || plan === null) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-10">
        <p className="text-zinc-600 dark:text-zinc-400">
          No merge session found for <code>{id}</code> in this tab (sessions aren&apos;t persisted
          across reloads yet).
        </p>
        <Link href="/" className="mt-3 inline-block text-blue-600 underline dark:text-blue-400">
          Upload files again
        </Link>
      </div>
    );
  }

  const targetKeys = Object.keys(plan.targets);

  async function handleApply() {
    if (!plan) return;
    setApplying(true);
    setApplyError(null);
    try {
      setApplyResult(await applyPlan(id, plan));
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : "Apply failed");
    } finally {
      setApplying(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Review the mapping</h1>
      <p className="mt-2 text-zinc-600 dark:text-zinc-400">
        {plan.files.length} file{plan.files.length === 1 ? "" : "s"} mapped onto{" "}
        {targetKeys.length} target columns. Fix anything below, then apply.
      </p>

      {unitConflicts.length > 0 && (
        <section className="mt-6">
          <h2 className="font-medium text-red-700 dark:text-red-400">
            Unit conflicts ({unitConflicts.length})
          </h2>
          <ul className="mt-2 space-y-2">
            {unitConflicts.map((issue, i) => (
              <li key={i} className={`rounded-md border px-3 py-2 text-sm ${SEVERITY_STYLE[issue.severity]}`}>
                <span className="font-mono text-xs uppercase">{issue.severity}</span>{" "}
                {issue.file && <span className="font-medium">{issue.file}</span>}
                {issue.column && <span className="font-mono"> / {issue.column}</span>}: {issue.message}
              </li>
            ))}
          </ul>
        </section>
      )}

      {plan.files.map((fp, fileIdx) => (
        <section
          key={fp.file}
          className="mt-6 overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900"
        >
          <h3 className="border-b border-zinc-200 bg-zinc-50 px-4 py-2 font-medium dark:border-zinc-800 dark:bg-zinc-800/50">
            {fp.file}
          </h3>
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-zinc-500">
              <tr>
                <th className="px-4 py-2">Source column</th>
                <th className="px-4 py-2">Target</th>
                <th className="px-4 py-2">Matched by</th>
                <th className="px-4 py-2">Unit</th>
                <th className="px-4 py-2">Note</th>
              </tr>
            </thead>
            <tbody>
              {[...fp.mappings.map((m) => ({ source: m.source, mapping: m })),
                ...fp.unmapped.map((u) => ({ source: u, mapping: null as ColumnMapping | null }))]
                .map(({ source, mapping }) => {
                  const used = targetsUsed(fp);
                  const spec = mapping?.target ? plan.targets[mapping.target] : undefined;
                  const isQuantity = spec?.kind === "quantity";
                  const unitConflictHere = unitConflicts.some(
                    (i) => i.file === fp.file && i.column === source,
                  );
                  return (
                    <tr
                      key={source}
                      className={`border-t border-zinc-100 dark:border-zinc-800 ${
                        unitConflictHere ? "bg-red-50/60 dark:bg-red-950/20" : ""
                      }`}
                    >
                      <td className="px-4 py-2 font-mono text-xs">{source}</td>
                      <td className="px-4 py-2">
                        <select
                          value={mapping?.target ?? ""}
                          onChange={(e) => setPlan(reassign(plan, fileIdx, source, e.target.value))}
                          className="rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900"
                        >
                          <option value="">— unmapped —</option>
                          {targetKeys.map((t) => (
                            <option
                              key={t}
                              value={t}
                              disabled={mapping?.target !== t && used.has(t)}
                            >
                              {t}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-4 py-2">
                        {mapping ? (
                          <span
                            className={`rounded px-2 py-0.5 text-xs font-medium ${
                              METHOD_STYLE[mapping.method] ?? METHOD_STYLE.user
                            }`}
                          >
                            {mapping.method} · {(mapping.confidence * 100).toFixed(0)}%
                          </span>
                        ) : (
                          <span className="text-zinc-400">—</span>
                        )}
                      </td>
                      <td className="px-4 py-2">
                        {isQuantity ? (
                          <input
                            type="text"
                            value={mapping?.unit ?? ""}
                            onChange={(e) => setPlan(setUnit(plan, fileIdx, source, e.target.value))}
                            placeholder={spec?.unit}
                            className="w-24 rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900"
                          />
                        ) : mapping?.date_format ? (
                          <span className="font-mono text-xs text-zinc-500">{mapping.date_format}</span>
                        ) : (
                          <span className="text-zinc-400">—</span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-xs text-zinc-500">
                        {mapping?.unit_note || mapping?.date_note || ""}
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </section>
      ))}

      {otherIssues.length > 0 && (
        <details className="mt-6">
          <summary className="cursor-pointer text-sm text-zinc-500">
            {otherIssues.length} other note{otherIssues.length === 1 ? "" : "s"}
          </summary>
          <ul className="mt-2 space-y-1.5">
            {otherIssues.map((issue, i) => (
              <li key={i} className={`rounded-md border px-3 py-1.5 text-sm ${SEVERITY_STYLE[issue.severity]}`}>
                <span className="font-mono text-xs uppercase">{issue.severity}</span>{" "}
                {issue.file && <span className="font-medium">{issue.file}</span>}
                {issue.column && <span className="font-mono"> / {issue.column}</span>}: {issue.message}
              </li>
            ))}
          </ul>
        </details>
      )}

      <button
        onClick={handleApply}
        disabled={applying}
        className="mt-6 w-full rounded-lg bg-zinc-900 px-5 py-3 font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
      >
        {applying ? "Merging…" : "Apply & merge"}
      </button>

      {applyError && (
        <p className="mt-4 rounded-md bg-red-50 px-4 py-2 text-sm text-red-800 dark:bg-red-950/40 dark:text-red-300">
          {applyError}
        </p>
      )}

      {applyResult && (
        <section className="mt-8">
          <h2 className="font-medium">
            Merged {applyResult.n_rows} row{applyResult.n_rows === 1 ? "" : "s"}
          </h2>

          {applyResult.issues.filter((i) => i.severity !== "info").length > 0 && (
            <ul className="mt-2 space-y-1.5">
              {applyResult.issues
                .filter((i) => i.severity !== "info")
                .map((issue, i) => (
                  <li key={i} className={`rounded-md border px-3 py-1.5 text-sm ${SEVERITY_STYLE[issue.severity]}`}>
                    <span className="font-mono text-xs uppercase">{issue.severity}</span>{" "}
                    {issue.file && <span className="font-medium">{issue.file}</span>}
                    {issue.column && <span className="font-mono"> / {issue.column}</span>}: {issue.message}
                  </li>
                ))}
            </ul>
          )}

          <div className="mt-4 overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
            <table className="w-full text-sm">
              <thead className="bg-zinc-50 text-left text-xs uppercase text-zinc-500 dark:bg-zinc-800/50">
                <tr>
                  {applyResult.columns.map((c) => (
                    <th key={c} className="whitespace-nowrap px-3 py-2">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {applyResult.rows.slice(0, 10).map((row, i) => (
                  <tr key={i} className="border-t border-zinc-100 dark:border-zinc-800">
                    {applyResult.columns.map((c) => (
                      <td key={c} className="whitespace-nowrap px-3 py-1.5 font-mono text-xs">
                        {String(row[c] ?? "")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-4 flex flex-wrap gap-3">
            <a
              href={mergedCsvUrl(id)}
              className="rounded-lg border border-zinc-300 px-4 py-2 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
            >
              Download merged.csv
            </a>
            <a
              href={cleanPyUrl(id)}
              className="rounded-lg border border-zinc-300 px-4 py-2 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
            >
              Download clean.py
            </a>
            <Link
              href="/pipeline"
              className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
            >
              Go to pipeline →
            </Link>
          </div>
        </section>
      )}
    </div>
  );
}
