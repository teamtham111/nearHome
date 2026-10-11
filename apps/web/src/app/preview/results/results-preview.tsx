"use client";

import { useMemo, useState } from "react";

import { ComparisonView } from "@/components/comparison-view";
import { RATING_PROMPT_STORAGE_KEY, RatingPrompt } from "@/components/rating-prompt";
import { buildResultsPreview } from "@/demo/fixtures";

type TransportMode = "MAINLY_PUBLIC_TRANSPORT" | "MAINLY_DRIVING" | "BOTH";

const fakeSave = async (_sessionId: string, body: { rating: number; comment?: string }) => {
  console.info("[results preview] rating not sent anywhere:", body);
  return { rating: body.rating, comment: body.comment ?? null };
};

export function ResultsPreview() {
  const [transportMode, setTransportMode] = useState<TransportMode>("BOTH");
  const [promptKey, setPromptKey] = useState(0);
  const { comparison, session } = useMemo(() => buildResultsPreview(transportMode), [transportMode]);
  // Render as a real (non-demo) comparison so sections start collapsed, as users see them.
  const data = useMemo(() => ({ ...comparison, demo_mode: false }), [comparison]);

  function showRatingPrompt() {
    try {
      window.localStorage.removeItem(RATING_PROMPT_STORAGE_KEY);
    } catch {
      /* ignore */
    }
    setPromptKey((key) => key + 1);
  }

  return (
    <div className="nh-workflow-grid space-y-7 py-8 sm:py-10">
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-dashed border-amber-400 bg-amber-50 p-3 text-sm text-amber-900">
        <span className="font-semibold">Results preview · sample data</span>
        <label className="flex items-center gap-2">
          Transport mode
          <select
            className="rounded border border-amber-300 bg-white px-2 py-1"
            value={transportMode}
            onChange={(event) => setTransportMode(event.target.value as TransportMode)}
          >
            <option value="BOTH">Both</option>
            <option value="MAINLY_PUBLIC_TRANSPORT">Mainly public transport</option>
            <option value="MAINLY_DRIVING">Mainly driving</option>
          </select>
        </label>
        <button type="button" className="rounded border border-amber-400 bg-white px-2 py-1 hover:bg-amber-100" onClick={showRatingPrompt}>
          Show rating popup
        </button>
      </div>

      <div>
        <p className="nh-section-kicker">Step 3 of 3 · Compare results</p>
        <h2 className="mt-1 text-3xl font-bold tracking-tight text-blue-950">Your comparison is ready</h2>
        <p className="mt-1 text-sm text-slate-600">Review the available factor results, trade-offs and supporting evidence across your shortlisted flats.</p>
      </div>

      <ComparisonView data={data} session={session} />
      {promptKey > 0 && <RatingPrompt key={promptKey} sessionId="preview" delayMs={300} onSave={fakeSave} />}
    </div>
  );
}
