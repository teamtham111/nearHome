"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Circle, Clock3, LoaderCircle, ShieldCheck, TriangleAlert } from "lucide-react";
import { getEnrichmentJob, getEnrichmentStatus } from "@/lib/api";
import type { SessionListing } from "@/lib/api";
import { deriveEnrichmentProgress, expectedStageTypes, type EnrichmentProgressSnapshot, type StageState } from "@/lib/enrichment-progress";

export function EnrichmentProgress({
  sessionId,
  jobId,
  onTerminal,
  listings = [],
  buyerProfile,
}: {
  sessionId: string;
  jobId: string;
  onTerminal?: (status: "completed" | "failed" | "cancelled", jobId: string) => void;
  listings?: SessionListing[];
  buyerProfile?: { main_transport_mode: string; schools_matter: boolean } | null;
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
    let delayMs = jobId === "demo-job" ? 180 : 1_000;
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
        delayMs = jobId === "demo-job" ? 180 : Math.min(8_000, Math.round(delayMs * 1.5));
      } catch {
        setPollingIssue(true);
        delayMs = jobId === "demo-job" ? 180 : Math.min(8_000, Math.round(delayMs * 1.5));
      }
      if (!cancelled) timeout = window.setTimeout(() => void update(), delayMs);
    };
    void update();
    return () => {
      cancelled = true;
      if (timeout) window.clearTimeout(timeout);
    };
  }, [jobId, onTerminal, sessionId]);

  const derived = deriveEnrichmentProgress(snapshot, {
    listingIds: listings.map((listing) => listing.listing_id),
    expectedTypes: buyerProfile ? expectedStageTypes(buyerProfile) : undefined,
  });
  // Never show less progress than already shown for this job, e.g. when a
  // retry resets the job's checks to queued.
  const [peak, setPeak] = useState({ jobId, percent: 0 });
  const shownPercent = peak.jobId === jobId ? Math.max(peak.percent, derived.percent) : derived.percent;
  if (peak.jobId !== jobId || shownPercent !== peak.percent) setPeak({ jobId, percent: shownPercent });
  const progress = { ...derived, percent: shownPercent };
  const countLabel = `${progress.completedCount} of ${progress.totalChecks} checks completed`;

  const stageIcon = (state: StageState) => {
    if (state === "succeeded") return <span className="nh-enrichment-icon nh-enrichment-icon-done"><Check size={15} /></span>;
    if (state === "running") return <span className="nh-enrichment-icon nh-enrichment-icon-running"><LoaderCircle className="animate-spin" size={15} /></span>;
    if (state === "failed") return <span className="nh-enrichment-icon nh-enrichment-icon-failed"><TriangleAlert size={15} /></span>;
    return <span className="grid h-6 w-6 place-items-center rounded-full border border-slate-300 bg-white text-slate-400"><Circle size={9} fill="currentColor" /></span>;
  };

  return <section data-demo-id="enrichment-progress" className="mt-6" aria-label="Listing enrichment progress">
    <div className="grid gap-6 lg:grid-cols-[1.7fr_0.75fr]">
      <div className="nh-card nh-enrichment-card">
        <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="nh-section-kicker">Step 3 of 3 · Analysis</p><h3 className="nh-enrichment-heading mt-1 text-3xl">{progress.successful ? "Your comparison is ready" : "Enriching listings"}</h3><p className="mt-1 text-sm text-slate-600">{progress.terminal ? "Available results have been saved to your comparison." : "Typically takes 1–2 minutes"}</p></div><span className="nh-enrichment-percent text-4xl font-semibold tabular-nums">{progress.percent}%</span></div>
        <div data-demo-id="enrichment-progress-bar" className="nh-enrichment-progress mt-6 h-3 overflow-hidden rounded-full" role="progressbar" aria-label="Listing enrichment progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.percent} aria-valuetext={`${progress.percent}% — ${progress.message}`}><div className="h-full rounded-full transition-[width] duration-700 ease-out" style={{ width: `${progress.percent}%` }} /></div>
        {!progress.successful && !progress.terminal && <p className="mt-3 text-sm font-medium text-slate-700">{countLabel}</p>}
        <p className={`nh-enrichment-status mt-2 text-sm font-medium ${progress.terminal && !progress.successful ? "is-error" : ""}`} aria-live="polite">{pollingIssue && !progress.terminal ? "Refreshing enrichment progress…" : progress.message}</p>
        {progress.unavailableCount > 0 && !progress.successful && <p className="mt-1 text-xs text-slate-500">{progress.unavailableCount} check{progress.unavailableCount === 1 ? "" : "s"} could not be requested or is unavailable.</p>}
        {progress.errorMessage && !progress.terminal && <p className="mt-2 text-xs text-amber-800" role="status">{progress.errorMessage}</p>}
        {snapshot.attempts > 1 && !progress.terminal && <p className="mt-2 text-xs text-slate-500">Retry attempt {snapshot.attempts}</p>}
        <ul className="nh-enrichment-stages mt-6 overflow-hidden rounded-xl border" aria-label="Enrichment stages">{progress.stages.map((stage) => <li key={stage.type} className="flex items-center gap-3 px-3 py-3"><span aria-hidden="true">{stageIcon(stage.state)}</span><div className="min-w-0 flex-1"><p className="text-sm font-semibold text-slate-900">{stage.label}</p><p className="truncate text-xs text-slate-500">{stage.action}</p></div><span className={`nh-enrichment-stage-status text-xs font-medium ${stage.state === "succeeded" ? "is-done" : stage.state === "running" ? "is-running" : stage.state === "failed" ? "is-failed" : ""}`}>{stage.state === "succeeded" ? "Completed" : stage.state === "running" ? "In progress" : stage.state === "failed" ? "Unavailable" : stage.state === "not_requested" ? "Not requested" : "Pending"}</span></li>)}</ul>
      </div>
      <aside className="nh-enrichment-aside nh-card"><ShieldCheck className="nh-enrichment-aside-icon" size={30} /><h3 className="nh-enrichment-heading mt-5 text-2xl">You can keep this tab open while we prepare your comparison.</h3><p className="mt-3 text-sm leading-6 text-slate-600">Progress is saved on the job, and the comparison updates when available data has been processed.</p><div className="mt-6 space-y-4 text-sm text-slate-700"><p className="flex gap-2"><Clock3 className="nh-enrichment-aside-icon mt-0.5 shrink-0" size={17} />Typical completion time: 1–2 minutes</p><p className="flex gap-2"><ShieldCheck className="nh-enrichment-aside-icon mt-0.5 shrink-0" size={17} />Results show provider limitations when a source cannot be used.</p></div></aside>
    </div>
    {listings.length > 0 && <div className="nh-card nh-enrichment-listings mt-6"><h3 className="nh-enrichment-heading text-xl">Analysing {listings.length} flat{listings.length === 1 ? "" : "s"}</h3><div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{listings.map((listing) => <article key={listing.listing_id} className="nh-enrichment-listing rounded-lg border p-3"><p className="font-semibold text-slate-900">{listing.display_name}</p><p className="mt-1 text-sm text-slate-600">{listing.address}</p><p className="mt-2 text-xs text-slate-500">{listing.flat_type} · {listing.floor_area_sqm} sqm</p></article>)}</div></div>}
  </section>;
}
