"use client";

import { useCallback, useEffect, useState } from "react";
import {
  type Benchmark,
  type ExecutionReport,
  type PipelineGraph,
  type StepStatus,
  figureUrl,
  getBenchmark,
  getPipelineGraph,
  getPipelineStatus,
  runPipeline,
} from "@/lib/api";

const FIGURES = ["temp_by_file.png", "timeseries.png"];

function fmtSeconds(s: number): string {
  return s < 1 ? `${Math.round(s * 1000)} ms` : `${s.toFixed(2)} s`;
}

function StatusBadge({ fresh }: { fresh: boolean }) {
  return (
    <span
      className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
        fresh
          ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300"
          : "bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300"
      }`}
    >
      {fresh ? "fresh" : "stale"}
    </span>
  );
}

export default function PipelinePage() {
  const [graph, setGraph] = useState<PipelineGraph | null>(null);
  const [statuses, setStatuses] = useState<Record<string, StepStatus>>({});
  const [report, setReport] = useState<ExecutionReport | null>(null);
  const [benchmark, setBenchmark] = useState<Benchmark | null>(null);
  const [busy, setBusy] = useState<"run" | "force" | "benchmark" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [figureVersion, setFigureVersion] = useState(0);
  const [brokenFigures, setBrokenFigures] = useState<Record<string, boolean>>({});

  const refresh = useCallback(async () => {
    const [g, s] = await Promise.all([getPipelineGraph(), getPipelineStatus()]);
    setGraph(g);
    setStatuses(Object.fromEntries(s.map((st) => [st.step, st])));
  }, []);

  useEffect(() => {
    // Fetch-on-mount: setState happens in the promise callback once the
    // external system (the API) responds, not synchronously in the effect
    // body -- exactly the pattern react-hooks itself documents as valid,
    // but its static check can't tell that apart from a sync call here.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh().catch((e) => setError(e instanceof Error ? e.message : "failed to load pipeline"));
  }, [refresh]);

  async function handleRun(force: boolean) {
    setBusy(force ? "force" : "run");
    setError(null);
    try {
      const r = await runPipeline(force);
      setReport(r);
      setFigureVersion((v) => v + 1);
      setBrokenFigures({});
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "run failed");
    } finally {
      setBusy(null);
    }
  }

  async function handleBenchmark() {
    setBusy("benchmark");
    setError(null);
    try {
      setBenchmark(await getBenchmark());
      setFigureVersion((v) => v + 1);
      setBrokenFigures({});
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "benchmark failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Pipeline</h1>
      <p className="mt-2 text-zinc-600 dark:text-zinc-400">
        clean → analyze → figures. Only the steps downstream of a change re-run; the rest are
        reused from cache.
      </p>

      <div className="mt-6 flex flex-wrap gap-3">
        <button
          onClick={() => handleRun(false)}
          disabled={busy !== null}
          className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700 disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          {busy === "run" ? "Running…" : "Run (only what's stale)"}
        </button>
        <button
          onClick={() => handleRun(true)}
          disabled={busy !== null}
          className="rounded-lg border border-zinc-300 px-4 py-2 text-sm font-medium hover:bg-zinc-100 disabled:opacity-40 dark:border-zinc-700 dark:hover:bg-zinc-800"
        >
          {busy === "force" ? "Running…" : "Force full re-run"}
        </button>
        <button
          onClick={handleBenchmark}
          disabled={busy !== null}
          className="rounded-lg border border-zinc-300 px-4 py-2 text-sm font-medium hover:bg-zinc-100 disabled:opacity-40 dark:border-zinc-700 dark:hover:bg-zinc-800"
        >
          {busy === "benchmark" ? "Benchmarking…" : "Benchmark full vs. incremental"}
        </button>
      </div>

      {error && (
        <p className="mt-4 rounded-md bg-red-50 px-4 py-2 text-sm text-red-800 dark:bg-red-950/40 dark:text-red-300">
          {error}
        </p>
      )}

      {graph && (
        <section className="mt-8">
          <div className="flex flex-wrap items-stretch gap-3">
            {graph.order.map((name, i) => {
              const step = graph.steps.find((s) => s.name === name);
              const status = statuses[name];
              if (!step) return null;
              return (
                <div key={name} className="flex items-center gap-3">
                  <div className="w-56 rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
                    <div className="flex items-center justify-between">
                      <span className="font-medium">{step.title}</span>
                      {status && <StatusBadge fresh={status.fresh} />}
                    </div>
                    <p className="mt-1 text-xs text-zinc-500">{step.description}</p>
                    {status && (
                      <p className="mt-2 text-xs text-zinc-400" title={status.reason}>
                        {status.reason.length > 60 ? `${status.reason.slice(0, 60)}…` : status.reason}
                      </p>
                    )}
                    {status && status.last_duration_s > 0 && (
                      <p className="mt-1 text-xs text-zinc-400">last run: {fmtSeconds(status.last_duration_s)}</p>
                    )}
                  </div>
                  {i < graph.order.length - 1 && <span className="text-zinc-400">→</span>}
                </div>
              );
            })}
          </div>
        </section>
      )}

      {report && (
        <section className="mt-8">
          <h2 className="font-medium">
            Last run ({report.mode}) – {fmtSeconds(report.total_duration_s)}
          </h2>
          <ul className="mt-2 space-y-1 text-sm">
            {report.results.map((r) => (
              <li key={r.step} className="flex items-center gap-2">
                <span
                  className={`inline-block w-20 rounded px-2 py-0.5 text-center text-xs font-medium ${
                    !r.ran
                      ? "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400"
                      : r.returncode === 0
                        ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300"
                        : "bg-red-100 text-red-800 dark:bg-red-950/50 dark:text-red-300"
                  }`}
                >
                  {!r.ran ? "skipped" : r.returncode === 0 ? "ran" : "failed"}
                </span>
                <span className="font-mono">{r.step}</span>
                <span className="text-zinc-500">
                  {r.ran ? fmtSeconds(r.duration_s) : ""} – {r.reason}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {benchmark && (
        <section className="mt-8 rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
          <h2 className="font-medium">Full re-run vs. incremental</h2>
          <div className="mt-3 flex flex-wrap gap-8 text-sm">
            <div>
              <div className="text-zinc-500">Full run (forced)</div>
              <div className="text-lg font-semibold">{fmtSeconds(benchmark.full.total_duration_s)}</div>
            </div>
            <div>
              <div className="text-zinc-500">Incremental (no changes)</div>
              <div className="text-lg font-semibold">{fmtSeconds(benchmark.incremental.total_duration_s)}</div>
            </div>
            {benchmark.speedup_x && (
              <div>
                <div className="text-zinc-500">Speedup</div>
                <div className="text-lg font-semibold text-emerald-600 dark:text-emerald-400">
                  {benchmark.speedup_x}×
                </div>
              </div>
            )}
          </div>
        </section>
      )}

      <section className="mt-8">
        <h2 className="font-medium">Figures</h2>
        <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
          {FIGURES.map((name) =>
            brokenFigures[name] ? (
              <div
                key={name}
                className="flex aspect-[7/4] items-center justify-center rounded-lg border border-dashed border-zinc-300 text-sm text-zinc-400 dark:border-zinc-700"
              >
                Run the pipeline to generate {name}
              </div>
            ) : (
              // eslint-disable-next-line @next/next/no-img-element -- served by the FastAPI backend, not Next's image pipeline
              <img
                key={name}
                src={figureUrl(name, figureVersion)}
                alt={name}
                onError={() => setBrokenFigures((b) => ({ ...b, [name]: true }))}
                className="w-full rounded-lg border border-zinc-200 bg-white dark:border-zinc-800"
              />
            ),
          )}
        </div>
      </section>
    </div>
  );
}
