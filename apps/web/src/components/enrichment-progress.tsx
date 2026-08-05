"use client";

import { useEffect, useState } from "react";
import { getEnrichmentStatus } from "@/lib/api";

type ProgressState = { complete: number; total: number };

const terminalStatuses = new Set(["SUCCEEDED", "FAILED", "UNAVAILABLE", "SKIPPED"]);

export function EnrichmentProgress({ sessionId }: { sessionId: string }) {
  const [progress, setProgress] = useState<ProgressState>({ complete: 0, total: 0 });

  useEffect(() => {
    let cancelled = false;
    const update = async () => {
      try {
        const { runs } = await getEnrichmentStatus(sessionId);
        if (cancelled || runs.length === 0) return;
        setProgress({
          complete: runs.filter((run) => terminalStatuses.has(run.status)).length,
          total: runs.length,
        });
      } catch {
        // The parent mutation owns the visible error state. Keep the progress
        // indicator in its honest "starting" state if status is unavailable.
      }
    };
    void update();
    const interval = window.setInterval(() => void update(), 1_000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [sessionId]);

  const hasTasks = progress.total > 0;
  const percent = hasTasks
    ? Math.min(95, Math.max(12, Math.round((progress.complete / progress.total) * 100)))
    : 0;
  const label = progress.total > 0
    ? progress.complete === progress.total
      ? "Finalising results…"
      : `Checking ${progress.complete} of ${progress.total} enrichment tasks…`
    : "Starting enrichment…";

  return (
    <div className="mt-3 max-w-xl" role="status" aria-live="polite">
      <div className="flex items-center justify-between gap-3 text-xs text-slate-600">
        <span>{label}</span>
        {progress.total > 0 && <span>{percent}%</span>}
      </div>
      <div className="mt-1 h-2 overflow-hidden rounded-full bg-slate-200" role="progressbar" aria-label="Enrichment progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={hasTasks ? percent : undefined} aria-valuetext={label}>
        <div className={`h-full rounded-full bg-teal-600 transition-all duration-500 ${hasTasks ? "" : "w-1/3 animate-pulse"}`} style={hasTasks ? { width: `${percent}%` } : undefined} />
      </div>
    </div>
  );
}
