import { BusFront, Car, GraduationCap, TrendingUp, type LucideIcon } from "lucide-react";

const examples = [
  { name: "Avenue 3", type: "4-room", score: 82, tone: "emerald" },
  { name: "Street 86", type: "4-room", score: 74, tone: "blue" },
  { name: "East Way", type: "4-room", score: 68, tone: "violet" },
];

const previewRows: Array<{ icon: LucideIcon; label: string; values: string[] }> = [
  { icon: BusFront, label: "Public transport", values: ["82", "74", "68"] },
  { icon: Car, label: "Driving", values: ["76", "72", "65"] },
  { icon: TrendingUp, label: "Fair-price estimate", values: ["Range", "Range", "Range"] },
  { icon: GraduationCap, label: "School access", values: ["Available", "Available", "Available"] },
];

export function MarketingPreview({ compact = false }: { compact?: boolean }) {
  return (
    <section className={`overflow-hidden rounded-2xl border border-blue-100 bg-white p-4 shadow-[0_16px_48px_rgba(30,64,175,0.12)] ${compact ? "max-w-xl" : ""}`} aria-label="Example comparison preview">
      <div className="flex items-center justify-between gap-3">
        <div><p className="font-semibold text-blue-950">Example comparison</p><p className="text-xs text-slate-500">Illustrative preview</p></div>
        <span className="rounded-full bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700">3 flats</span>
      </div>
      <div className="mt-4 grid grid-cols-3 gap-2">
        {examples.map((example, index) => (
          <div key={example.name} className={`rounded-xl border p-2 ${index === 0 ? "border-emerald-300 bg-emerald-50/50" : "border-slate-200 bg-slate-50/50"}`}>
            <div className="h-12 rounded-lg bg-gradient-to-br from-blue-100 via-sky-50 to-emerald-100" aria-hidden="true" />
            <p className="mt-2 truncate text-xs font-semibold text-blue-950">{example.name}</p>
            <p className="mt-0.5 text-[11px] text-slate-500">{example.type}</p>
            <p className={`mt-2 text-lg font-bold ${example.tone === "emerald" ? "text-emerald-700" : example.tone === "blue" ? "text-blue-700" : "text-violet-700"}`}>{example.score}<span className="text-[10px] font-medium">/100</span></p>
          </div>
        ))}
      </div>
      {!compact && <div className="mt-4 overflow-hidden rounded-xl border border-slate-200 text-xs">
        {previewRows.map(({ icon: Icon, label, values }) => (
          <div key={label} className="grid grid-cols-[1.5fr_repeat(3,1fr)] border-b border-slate-100 last:border-0">
            <span className="flex items-center gap-1.5 px-2 py-2 text-slate-600"><Icon size={12} /> {label}</span>
            {values.map((value, index) => <span key={index} className={`border-l border-slate-100 px-1 py-2 text-center font-semibold ${index === 0 ? "text-emerald-700" : index === 1 ? "text-blue-700" : "text-violet-700"}`}>{value}</span>)}
          </div>
        ))}
      </div>}
      <p className="mt-3 text-center text-xs font-semibold text-blue-700">View full comparison <span aria-hidden="true">→</span></p>
    </section>
  );
}
