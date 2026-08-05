export type SmartPasteSourceType = "url" | "text";

export type ProgressStage = {
  label: string;
  startMs: number;
  endMs: number;
  startPercent: number;
  endPercent: number;
};

/** Stages mirror the backend pipeline: retrieve → prepare → LLM extract → validate. */
export const URL_PROGRESS_STAGES: ProgressStage[] = [
  {
    label: "Retrieving listing page…",
    startMs: 0,
    endMs: 14_000,
    startPercent: 0,
    endPercent: 38,
  },
  {
    label: "Preparing content…",
    startMs: 14_000,
    endMs: 16_000,
    startPercent: 38,
    endPercent: 48,
  },
  {
    label: "Extracting property information…",
    startMs: 16_000,
    endMs: 40_000,
    startPercent: 48,
    endPercent: 88,
  },
  {
    label: "Validating extracted fields…",
    startMs: 40_000,
    endMs: Number.POSITIVE_INFINITY,
    startPercent: 88,
    endPercent: 94,
  },
];

export const TEXT_PROGRESS_STAGES: ProgressStage[] = [
  {
    label: "Preparing content…",
    startMs: 0,
    endMs: 1_500,
    startPercent: 0,
    endPercent: 18,
  },
  {
    label: "Extracting property information…",
    startMs: 1_500,
    endMs: 22_000,
    startPercent: 18,
    endPercent: 88,
  },
  {
    label: "Validating extracted fields…",
    startMs: 22_000,
    endMs: Number.POSITIVE_INFINITY,
    startPercent: 88,
    endPercent: 94,
  },
];

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function interpolateStage(elapsedMs: number, stage: ProgressStage): number {
  if (elapsedMs <= stage.startMs) return stage.startPercent;
  if (Number.isFinite(stage.endMs) && elapsedMs >= stage.endMs) return stage.endPercent;

  if (!Number.isFinite(stage.endMs)) {
    const overshootMs = elapsedMs - stage.startMs;
    const approachSpanMs = 20_000;
    const ratio = 1 - Math.exp(-overshootMs / approachSpanMs);
    return stage.startPercent + (stage.endPercent - stage.startPercent) * ratio;
  }

  const span = stage.endMs - stage.startMs;
  if (span <= 0) return stage.endPercent;

  const ratio = (elapsedMs - stage.startMs) / span;
  return stage.startPercent + (stage.endPercent - stage.startPercent) * ratio;
}

export function getSmartPasteProgress(
  elapsedMs: number,
  sourceType: SmartPasteSourceType,
  pending: boolean,
): { percent: number; label: string } {
  const stages = sourceType === "url" ? URL_PROGRESS_STAGES : TEXT_PROGRESS_STAGES;
  const activeStage =
    stages.find((stage) => elapsedMs >= stage.startMs && elapsedMs < stage.endMs) ??
    stages[stages.length - 1];
  const percentCap = pending ? 94 : 100;
  const percent = clamp(interpolateStage(elapsedMs, activeStage), 0, percentCap);

  return { percent, label: activeStage.label };
}
