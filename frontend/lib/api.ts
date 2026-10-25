/**
 * Thin client for the Schemata FastAPI backend.
 *
 * Mirrors the shapes in backend/merger/plan.py and backend/pipeline/* exactly
 * (field names included) so responses can be used as-is without translation.
 */

export interface ColumnMapping {
  source: string;
  target: string | null;
  confidence: number;
  method: string;
  unit: string | null;
  unit_source: string | null;
  unit_note: string;
  date_format: string | null;
  date_note: string;
}

export interface FilePlan {
  file: string;
  mappings: ColumnMapping[];
  unmapped: string[];
}

export type IssueSeverity = "error" | "warning" | "info";
export type IssueKind = "unit_conflict" | "date" | "mapping" | "validation" | "general";

export interface Issue {
  severity: IssueSeverity;
  file: string | null;
  column: string | null;
  message: string;
  kind: IssueKind;
}

export interface TargetSpec {
  kind: "id" | "datetime" | "quantity" | "text";
  unit?: string;
  synonyms?: string[];
  valid_range?: [number, number];
}

export interface MergePlan {
  targets: Record<string, TargetSpec>;
  files: FilePlan[];
  issues: Issue[];
}

export interface ColumnProfile {
  inferred_type: "numeric" | "datetime" | "text";
  null_rate: number;
  n_unique: number;
  examples: string[];
  min?: number;
  max?: number;
  median?: number;
}

export interface FileProfile {
  n_rows: number;
  columns: Record<string, ColumnProfile>;
}

export interface PreviewResponse {
  session_id: string;
  profiles: Record<string, FileProfile>;
  plan: MergePlan;
}

export interface ApplyResponse {
  n_rows: number;
  columns: string[];
  rows: Record<string, unknown>[];
  issues: Issue[];
}

export interface StepInfo {
  name: string;
  command: string[];
  inputs: string[];
  outputs: string[];
  title: string;
  description: string;
  depends_on: string[];
  feeds: string[];
}

export interface PipelineGraph {
  steps: StepInfo[];
  edges: { from: string; to: string }[];
  order: string[];
}

export interface StepStatus {
  step: string;
  fresh: boolean;
  reason: string;
  outputs_exist: boolean;
  last_ran_at: number | null;
  last_duration_s: number;
}

export interface RunResult {
  step: string;
  ran: boolean;
  reason: string;
  duration_s: number;
  returncode: number | null;
  stdout: string;
  stderr: string;
}

export interface ExecutionReport {
  mode: "full" | "incremental";
  total_duration_s: number;
  ran: string[];
  skipped: string[];
  failed: string[];
  results: RunResult[];
}

export interface Benchmark {
  full: ExecutionReport;
  incremental: ExecutionReport;
  speedup_x: number | null;
}

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${text ? `: ${text}` : ""}`);
  }
  return (await res.json()) as T;
}

export async function uploadFiles(files: File[]): Promise<PreviewResponse> {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  const res = await fetch(`${API_BASE_URL}/merge/preview`, { method: "POST", body: form });
  return asJson<PreviewResponse>(res);
}

export async function applyPlan(sessionId: string, plan: MergePlan): Promise<ApplyResponse> {
  const res = await fetch(`${API_BASE_URL}/merge/${sessionId}/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(plan),
  });
  return asJson<ApplyResponse>(res);
}

export function mergedCsvUrl(sessionId: string): string {
  return `${API_BASE_URL}/merge/${sessionId}/merged.csv`;
}

export function cleanPyUrl(sessionId: string): string {
  return `${API_BASE_URL}/merge/${sessionId}/clean.py`;
}

export async function getPipelineGraph(): Promise<PipelineGraph> {
  return asJson(await fetch(`${API_BASE_URL}/pipeline`, { cache: "no-store" }));
}

export async function getPipelineStatus(): Promise<StepStatus[]> {
  return asJson(await fetch(`${API_BASE_URL}/pipeline/status`, { cache: "no-store" }));
}

export async function runPipeline(force = false, only?: string[]): Promise<ExecutionReport> {
  const res = await fetch(`${API_BASE_URL}/pipeline/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force, only }),
  });
  return asJson<ExecutionReport>(res);
}

export async function getBenchmark(): Promise<Benchmark> {
  return asJson(await fetch(`${API_BASE_URL}/pipeline/benchmark`, { cache: "no-store" }));
}

export function figureUrl(name: string, cacheBust?: number): string {
  return `${API_BASE_URL}/pipeline/figures/${name}${cacheBust ? `?v=${cacheBust}` : ""}`;
}
