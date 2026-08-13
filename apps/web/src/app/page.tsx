"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { startTransition, useEffect, useState } from "react";
import { createSession } from "@/lib/api";

const assessments = [
  {
    title: "Public transport strength",
    supportingLine: "Go beyond the nearest MRT station or bus stop.",
    description: "NearHome evaluates walking access, bus-route coverage, MRT network reach and alternative travel options to assess the strength of the wider public transport network around each flat.",
    outcome: "This helps distinguish between a flat that is merely close to one transport node and one that offers stronger overall connectivity.",
  },
  {
    title: "Driving connectivity",
    supportingLine: "Go beyond the distance to the nearest major road.",
    description: "NearHome considers major-road access, traffic-aware journey times, route connectivity and nearby HDB parking availability to assess how convenient each location may be for regular drivers.",
    outcome: "This provides a broader picture of driving convenience than a list of nearby roads or estimated journey times alone.",
  },
  {
    title: "Fair-price estimate",
    supportingLine: "Go beyond viewing recent transactions individually.",
    description: "NearHome analyses relevant HDB resale data together with property characteristics such as location, flat type, floor area and remaining lease to produce an estimated value range.",
    outcome: "The estimate is compared directly with the listing’s asking price, helping you identify whether the flat appears reasonably priced based on the available market evidence.",
  },
  {
    title: "School access",
    supportingLine: "Go beyond a list of nearby schools.",
    description: "NearHome calculates property-to-school distances, organises schools into commonly used distance bands and compares school access consistently across your shortlisted flats.",
    outcome: "Location-based results are clearly separated from admission eligibility or guaranteed placement.",
  },
];

export default function HomePage() {
  const router = useRouter();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: createSession,
    onMutate: () => setCreateError(null),
    onSuccess: (data) => {
      setSessionId(data.session_id);
      localStorage.setItem("nearhome_session_id", data.session_id);
      router.push(`/session/${data.session_id}`);
    },
    onError: (error) => setCreateError(error instanceof Error ? error.message : "Could not create a comparison."),
  });

  useEffect(() => {
    const existing = localStorage.getItem("nearhome_session_id");
    if (existing) startTransition(() => setSessionId(existing));
  }, []);

  return (
    <div className="space-y-12">
      <section className="nh-card">
        <p className="nh-section-kicker">Evidence-led shortlist review</p>
        <h2 className="mt-1 text-3xl font-semibold tracking-tight text-slate-900">Compare your shortlisted HDB flats</h2>
        <p className="mt-2 max-w-2xl text-slate-600">NearHome helps you compare 2–5 actual listings with explainable evidence. It is not a listing search portal — bring flats you have already shortlisted.</p>
        <div className="mt-6 flex flex-wrap gap-3">
          <button type="button" className="nh-primary" onClick={() => create.mutate()} disabled={create.isPending}>{create.isPending ? "Creating…" : "Start new comparison"}</button>
          {sessionId && <Link href={`/session/${sessionId}`} className="nh-secondary">Continue previous session</Link>}
        </div>
        {create.isSuccess && create.data && <p className="mt-4 text-sm text-teal-700">Session created. <Link href={`/session/${create.data.session_id}`} className="underline">Open comparison workspace →</Link></p>}
        {createError && <p className="mt-4 text-sm text-red-700" role="alert">{createError}</p>}
      </section>

      <section aria-label="How NearHome works" className="grid gap-4 md:grid-cols-3">
        {[
          ["1. Set context", "Budget, transport, priorities and important locations"],
          ["2. Add flats", "Manual entry or Smart Paste, then review the extracted details"],
          ["3. Compare", "See the recommendation, scores and supporting evidence"],
        ].map(([title, desc]) => <div key={title} className="rounded-xl border border-slate-200 bg-white p-4"><p className="text-sm font-semibold text-slate-900">{title}</p><p className="mt-1 text-sm text-slate-600">{desc}</p></div>)}
      </section>


      <section aria-labelledby="how-nearhome-helps" className="pt-2 sm:pt-4">
        <div className="max-w-3xl">
          <p className="nh-section-kicker">HOW NEARHOME HELPS</p>
          <h2 id="how-nearhome-helps" className="mt-2 text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">From property search to property decision</h2>
          <p className="mt-4 leading-7 text-slate-600">Traditional property portals are designed to help you discover available homes. NearHome is designed for the next step: deciding which of your shortlisted flats fits you best.</p>
          <p className="mt-4 leading-7 text-slate-600">Instead of presenting isolated facts such as the nearest MRT station, nearby schools or recent transactions, NearHome processes property, transport and location data through purpose-built models. The results are compared consistently across every flat and organised around the factors that matter to you.</p>
        </div>

        <div className="mt-8 grid items-stretch gap-4 md:grid-cols-2">
          {assessments.map((assessment) => <article key={assessment.title} className="flex h-full flex-col rounded-xl border border-slate-200 bg-white p-5"><h3 className="text-lg font-semibold text-slate-900">{assessment.title}</h3><p className="mt-2 text-sm font-medium leading-6 text-teal-700">{assessment.supportingLine}</p><p className="mt-3 text-sm leading-6 text-slate-600">{assessment.description}</p><div className="mt-5 rounded-lg bg-slate-50 p-3"><p className="text-xs font-semibold uppercase tracking-wide text-slate-700">Why it matters</p><p className="mt-1 text-sm leading-6 text-slate-600">{assessment.outcome}</p></div></article>)}
        </div>
      </section>

      <section aria-labelledby="comparison-around-you" className="border-t border-slate-200 pt-10">
        <p className="nh-section-kicker">COMPARED AROUND YOUR PRIORITIES</p>
        <h2 id="comparison-around-you" className="mt-2 text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">One comparison built around you</h2>
        <p className="mt-4 max-w-3xl leading-7 text-slate-600">NearHome brings these assessments together in one place and compares your shortlisted flats according to the factors you selected, such as affordability, space, transport and proximity to important locations.</p>
        <div className="mt-6 grid gap-3 sm:grid-cols-2">
          {["Where each flat performs strongly", "Which flat has the advantage for each priority", "What trade-offs you would be accepting", "The evidence supporting every assessment"].map((item) => <p key={item} className="flex items-start gap-2 rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700"><span className="mt-0.5 text-teal-700" aria-hidden="true">✓</span>{item}</p>)}
        </div>
        <p className="mt-6 text-sm leading-6 text-slate-600">No unexplained overall ranking. NearHome shows the priorities, evidence and trade-offs behind each comparison.</p>
      </section>
    </div>
  );
}
