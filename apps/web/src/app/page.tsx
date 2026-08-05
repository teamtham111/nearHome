"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { createSession } from "@/lib/api";

export default function HomePage() {
  const router = useRouter();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: createSession,
    onMutate: () => {
      setCreateError(null);
    },
    onSuccess: (data) => {
      setSessionId(data.session_id);
      if (typeof window !== "undefined") {
        localStorage.setItem("nearhome_session_id", data.session_id);
      }
      router.push(`/session/${data.session_id}`);
    },
    onError: (error) => {
      setCreateError(error instanceof Error ? error.message : "Could not create a comparison.");
    },
  });

  useEffect(() => {
    const existing = localStorage.getItem("nearhome_session_id");
    if (existing) setSessionId(existing);
  }, []);

  return (
    <div className="space-y-8">
      <section className="nh-card">
        <p className="nh-section-kicker">Evidence-led shortlist review</p>
        <h2 className="mt-1 text-3xl font-semibold tracking-tight text-slate-900">
          Compare your shortlisted HDB flats
        </h2>
        <p className="mt-2 max-w-2xl text-slate-600">
          NearHome helps you compare 2–5 actual listings with explainable evidence. It is not a
          listing search portal — bring flats you have already shortlisted.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <button
            type="button"
            className="nh-primary"
            onClick={() => create.mutate()}
            disabled={create.isPending}
          >
            {create.isPending ? "Creating…" : "Start new comparison"}
          </button>
          {sessionId && (
            <Link
              href={`/session/${sessionId}`}
              className="nh-secondary"
            >
              Continue previous session
            </Link>
          )}
        </div>
        {create.isSuccess && create.data && (
          <p className="mt-4 text-sm text-teal-700">
            Session created.{" "}
            <Link href={`/session/${create.data.session_id}`} className="underline">
              Open comparison workspace →
            </Link>
          </p>
        )}
        {createError && (
          <p className="mt-4 text-sm text-red-700" role="alert">
            {createError}
          </p>
        )}
      </section>

      <section aria-label="How NearHome works" className="grid gap-4 md:grid-cols-3">
        {[
          ["1. Set context", "Budget, transport, priorities and important locations"],
          ["2. Add flats", "Manual entry or Smart Paste, then review the extracted details"],
          ["3. Compare", "See the recommendation, scores and supporting evidence"],
        ].map(([title, desc]) => (
          <div key={title} className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-sm font-semibold text-slate-900">{title}</p>
            <p className="mt-1 text-sm text-slate-600">{desc}</p>
          </div>
        ))}
      </section>
    </div>
  );
}
