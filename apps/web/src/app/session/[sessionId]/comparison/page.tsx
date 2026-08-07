"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { ComparisonView } from "@/components/comparison-view";
import { EnrichmentProgress } from "@/components/enrichment-progress";
import { WorkflowStepper } from "@/components/workflow-stepper";
import { getComparison, getSession, startEnrichment } from "@/lib/api";

type TerminalEnrichmentStatus = "completed" | "failed" | "cancelled";

export default function SessionComparisonPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;
  const router = useRouter();
  const searchParams = useSearchParams();
  const qc = useQueryClient();
  const runRequested = searchParams.get("run") === "1";
  const enrichmentStorageKey = `nearhome:enrichment-job:${sessionId}`;
  const autoRunTriggered = useRef(false);
  const [activeEnrichmentJobId, setActiveEnrichmentJobId] = useState<string | null>(null);
  const [comparisonReady, setComparisonReady] = useState(!runRequested);

  const sessionQuery = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => getSession(sessionId),
  });
  const comparisonQuery = useQuery({
    queryKey: ["comparison", sessionId],
    queryFn: ({ signal }) => getComparison(sessionId, signal),
    enabled: Boolean(sessionId),
  });

  const enrich = useMutation({
    mutationFn: () => startEnrichment(sessionId),
    onSuccess: (result) => {
      setComparisonReady(false);
      window.sessionStorage.setItem(enrichmentStorageKey, result.job_id);
      setActiveEnrichmentJobId(result.job_id);
    },
    onError: () => setComparisonReady(true),
  });

  useEffect(() => {
    const savedJobId = window.sessionStorage.getItem(enrichmentStorageKey);
    if (savedJobId) {
      setComparisonReady(false);
      setActiveEnrichmentJobId(savedJobId);
    }
  }, [enrichmentStorageKey]);

  useEffect(() => {
    if (
      !runRequested ||
      autoRunTriggered.current ||
      !sessionQuery.data ||
      sessionQuery.data.listing_count < 2
    ) {
      return;
    }
    autoRunTriggered.current = true;
    setComparisonReady(false);
    enrich.mutate();
    router.replace(`/session/${sessionId}/comparison`, { scroll: false });
  }, [enrich, router, runRequested, sessionId, sessionQuery.data]);

  const handleEnrichmentTerminal = useCallback(
    (jobStatus: TerminalEnrichmentStatus, jobId: string) => {
      window.sessionStorage.removeItem(enrichmentStorageKey);
      setActiveEnrichmentJobId(null);
      void Promise.all([
        qc.refetchQueries({ queryKey: ["comparison", sessionId] }),
        qc.refetchQueries({ queryKey: ["session", sessionId] }),
      ]).finally(() => setComparisonReady(true));
    },
    [enrichmentStorageKey, qc, sessionId],
  );

  if (sessionQuery.isPending) {
    return <div className="nh-card text-sm text-slate-600" role="status">Loading comparison…</div>;
  }

  if (sessionQuery.isError || !sessionQuery.data) {
    return (
      <div className="space-y-3">
        <Link href={`/session/${sessionId}`} className="text-sm text-teal-700 hover:underline">← Back to workspace</Link>
        <p className="text-sm text-red-600" role="alert">
          {String(sessionQuery.error?.message ?? "Unable to load comparison")}
        </p>
      </div>
    );
  }

  const session = sessionQuery.data;
  const isEnriching = enrich.isPending || Boolean(activeEnrichmentJobId);

  return (
    <div className="nh-workflow-grid space-y-7 py-8 sm:py-10">
      <div className="space-y-4">
        <Link href={`/session/${sessionId}`} className="text-sm text-teal-700 hover:underline">← Back to workspace</Link>
        <div className="mt-1">
          <p className="nh-section-kicker">Step 3 of 3 · Compare results</p>
          <h2 className="mt-1 text-3xl font-bold tracking-tight text-blue-950">
            {isEnriching ? "Enriching your listings" : comparisonReady ? "Your comparison is ready" : "Preparing your comparison"}
          </h2>
          <p className="mt-1 text-sm text-slate-600">
            {isEnriching
              ? "We are gathering the evidence needed for a clear, like-for-like comparison."
              : "Review the available factor results, trade-offs and supporting evidence across your shortlisted flats."}
          </p>
        </div>
        <WorkflowStepper current="compare" profileSaved={Boolean(session.profile_saved)} listingCount={session.listing_count} sessionId={sessionId} />
      </div>

      {session.listing_count < 2 ? (
        <section className="nh-card">
          <p className="text-sm text-slate-600">Add one more confirmed listing to see the comparison.</p>
        </section>
      ) : isEnriching ? (
        <>
          {enrich.isPending && !activeEnrichmentJobId ? (
            <section className="nh-card text-sm text-slate-600" role="status">Starting analysis…</section>
          ) : null}
          {activeEnrichmentJobId ? (
            <EnrichmentProgress
              sessionId={sessionId}
              jobId={activeEnrichmentJobId}
              listings={session.listings}
              onTerminal={handleEnrichmentTerminal}
            />
          ) : null}
        </>
      ) : enrich.isError ? (
        <section className="nh-card space-y-3">
          <p className="text-sm text-red-600" role="alert">{String(enrich.error.message)}</p>
          <button type="button" className="nh-primary" onClick={() => enrich.mutate()}>
            Try enrichment again
          </button>
        </section>
      ) : comparisonQuery.isPending ? (
        <div className="nh-card text-sm text-slate-600" role="status">Preparing comparison results…</div>
      ) : comparisonQuery.isError || !comparisonQuery.data ? (
        <div className="nh-card text-sm text-red-600" role="alert">
          {String(comparisonQuery.error?.message ?? "Unable to load comparison")}
        </div>
      ) : (
        <ComparisonView data={comparisonQuery.data} session={session} />
      )}
    </div>
  );
}
