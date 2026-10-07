import Link from "next/link";
import { AlertTriangle, ArrowRight, Car, Check, Database, EyeOff, GraduationCap, ReceiptText, Ruler, TrainFront } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  evaluationGuides,
  methodology,
  notAssessed,
  pipelineSteps,
  priorityScoreInfo,
  sampleFlat,
  scoreKeyBands,
} from "@/lib/methodology";
import type { Methodology } from "@/lib/methodology";
import {
  FAIR_PRICE_CONFIDENCE_FACTOR,
  PRIORITY_WEIGHTS_BY_RANK,
  TIE_THRESHOLD,
  affordabilityScore,
  fairPriceScore,
  isTie,
  rescaledWeights,
  round1,
  round2,
  spaceScore,
  weightedAverage,
} from "@/lib/methodology-scoring";
import { ratingForScore } from "@/lib/public-transport";
import { MethodologyExplorer } from "@/components/methodology-explorer";
import { OverallFitFlow, PriorityCurve } from "@/components/methodology-diagrams";

const icons: Record<string, LucideIcon> = {
  transport: TrainFront,
  driving: Car,
  "fair-price": ReceiptText,
  schools: GraduationCap,
};

const principles = [
  { icon: Ruler, title: "Fixed rules", text: "Every result follows published rules. Never an AI-generated rating." },
  { icon: Database, title: "Real data", text: "LTA, HDB and MOE reference data, plus confirmed routing." },
  { icon: EyeOff, title: "No guessing", text: "Missing evidence is shown as unavailable, never filled in." },
];

const curveKinds = { affordability: "affordability", space: "space", lease: "lease", journey: "journey", "fair-price": "fair-price" } as const;

const fmt = (value: number, digits = 1) => value.toFixed(digits);
const ordinal = (rank: number) => ["1st", "2nd", "3rd"][rank - 1] ?? `${rank}th`;
const money = (value: number) => `$${value.toLocaleString("en-SG")}`;

function categoryRows(item: Methodology) {
  if (item.total.kind === "note") return [];
  const scores = item.total.scores;
  return item.components.map((component) => ({
    label: component.label,
    score: scores[component.id],
    pct: component.pct ?? 0,
  }));
}

function categoryScore(item: Methodology) {
  if (item.total.kind === "weighted") {
    const value = weightedAverage(categoryRows(item).map((row) => ({ score: row.score, weight: row.pct })));
    return value === null ? null : round1(value);
  }
  if (item.total.kind === "sum") return categoryRows(item).reduce((sum, row) => sum + row.score, 0);
  return null;
}

function RatingWord({ score }: { score: number }) {
  const word = ratingForScore(score);
  return word ? <span className="nh-method-rating">{word}</span> : null;
}

