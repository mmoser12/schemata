import { Suspense } from "react";
import ReviewClient from "./ReviewClient";

// Entirely client-rendered (reads sessionStorage for the plan handed off from
// the upload page), so it's wrapped in Suspense rather than statically
// prerendered -- there's nothing to prerender until the browser runs.
export default function ReviewPage() {
  return (
    <Suspense fallback={<div className="mx-auto max-w-5xl px-6 py-10 text-zinc-500">Loading…</div>}>
      <ReviewClient />
    </Suspense>
  );
}
