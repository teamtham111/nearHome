"use client";

import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { startTransition, useEffect, useState } from "react";
import { Car, ClipboardPaste, Layers, ReceiptText, Repeat, GraduationCap, SlidersHorizontal, TrainFront, ListChecks, Globe } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { createSession } from "@/lib/api";
import { InlineDemo } from "@/components/inline-demo";

export default function HomePage() {
  const router = useRouter();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: createSession,
    onMutate: () => setCreateError(null),
    onSuccess: (data) => {
      setSessionId(data.session_id);
      if (new URLSearchParams(window.location.search).get("demo") !== "1" && document.body.dataset.demoActive !== "true") {
        localStorage.setItem("nearhome_session_id", data.session_id);
      }
      router.push(`/session/${data.session_id}`);
    },
    onError: (error) => setCreateError(error instanceof Error ? error.message : "Could not create a comparison."),
  });

  useEffect(() => {
    const existing = localStorage.getItem("nearhome_session_id");
    if (existing) startTransition(() => setSessionId(existing));
  }, []);

  return (
    <div className="nh-landing">
      <section className="nh-landing-shell nh-landing-hero">
        <div className="nh-landing-hero-grid">
          <div className="nh-landing-copy">
            <p className="nh-landing-eyebrow">A FIELD GUIDE FOR YOUR HDB SHORTLIST</p>
            <h1>
              A home is more
              <span>than its asking price.</span>
            </h1>
            <p className="nh-landing-hero-copy">
              Compare HDB flats by what truly matters — your route, room and lease, so you can find a home that fits your life, not just your budget.
            </p>
            <div className="nh-landing-actions">
              <button id="start-comparison" type="button" data-demo-id="start-comparison" className="nh-landing-button nh-landing-button-primary" onClick={() => create.mutate()} disabled={create.isPending}>
                {create.isPending ? "Preparing your shortlist…" : "Start a comparison"} <span aria-hidden="true">↗</span>
              </button>
              <a className="nh-landing-button nh-landing-button-secondary" href="#why-nearhome">Why NearHome <span aria-hidden="true">↘</span></a>
            </div>
            <p className="nh-landing-microcopy">Bring 2-5 flats you already have in mind.</p>
            {sessionId && <p className="nh-landing-session"><Link href={`/session/${sessionId}`}>Continue your previous comparison →</Link></p>}
            {createError && <p className="nh-landing-error" role="alert">{createError}</p>}
          </div>
          <figure className="nh-landing-hero-art">
            <Image
              src="/nearhome-hero-artwork-v2.png"
              alt="HDB homes among trees, with Singapore's skyline beyond"
              fill
              priority
              sizes="(max-width: 850px) 100vw, 48vw"
            />
          </figure>
        </div>
      </section>

      <section className="nh-landing-shell nh-landing-why" id="why-nearhome">
        <p className="nh-landing-eyebrow nh-landing-eyebrow-left">WHY NEARHOME</p>
        <div className="nh-landing-heading-row">
          <h2>Less checking across tabs.<br />More of the picture.</h2>
          <p>Paste the listings you already have. NearHome does the checking and shows what each flat means for your life.</p>
        </div>

        <div className="nh-why-contrast">
          <div className="nh-why-diy">
            <p className="nh-why-label">COMPARING IT YOURSELF</p>
            <ul>
              <li><Globe size={16} aria-hidden="true" /> A transport map in one tab</li>
              <li><Globe size={16} aria-hidden="true" /> Recent resale prices in another</li>
              <li><Globe size={16} aria-hidden="true" /> School maps and road routes elsewhere</li>
              <li><Repeat size={16} aria-hidden="true" /> Then repeat for every flat</li>
            </ul>
          </div>
          <div className="nh-why-with">
            <p className="nh-why-label nh-why-label-teal">WITH NEARHOME</p>
            <ul>
              <li><ClipboardPaste size={16} aria-hidden="true" /> Paste each listing</li>
              <li><SlidersHorizontal size={16} aria-hidden="true" /> Rank what matters to you</li>
              <li><Layers size={16} aria-hidden="true" /> Get one side-by-side comparison</li>
              <li><ListChecks size={16} aria-hidden="true" /> See the evidence behind every result</li>
            </ul>
          </div>
        </div>

        <p className="nh-why-subhead">WHAT YOU GET FOR EACH FLAT <span>Sample results for an illustrative flat</span></p>
        <div className="nh-why-cards">
          <article className="nh-why-card">
            <h3><TrainFront size={20} aria-hidden="true" /> Public transport</h3>
            <p>How well can you get around without a car?</p>
            <dl>
              <div><dt>Walk to nearest MRT</dt><dd>8 min</dd></div>
              <div><dt>Direct bus corridors</dt><dd>8</dd></div>
              <div><dt>One-transfer corridors</dt><dd>14</dd></div>
            </dl>
          </article>
          <article className="nh-why-card">
            <h3><Car size={20} aria-hidden="true" /> Driving</h3>
            <p>How easily can you get onto the major roads?</p>
            <dl>
              <div><dt>Drive to the PIE</dt><dd>8 min at peak</dd></div>
              <div><dt>Extra time versus off-peak</dt><dd>+3 min</dd></div>
              <div><dt>Home carpark</dt><dd>4 min walk</dd></div>
            </dl>
          </article>
          <article className="nh-why-card">
            <h3><ReceiptText size={20} aria-hidden="true" /> Fair price</h3>
            <p>Is the asking price in line with recent sales?</p>
            <dl>
              <div><dt>Asking price</dt><dd>$838,000</dd></div>
              <div><dt>Average of 5 nearby sales</dt><dd>$832,600</dd></div>
              <div><dt>Difference</dt><dd>+0.6%</dd></div>
            </dl>
          </article>
          <article className="nh-why-card">
            <h3><GraduationCap size={20} aria-hidden="true" /> Schools</h3>
            <p>How close are the schools you care about?</p>
            <dl>
              <div><dt>Schools within 1 km</dt><dd>1</dd></div>
              <div><dt>Schools within 2 km</dt><dd>5</dd></div>
              <div><dt>Ang Mo Kio Primary</dt><dd className="nh-why-pill">1.3 km · within 2 km</dd></div>
            </dl>
          </article>
        </div>

        <div className="nh-why-principles">
          <div><h3>Built on your priorities</h3><p>You rank what matters. The comparison follows your order.</p></div>
          <div><h3>Fixed rules, not AI scores</h3><p>Every result comes from published rules and real data.</p></div>
          <div><h3>Gaps shown, never guessed</h3><p>Missing data is marked unavailable instead of filled in.</p></div>
        </div>

        <div className="nh-why-cta">
          <button type="button" className="nh-landing-button nh-landing-button-primary" onClick={() => create.mutate()} disabled={create.isPending}>
            {create.isPending ? "Preparing your shortlist…" : "Start a comparison"} <span aria-hidden="true">↗</span>
          </button>
          <Link href="/how-flats-are-evaluated">See exactly how each result is calculated →</Link>
        </div>
      </section>

      <InlineDemo />

      <footer className="nh-landing-shell nh-landing-footer">
        <Link className="nh-landing-brand" href="/">nearhome<span>.</span></Link>
        <p>Compare the flat. Understand the trade-offs.</p>
      </footer>
    </div>
  );
}