function ScoreKey({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`nh-method-key${compact ? " nh-method-key-compact" : ""}`}>
      <p className="nh-method-label">READING A 0 TO 100 SCORE</p>
      <ul>
        {scoreKeyBands.map((band) => (
          <li key={band.min}>
            <b>{ratingForScore(band.min)}</b>
            <span className="nh-method-key-range">{band.min === 0 ? "Below 40" : band.max === 100 ? "85 and above" : `${band.min} to ${band.max}`}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function CategoryTotal({ item }: { item: Methodology }) {
  const total = categoryScore(item);
  if (item.total.kind === "note") {
    const sample = round1(fairPriceScore(sampleFlat.askingPrice, sampleFlat.estimate, FAIR_PRICE_CONFIDENCE_FACTOR.HIGH));
    return (
      <div className="nh-method-card nh-method-total">
        <h3>Fair price has no category score</h3>
        <p>It is an estimate, a likely range and a label. It becomes a 0 to 100 score only if you rank it as one of your priorities.</p>
        <p className="nh-method-total-line">Sample flat, ranked as a priority at high confidence: 50 + 500 × (−0.65%) = <b>{sample}</b> <RatingWord score={sample} /></p>
      </div>
    );
  }
  const rows = categoryRows(item);
  const weighted = item.total.kind === "weighted";
  return (
    <div className="nh-method-card nh-method-total">
      <h3>{item.label} total for the sample flat</h3>
      <ul className="nh-method-sum">
        {rows.map((row) => (
          <li key={row.label}>
            <span>{row.label}</span>
            <span>{weighted ? `${row.score} × ${row.pct}%` : `${row.score} pts`}</span>
            <b>{weighted ? fmt((row.score * row.pct) / 100, 2) : row.score}</b>
          </li>
        ))}
        <li className="nh-method-sum-total">
          <span>{weighted ? "Weighted total" : "School proximity score"}</span>
          <span>{weighted ? `${rows.map((row) => fmt((row.score * row.pct) / 100, 2)).join(" + ")}` : rows.map((row) => row.score).join(" + ")}</span>
          <b>{total !== null ? (weighted ? fmt(total) : total) : "—"}</b>
        </li>
      </ul>
      {total !== null && <p className="nh-method-total-line">That is a <RatingWord score={total} /> result{weighted ? " (shown rounded to one decimal)" : ""}.</p>}
      <ScoreKey compact />
    </div>
  );
}

function SampleRanking() {
  const transport = methodology.find((item) => item.id === "transport");
  const transportScore = transport ? categoryScore(transport) ?? 0 : 0;
  const budgetPct = (sampleFlat.askingPrice / sampleFlat.buyerBudget) * 100;
  const overPct = budgetPct - 75;
  const transportRows = transport ? categoryRows(transport) : [];
  const transportExact = weightedAverage(transportRows.map((row) => ({ score: row.score, weight: row.pct }))) ?? 0;
  const rows = [
    {
      label: "Affordability",
      rank: 1,
      input: `${money(sampleFlat.askingPrice)} against a ${money(sampleFlat.buyerBudget)} budget`,
      score: round2(affordabilityScore(sampleFlat.askingPrice, sampleFlat.buyerBudget)),
      steps: [
        `${money(sampleFlat.askingPrice)} ÷ ${money(sampleFlat.buyerBudget)} = ${fmt(budgetPct, 2)}% of budget`,
        `${fmt(budgetPct, 2)}% is ${fmt(overPct, 2)}% above the 75% mark`,
        `${fmt(overPct, 2)} × 2 points = ${fmt(overPct * 2, 2)} points lost`,
        `100 − ${fmt(overPct * 2, 2)} = ${fmt(round2(affordabilityScore(sampleFlat.askingPrice, sampleFlat.buyerBudget)), 2)}`,
      ],
    },
    {
      label: "Space",
      rank: 2,
      input: `${sampleFlat.floorAreaSqm} sqm`,
      score: round2(spaceScore(sampleFlat.floorAreaSqm)),
      steps: [
        `${sampleFlat.floorAreaSqm} − 60 = ${sampleFlat.floorAreaSqm - 60} sqm above the 60 sqm floor`,
        `${sampleFlat.floorAreaSqm - 60} ÷ 60 × 100 = ${fmt(round2(spaceScore(sampleFlat.floorAreaSqm)), 2)}`,
      ],
    },
    {
      label: "Public transport",
      rank: 3,
      input: "Category score from the total above",
      score: transportScore,
      steps: [
        transportRows.map((row) => `${row.score} × ${row.pct}%`).join(" + "),
        `= ${fmt(transportExact, 3)}, rounded to one decimal: ${fmt(transportScore, 1)}`,
      ],
    },
  ];
  const weights = rescaledWeights([true, true, true]);
  const fit = round1(weightedAverage(rows.map((row, index) => ({ score: row.score, weight: weights[index] * 100 }))) ?? 0);
  const twoWeights = rescaledWeights([true, true, false]);
  const partial = round1(weightedAverage(rows.slice(0, 2).map((row, index) => ({ score: row.score, weight: twoWeights[index] * 100 }))) ?? 0);
  const rival = 72.4;
  const gap = round1(fit - rival);
  return (
    <div className="nh-method-card nh-method-sample">
      <h3>One flat, start to finish</h3>
      <p className="nh-method-sample-intro">
        {sampleFlat.address}. Asking {money(sampleFlat.askingPrice)}, {sampleFlat.floorAreaSqm} sqm, {sampleFlat.remainingLeaseYears} years of lease. The buyer’s budget is {money(sampleFlat.buyerBudget)} and their top three priorities are affordability, space and public transport, in that order.
      </p>
      <ul className="nh-method-sum">
        {rows.map((row, index) => (
          <li key={row.label}>
            <span>
              <b>{ordinal(row.rank)} priority: {row.label}</b>
              <small>{row.input}</small>
              <ol className="nh-method-derive" aria-label={`How the ${row.label.toLowerCase()} score is worked out`}>
                {row.steps.map((step) => <li key={step}>{step}</li>)}
              </ol>
            </span>
            <span>{fmt(row.score, 2)} × {Math.round(PRIORITY_WEIGHTS_BY_RANK[index] * 100)}%</span>
            <b>{fmt(row.score * PRIORITY_WEIGHTS_BY_RANK[index], 2)}</b>
          </li>
        ))}
        <li className="nh-method-sum-total">
          <span>Overall fit</span>
          <span>weighted average</span>
          <b>{fmt(fit)}</b>
        </li>
      </ul>
      <p className="nh-method-total-line">An overall fit of {fmt(fit)} reads as <RatingWord score={fit} />.</p>
      <ul className="nh-method-facts">
        <li>
          <b>Tie check.</b> If another shortlisted flat scored {rival}, the gap would be {fmt(gap)} points. That is {isTie([fit, rival]) ? "inside" : "outside"} the {TIE_THRESHOLD}-point rule, so NearHome would report a practical tie instead of naming a winner.
        </li>
        <li>
          <b>Missing data.</b> If public transport could not be assessed for this flat, only 2 of 3 priorities are scored. Coverage is 67%, the weights become {Math.round(twoWeights[0] * 100)}% and {Math.round(twoWeights[1] * 100)}%, the overall fit is {fmt(partial)} and the result is marked provisional.
        </li>
      </ul>
    </div>
  );
}

export default function HowFlatsAreEvaluatedPage() {
  const twoWeights = rescaledWeights([true, true, false]);
  return (
    <div className="nh-method">
      <div className="nh-method-layout">
        <nav className="nh-method-nav" aria-label="On this page">
          <p>ON THIS PAGE</p>
          <ul>
            <li><a href="#from-evidence">From evidence to your ranking</a></li>
            {methodology.map((item) => <li key={item.id}><a href={`#${item.id}`}>{item.label}</a></li>)}
            <li><a href="#reading-results">Reading your results</a></li>
          </ul>
        </nav>

        <div className="nh-method-main">
          <header className="nh-method-intro">
            <p className="nh-method-kicker">METHODOLOGY</p>
            <h1>How NearHome evaluates flats</h1>
            <p>Every result comes from published rules and real data. Here is exactly what we measure, how each score is built, how the scores combine into your ranking, and where the limits are.</p>
            <div className="nh-method-principles">
              {principles.map(({ icon: Icon, title, text }) => (
                <div key={title}><h2><Icon size={17} aria-hidden="true" /> {title}</h2><p>{text}</p></div>
              ))}
            </div>
            <ScoreKey />
          </header>

          <section id="from-evidence" className="nh-method-section" aria-labelledby="from-evidence-heading">
            <h2 id="from-evidence-heading">From evidence to your ranking</h2>
            <p className="nh-method-question">Each flat gets scores that do not depend on the other flats. Your priorities then decide how much each score counts.</p>

            <div className="nh-method-flowrow">
              <OverallFitFlow steps={pipelineSteps} />
              <div className="nh-method-flowtext">
                <p><b>Steps 1 to 3</b> are the same for every buyer. They describe the flat and its surroundings.</p>
                <p><b>Steps 4 to 6</b> depend on you. Your ranking of up to three priorities sets the weights, and the weighted average is the overall fit.</p>
                <p>Every score is absolute, from 0 to 100. A flat is never marked up or down because of the other flats in your shortlist.</p>
              </div>
            </div>

            <div className="nh-method-grid">
              <div className="nh-method-card">
                <h3>Your first priority counts the most</h3>
                <p className="nh-method-lead">The order you rank your priorities sets how much each one counts. Put affordability first and it carries 45% of a flat’s overall fit.</p>
                <ol className="nh-method-weightbars">
                  {PRIORITY_WEIGHTS_BY_RANK.map((weight, index) => (
                    <li key={index}>
                      <span><b>{ordinal(index + 1)} priority</b>{index === 0 ? " · counts the most" : index === 2 ? " · counts the least" : ""}</span>
                      <span className="nh-method-weightbar"><i style={{ width: `${(weight / PRIORITY_WEIGHTS_BY_RANK[0]) * 100}%` }} /></span>
                      <b>{Math.round(weight * 100)}%</b>
                    </li>
                  ))}
                </ol>
                <p className="nh-method-note">With two priorities the weights become {Math.round(twoWeights[0] * 100)}% and {Math.round(twoWeights[1] * 100)}%. With one, it is 100%.</p>
              </div>
              <div className="nh-method-card">
                <h3>Coverage and ties</h3>
                <div className="nh-method-points-block">
                  <h4>Coverage</h4>
                  <ul className="nh-method-points">
                    <li><span className="nh-method-pointtag nh-method-pointtag-meaning">Meaning</span><span>How many of your priorities could be scored for a flat.</span></li>
                    <li><span className="nh-method-pointtag nh-method-pointtag-example">Example</span><span>You rank affordability, space and public transport. Transport can’t be assessed for one flat, so only 2 of 3 are scored: coverage is 67%.</span></li>
                    <li><span className="nh-method-pointtag nh-method-pointtag-action">What happens</span><span>Transport is dropped for that flat and the other weights are rescaled to {Math.round(twoWeights[0] * 100)}% and {Math.round(twoWeights[1] * 100)}%.</span></li>
                    <li><span className="nh-method-pointtag nh-method-pointtag-result">Result</span><span>Marked provisional, because it rests on less than your full list.</span></li>
                  </ul>
                </div>
                <div className="nh-method-points-block">
                  <h4>Practical tie</h4>
                  <ul className="nh-method-points">
                    <li><span className="nh-method-pointtag nh-method-pointtag-meaning">Meaning</span><span>Flats that are too close to separate.</span></li>
                    <li><span className="nh-method-pointtag nh-method-pointtag-rule">Rule</span><span>The best and worst overall fit across your shortlist are within {TIE_THRESHOLD} points.</span></li>
                    <li><span className="nh-method-pointtag nh-method-pointtag-example">Example</span><span>74.9 and 72.4 are 2.5 apart, so it is a tie.</span></li>
                    <li><span className="nh-method-pointtag nh-method-pointtag-why">Why</span><span>A gap that small can come from small differences in the data, so it is not treated as a clear win.</span></li>
                    <li><span className="nh-method-pointtag nh-method-pointtag-result">Result</span><span>NearHome says “practical tie” instead of naming a winner.</span></li>
                  </ul>
                </div>
              </div>
            </div>

            <details className="nh-method-more">
              <summary>How each priority becomes a 0 to 100 score <span aria-hidden="true">＋</span></summary>
            <div className="nh-method-priorities">
              {priorityScoreInfo.map((info) => (
                <article key={info.id} className="nh-method-card nh-method-priority">
                  <h4>{info.label}</h4>
                  <p className="nh-method-priority-input">{info.input}</p>
                  <ul className="nh-method-rulerows" aria-label={`${info.label} scoring`}>
                    <li className="nh-method-rulerows-head"><span>{info.column}</span><span>Score</span></li>
                    {info.rows.map((row) => (
                      <li key={row.when}>
                        <span>{row.when}</span>
                        <b className={`nh-method-tone nh-method-tone-${row.tone}`}>{row.score}</b>
                      </li>
                    ))}
                  </ul>
                  {info.detail && <p className="nh-method-note">{info.detail}</p>}
                  {info.id !== "category" && (
                    <div className="nh-method-curve"><PriorityCurve kind={curveKinds[info.id]} label={info.label} /></div>
                  )}
                </article>
              ))}
            </div>
            <p className="nh-method-note">Every score is clamped to stay between 0 and 100. The amber dot marks the sample flat below, where it has a value.</p>

            </details>

            <SampleRanking />
          </section>

          {methodology.map((item) => {
            const Icon = icons[item.id];
            const guide = evaluationGuides.find((entry) => entry.id === item.id);
            return (
              <section key={item.id} id={item.id} className="nh-method-section" aria-labelledby={`${item.id}-heading`}>
                <h2 id={`${item.id}-heading`}><Icon size={26} aria-hidden="true" /> {item.label}</h2>
                <p className="nh-method-question">{item.question}</p>

                <MethodologyExplorer item={item} />

                <CategoryTotal item={item} />

                <details className="nh-method-more">
                  <summary>Missing data, limits and the full rules <span aria-hidden="true">＋</span></summary>
                <div className="nh-method-grid nh-method-grid-after">
                  <div className="nh-method-card">
                    <h3>If something is missing</h3>
                    <ul className="nh-method-facts nh-method-facts-first">{item.missing.map((line) => <li key={line}>{line}</li>)}</ul>
                  </div>
                  <div className="nh-method-card nh-method-limits-card">
                    <h3>Data and limits</h3>
                    <ul className="nh-method-limits">
                      {item.limits.map((limit) => (
                        <li key={limit.text}>
                          {limit.ok ? <Check size={16} className="nh-method-ok" aria-label="Solid" /> : <AlertTriangle size={16} className="nh-method-warn" aria-label="Limit" />}
                          <span>{limit.text}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>


                  {guide && (
                    <div className="nh-method-rules-body">
                      {guide.disclosures.filter((group) => group.title !== "Data and limits").map((group) => (
                        <div key={group.title}>
                          <h4>{group.title}</h4>
                          <ul>{(group.items ?? [group.text ?? ""]).map((point) => <li key={point}>{point}</li>)}</ul>
                        </div>
                      ))}
                    </div>
                  )}
                </details>
              </section>
            );
          })}

          <section id="reading-results" className="nh-method-section" aria-labelledby="reading-heading">
            <h2 id="reading-heading">Reading your results</h2>
            <div className="nh-method-card nh-method-notassessed">
              <h3>What NearHome doesn’t assess</h3>
              <p>None of these feed into any score. Check them yourself before you decide.</p>
              <ul className="nh-method-facts">{notAssessed.map((line) => <li key={line}>{line}</li>)}</ul>
            </div>

            <Link className="nh-method-cta" href="/">Back to NearHome <ArrowRight size={16} aria-hidden="true" /></Link>
          </section>
        </div>
      </div>
    </div>
  );
}
