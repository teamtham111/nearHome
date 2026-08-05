import type { CSSProperties } from "react";

export type ScoreRingProps = {
  score: number | null;
  label: string;
  size?: "sm" | "md" | "lg";
  statusLabel?: string;
  partial?: boolean;
  unavailableReason?: string;
};

const sizes = {
  sm: { ring: "h-12 w-12", score: "text-sm", label: "text-xs" },
  md: { ring: "h-16 w-16", score: "text-lg", label: "text-sm" },
  lg: { ring: "h-24 w-24", score: "text-2xl", label: "text-sm" },
} as const;

function colourFor(score: number | null, partial: boolean) {
  if (score == null) return { ring: "#94a3b8", text: "text-slate-600" };
  if (partial) return { ring: "#d97706", text: "text-amber-700" };
  if (score >= 80) return { ring: "#15803d", text: "text-green-700" };
  if (score >= 60) return { ring: "#0f766e", text: "text-teal-700" };
  if (score >= 40) return { ring: "#d97706", text: "text-amber-700" };
  return { ring: "#dc2626", text: "text-red-700" };
}

export function ScoreRing({
  score,
  label,
  size = "md",
  statusLabel,
  partial = false,
  unavailableReason,
}: ScoreRingProps) {
  const palette = colourFor(score, partial);
  const dimensions = sizes[size];
  const value = score == null ? 0 : Math.min(100, Math.max(0, score));
  const status = statusLabel ?? (score == null ? "Not assessed" : partial ? "Partial" : "Assessed");

  return (
    <div className="flex items-center gap-3" aria-label={`${label}: ${score == null ? status : `${Math.round(score)} out of 100, ${status}`}`}>
      <div
        className={`relative shrink-0 rounded-full ${dimensions.ring}`}
        role="img"
        style={{ background: `conic-gradient(${palette.ring} ${value}%, #e2e8f0 ${value}% 100%)` } as CSSProperties}
      >
        <div className="absolute inset-[4px] flex flex-col items-center justify-center rounded-full bg-white">
          <span className={`font-semibold leading-none ${dimensions.score} ${palette.text}`}>
            {score == null ? "—" : Math.round(score)}
          </span>
          {score != null && <span className="mt-0.5 text-[10px] text-slate-500">/100</span>}
        </div>
      </div>
      <div className="min-w-0">
        <p className={`font-medium text-slate-900 ${dimensions.label}`}>{label}</p>
        <p className={`mt-0.5 text-xs ${palette.text}`}>{status}</p>
        {unavailableReason && <p className="mt-1 text-xs leading-4 text-slate-500">{unavailableReason}</p>}
      </div>
    </div>
  );
}
