"use client";

import { useEffect, useRef, useState } from "react";
import {
  getSmartPasteProgress,
  type SmartPasteSourceType,
} from "@/lib/smart-paste-progress";

type SmartPasteProgressProps = {
  active: boolean;
  sourceType: SmartPasteSourceType;
};

export function SmartPasteProgress({ active, sourceType }: SmartPasteProgressProps) {
  const [percent, setPercent] = useState(0);
  const [label, setLabel] = useState("");
  const startTimeRef = useRef<number | null>(null);
  const frameRef = useRef<number | null>(null);

  useEffect(() => {
    if (!active) {
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
      startTimeRef.current = null;
      return undefined;
    }

    startTimeRef.current = performance.now();

    const tick = (now: number) => {
      const startedAt = startTimeRef.current ?? now;
      const { percent: nextPercent, label: nextLabel } = getSmartPasteProgress(
        now - startedAt,
        sourceType,
        true,
      );
      setPercent(nextPercent);
      setLabel(nextLabel);
      frameRef.current = requestAnimationFrame(tick);
    };

    frameRef.current = requestAnimationFrame(tick);

    return () => {
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
    };
  }, [active, sourceType]);

  if (!active) return null;

  const roundedPercent = Math.round(percent);

  return (
    <div className="space-y-2" role="status" aria-live="polite">
      <div className="flex items-center justify-between gap-3 text-sm text-teal-800">
        <span>{label}</span>
        <span className="tabular-nums text-teal-700">{roundedPercent}%</span>
      </div>
      <div
        className="h-2.5 w-full overflow-hidden rounded-full bg-teal-100"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={roundedPercent}
        aria-valuetext={`${label} ${roundedPercent}%`}
      >
        <div
          className="relative h-full rounded-full bg-teal-600 transition-[width] duration-100 ease-linear"
          style={{ width: `${percent}%` }}
        >
          <div className="absolute inset-0 animate-pulse bg-white/20" />
        </div>
      </div>
    </div>
  );
}
