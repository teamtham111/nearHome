import Link from "next/link";
import type { LucideIcon } from "lucide-react";

export function ModelCard({
  title,
  description,
  supportingLine,
  outcome,
  icon: Icon,
  tone = "blue",
  detailsHref,
}: {
  title: string;
  description: string;
  supportingLine?: string;
  outcome?: string;
  icon: LucideIcon;
  tone?: "green" | "blue" | "purple" | "orange";
  detailsHref?: string;
}) {
  const tones = {
    green: "bg-emerald-50 text-emerald-700 ring-emerald-100",
    blue: "bg-blue-50 text-blue-700 ring-blue-100",
    purple: "bg-violet-50 text-violet-700 ring-violet-100",
    orange: "bg-orange-50 text-orange-700 ring-orange-100",
  };
  return (
    <article className="flex h-full flex-col rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_8px_30px_rgba(15,23,42,0.04)]">
      <span className={`grid h-12 w-12 place-items-center rounded-2xl ring-1 ${tones[tone]}`} aria-hidden="true"><Icon size={25} /></span>
      <h3 className="mt-4 text-lg font-semibold tracking-tight text-blue-950">{title}</h3>
      {supportingLine && <p className="mt-2 text-sm font-semibold leading-6 text-teal-700">{supportingLine}</p>}
      <p className="mt-2 text-sm leading-6 text-slate-600">{description}</p>
      {outcome && <div className="mt-5 rounded-xl bg-slate-50 px-3 py-3"><p className="text-xs font-semibold uppercase tracking-wide text-slate-700">Why it matters</p><p className="mt-1 text-sm leading-6 text-slate-600">{outcome}</p></div>}
      {detailsHref && <Link href={detailsHref} className="mt-5 inline-flex text-sm font-semibold text-blue-700 hover:text-blue-800 focus:outline-none focus:underline">View model details <span aria-hidden="true" className="ml-1">→</span></Link>}
    </article>
  );
}
