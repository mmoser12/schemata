"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { uploadFiles } from "@/lib/api";
import { savePreview } from "@/lib/session";

const ACCEPTED = [".csv", ".xlsx", ".xls", ".xlsm"];

function isAccepted(file: File): boolean {
  const name = file.name.toLowerCase();
  return ACCEPTED.some((ext) => name.endsWith(ext));
}

export default function UploadPage() {
  const router = useRouter();
  const [files, setFiles] = useState<File[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  function addFiles(incoming: FileList | File[]) {
    const accepted = Array.from(incoming).filter(isAccepted);
    setError(
      accepted.length < incoming.length
        ? "Some files were skipped (only .csv, .xlsx, .xls, .xlsm are supported)."
        : null,
    );
    setFiles((prev) => {
      const byName = new Map(prev.map((f) => [f.name, f]));
      for (const f of accepted) byName.set(f.name, f);
      return Array.from(byName.values());
    });
  }

  function removeFile(name: string) {
    setFiles((prev) => prev.filter((f) => f.name !== name));
  }

  async function handleSubmit() {
    if (files.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const preview = await uploadFiles(files);
      savePreview(preview);
      router.push(`/review/${preview.session_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-3xl flex-1 px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Drop in your files</h1>
      <p className="mt-2 text-zinc-600 dark:text-zinc-400">
        CSV or Excel, from different people, instruments, or years. Schemata matches headers,
        detects units and dates, and flags anything that doesn&apos;t agree before merging.
      </p>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragActive(false);
          addFiles(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        className={`mt-6 flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-16 text-center transition-colors ${
          dragActive
            ? "border-blue-500 bg-blue-50 dark:bg-blue-950/30"
            : "border-zinc-300 bg-white hover:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-900"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED.join(",")}
          className="hidden"
          onChange={(e) => e.target.files && addFiles(e.target.files)}
        />
        <p className="font-medium">Drag files here, or click to choose</p>
        <p className="mt-1 text-sm text-zinc-500">.csv, .xlsx, .xls, .xlsm</p>
      </div>

      {error && (
        <p className="mt-4 rounded-md bg-amber-50 px-4 py-2 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
          {error}
        </p>
      )}

      {files.length > 0 && (
        <ul className="mt-6 divide-y divide-zinc-200 rounded-lg border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-900">
          {files.map((f) => (
            <li key={f.name} className="flex items-center justify-between px-4 py-2.5 text-sm">
              <span className="truncate">{f.name}</span>
              <div className="flex items-center gap-3 shrink-0">
                <span className="text-zinc-500">{(f.size / 1024).toFixed(1)} KB</span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    removeFile(f.name);
                  }}
                  className="text-zinc-400 hover:text-red-600"
                  aria-label={`Remove ${f.name}`}
                >
                  &times;
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <button
        onClick={handleSubmit}
        disabled={files.length === 0 || busy}
        className="mt-6 w-full rounded-lg bg-zinc-900 px-5 py-3 font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
      >
        {busy ? "Matching headers & units…" : `Preview merge of ${files.length} file${files.length === 1 ? "" : "s"}`}
      </button>
    </div>
  );
}
