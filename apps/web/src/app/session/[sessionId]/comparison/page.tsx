"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ComparisonView } from "@/components/comparison-view";
import { WorkflowStepper } from "@/components/workflow-stepper";
import { EnrichmentProgress } from "@/components/enrichment-progress";
import { getComparison, getSession, startEnrichment } from "@/lib/api";
import { waitForEnrichmentComplete } from "@/lib/wait-for-enrichment";

export default function SessionComparisonPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;
  const qc = useQueryClient();

  const sessionQuery = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => getSession(sessionId),
  });

  const comparisonQuery = useQuery({
    queryKey: ["comparison", sessionId],
    queryFn: ({ signal }) => getComparison(sessionId, signal),
    enabled: Boolean(sessionId),
    refetchInterval: 8000,
  });

  const enrich = useMutation({
    mutationFn: async () => {
      const result = await startEnrichment(sessionId);
      if (result.mode === "queued") {
        await waitForEnrichmentComplete(sessionId);
      }
      return result;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["comparison", sessionId] });
      qc.invalidateQueries({ queryKey: ["session", sessionId] });
    },
  });

  if (sessionQuery.isPending || comparisonQuery.isPending) {
    return <div className="nh-card text-sm text-slate-600" role="status">Loading comparison…</div>;
  }

  if (sessionQuery.isError || comparisonQuery.isError) {
    const error = sessionQuery.error ?? comparisonQuery.error;
    return (
      <div className="space-y-3">
        <Link href={`/session/${sessionId}`} className="text-sm text-teal-700 hover:underline">
          ← Back to workspace
        </Link>
        <p className="text-sm text-red-600" role="alert">{String(error?.message ?? "Unable to load comparison")}</p>
      </div>
    );
  }

  const session = sessionQuery.data;
  const comparison = comparisonQuery.data;

  if (!session || !comparison) return null;

  return (
    <div className="nh-workflow-grid space-y-7">
      <div className="space-y-4">
        <Link href={`/session/${sessionId}`} className="text-sm text-teal-700 hover:underline">
          ← Back to workspace
        </Link>
        <div className="mt-1 flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="nh-section-kicker">Step 3 · Compare results</p>
            <h2 className="mt-1 text-2xl font-semibold tracking-tight">Your shortlist at a glance</h2>
            <p className="mt-1 text-sm text-slate-600">Start with the recommendation, then open the sections below to inspect the evidence.</p>
          </div>
          {session.listing_count >= 2 && (
            <button
              type="button"
              className="nh-primary"
              onClick={() => enrich.mutate()}
              disabled={enrich.isPending}
            >
              {enrich.isPending ? "Running enrichment…" : "Run enrichment"}
            </button>
          )}
        </div>
        {enrich.isError && (
          <p className="mt-2 text-sm text-red-600" role="alert">{String(enrich.error.message)}</p>
        )}
        {enrich.isPending && <EnrichmentProgress sessionId={sessionId} />}
        <WorkflowStepper current="compare" profileSaved={Boolean(session.profile_saved)} listingCount={session.listing_count} sessionId={sessionId} />
      </div>

      {session.listing_count < 2 ? (
        <section className="nh-card">
          <p className="text-sm text-slate-600">Add one more confirmed listing to see the comparison.</p>
        </section>
      ) : (
        <ComparisonView data={comparison} session={session} />
      )}
    </div>
  );
}
