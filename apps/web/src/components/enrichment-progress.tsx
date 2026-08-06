"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Circle, Clock3, LoaderCircle, ShieldCheck, TriangleAlert } from "lucide-react";
import { getEnrichmentJob, getEnrichmentStatus } from "@/lib/api";
import type { SessionListing } from "@/lib/api";
import { deriveEnrichmentProgress, type EnrichmentProgressSnapshot, type StageState } from "@/lib/enrichment-progress";

export function EnrichmentProgress({
  sessionId,
  jobId,
  onTerminal,
  listings = [],
}: {
  sessionId: string;
  jobId: string;
  onTerminal?: (status: "completed" | "failed" | "cancelled", jobId: string) => void;
  listings?: SessionListing[];
}) {
  const [snapshot, setSnapshot] = useState<EnrichmentProgressSnapshot>({
    jobStatus: "queued",
    progressStage: "queued",
    attempts: 0,
    runs: [],
  });
  const [pollingIssue, setPollingIssue] = useState(false);
  const terminalNotified = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let delayMs = 1_000;
    let timeout: number | undefined;
    const update = async () => {
      try {
        const [job, stageStatus] = await Promise.all([
          getEnrichmentJob(sessionId, jobId),
          getEnrichmentStatus(sessionId),
        ]);
        if (cancelled) return;
        setPollingIssue(false);
        setSnapshot({
          jobStatus: job.status,
          progressStage: job.progress_stage,
          attempts: job.attempts,
          errorMessage: job.error_message,
          runs: stageStatus.runs,
        });
        if (["completed", "failed", "cancelled"].includes(job.status)) {
          if (!terminalNotified.current) {
            terminalNotified.current = true;
            onTerminal?.(job.status as "completed" | "failed" | "cancelled", jobId);
          }
          return;
        }
        delayMs = Math.min(8_000, Math.round(delayMs * 1.5));
      } catch {
        setPollingIssue(true);
        delayMs = Math.min(8_000, Math.round(delayMs * 1.5));
      }
      if (!cancelled) timeout = window.setTimeout(() => void update(), delayMs);
    };
    void update();
    return () => {
      cancelled = true;
      if (timeout) window.clearTimeout(timeout);
    };
  }, [jobId, onTerminal, sessionId]);

  const progress = deriveEnrichmentProgress(snapshot);
  const countLabel = `${progress.completedCount} of ${progress.totalChecks} checks completed`;

  const stageIcon = (state: StageState) => {
    if (state === "succeeded") return <span className="grid h-6 w-6 place-items-center rounded-full bg-emerald-600 text-white"><Check size={15} /></span>;
    if (state === "running") return <span className="grid h-6 w-6 place-items-center rounded-full border-2 border-blue-600 text-blue-700"><LoaderCircle className="animate-spin" size={15} /></span>;
    if (state === "failed") return <span className="grid h-6 w-6 place-items-center rounded-full bg-amber-100 text-amber-700"><TriangleAlert size={15} /></span>;
    return <span className="grid h-6 w-6 place-items-center rounded-full border border-slate-300 bg-white text-slate-400"><Circle size={9} fill="currentColor" /></span>;
  };

  return <section className="mt-6" aria-label="Listing enrichment progress">
    <div className="grid gap-6 lg:grid-cols-[1.7fr_0.75fr]">
      <div className="rounded-2xl border border-blue-100 bg-white p-5 shadow-sm sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="nh-section-kicker">Step 3 of 3 · Analysis</p><h3 className="mt-1 text-2xl font-bold tracking-tight text-blue-950">{progress.successful ? "Your comparison is ready" : "Enriching listings"}</h3><p className="mt-1 text-sm text-slate-600">{progress.terminal ? "Available results have been saved to your comparison." : "Typically takes 1–2 minutes"}</p></div><span className="text-4xl font-bold tracking-tight tabular-nums text-blue-700">{progress.percent}%</span></div>
        <div className="mt-6 h-3 overflow-hidden rounded-full bg-blue-100" role="progressbar" aria-label="Listing enrichment progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.percent} aria-valuetext={`${progress.percent}% — ${progress.message}`}><div className="h-full rounded-full bg-blue-600 transition-[width] duration-700 ease-out" style={{ width: `${progress.percent}%` }} /></div>
        {!progress.successful && !progress.terminal && <p className="mt-3 text-sm font-medium text-slate-700">{countLabel}</p>}
        <p className={`mt-2 text-sm font-medium ${progress.terminal && !progress.successful ? "text-red-700" : "text-blue-700"}`} aria-live="polite">{pollingIssue && !progress.terminal ? "Refreshing enrichment progress…" : progress.message}</p>
        {progress.unavailableCount > 0 && !progress.successful && <p className="mt-1 text-xs text-slate-500">{progress.unavailableCount} check{progress.unavailableCount === 1 ? "" : "s"} could not be requested or is unavailable.</p>}
        {progress.errorMessage && !progress.terminal && <p className="mt-2 text-xs text-amber-800" role="status">{progress.errorMessage}</p>}
        {snapshot.attempts > 1 && !progress.terminal && <p className="mt-2 text-xs text-slate-500">Retry attempt {snapshot.attempts}</p>}
        <ul className="mt-6 divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200" aria-label="Enrichment stages">{progress.stages.map((stage) => <li key={stage.type} className="flex items-center gap-3 px-3 py-3"><span aria-hidden="true">{stageIcon(stage.state)}</span><div className="min-w-0 flex-1"><p className="text-sm font-semibold text-slate-900">{stage.label}</p><p className="truncate text-xs text-slate-500">{stage.action}</p></div><span className={`text-xs font-medium ${stage.state === "succeeded" ? "text-emerald-700" : stage.state === "running" ? "text-blue-700" : stage.state === "failed" ? "text-amber-700" : "text-slate-500"}`}>{stage.state === "succeeded" ? "Completed" : stage.state === "running" ? "In progress" : stage.state === "failed" ? "Unavailable" : stage.state === "not_requested" ? "Not requested" : "Pending"}</span></li>)}</ul>
      </div>
      <aside className="rounded-2xl border border-blue-100 bg-blue-50/70 p-5 sm:p-6"><ShieldCheck className="text-blue-700" size={30} /><h3 className="mt-5 text-xl font-bold tracking-tight text-blue-950">You can keep this tab open while we prepare your comparison.</h3><p className="mt-3 text-sm leading-6 text-slate-600">Progress is saved on the job, and the comparison updates when available data has been processed.</p><div className="mt-6 space-y-4 text-sm text-slate-700"><p className="flex gap-2"><Clock3 className="mt-0.5 shrink-0 text-blue-700" size={17} />Typical completion time: 1–2 minutes</p><p className="flex gap-2"><ShieldCheck className="mt-0.5 shrink-0 text-blue-700" size={17} />Results show provider limitations when a source cannot be used.</p></div></aside>
    </div>
    {listings.length > 0 && <div className="mt-6 rounded-2xl border border-blue-100 bg-white p-5"><h3 className="font-semibold text-blue-950">Analysing {listings.length} flat{listings.length === 1 ? "" : "s"}</h3><div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{listings.map((listing) => <article key={listing.listing_id} className="rounded-xl border border-slate-200 bg-slate-50/60 p-3"><p className="font-semibold text-slate-900">{listing.display_name}</p><p className="mt-1 text-sm text-slate-600">{listing.address}</p><p className="mt-2 text-xs text-slate-500">{listing.flat_type} · {listing.floor_area_sqm} sqm</p></article>)}</div></div>}
  </section>;
}
