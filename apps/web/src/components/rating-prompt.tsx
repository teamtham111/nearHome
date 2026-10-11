"use client";

import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { saveSessionFeedback } from "@/lib/api";

export const RATING_PROMPT_STORAGE_KEY = "nearhome:rating-prompt-shown";
const STARS = [1, 2, 3, 4, 5] as const;
const STAR_LABELS = ["Poor", "Fair", "Good", "Very good", "Excellent"];

function alreadyPrompted() {
  try {
    return window.localStorage.getItem(RATING_PROMPT_STORAGE_KEY) !== null;
  } catch {
    return true;
  }
}

function markPrompted() {
  try {
    window.localStorage.setItem(RATING_PROMPT_STORAGE_KEY, new Date().toISOString());
  } catch {
    /* storage unavailable: the prompt simply may appear again */
  }
}

/** A small corner popup asking for a 1–5 star rating, shown once per browser. */
export function RatingPrompt({
  sessionId,
  delayMs = 8000,
  onSave = saveSessionFeedback,
}: {
  sessionId: string;
  delayMs?: number;
  onSave?: typeof saveSessionFeedback;
}) {
  const [open, setOpen] = useState(false);
  const [rating, setRating] = useState<number | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const [comment, setComment] = useState("");
  const [step, setStep] = useState<"rate" | "comment" | "thanks">("rate");

  const save = useMutation({
    mutationFn: (body: { rating: number; comment?: string }) => onSave(sessionId, body),
  });

  useEffect(() => {
    if (alreadyPrompted()) return;
    const timer = window.setTimeout(() => {
      // Mark as soon as it is shown, so leaving the page also counts as the one showing.
      markPrompted();
      setOpen(true);
    }, delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs]);

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open]);

  useEffect(() => {
    if (step !== "thanks") return;
    const timer = window.setTimeout(() => setOpen(false), 2500);
    return () => window.clearTimeout(timer);
  }, [step]);

  if (!open) return null;

  const shown = hovered ?? rating ?? 0;

  function chooseRating(value: number) {
    setRating(value);
    setStep("comment");
    save.mutate({ rating: value });
  }

  function sendComment() {
    if (rating === null) return;
    const trimmed = comment.trim();
    if (!trimmed) {
      setStep("thanks");
      return;
    }
    save.mutate({ rating, comment: trimmed }, { onSuccess: () => setStep("thanks") });
  }

  return (
    <div
      role="dialog"
      aria-labelledby="rating-prompt-title"
      className="nh-rating-prompt fixed inset-x-4 bottom-4 z-40 rounded-xl border border-[var(--nh-border)] bg-[var(--nh-surface)] p-4 shadow-lg sm:inset-x-auto sm:right-6 sm:bottom-6 sm:w-80"
    >
      <button
        type="button"
        aria-label="Close rating"
        className="absolute right-2 top-2 flex h-8 w-8 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-800"
        onClick={() => setOpen(false)}
      >
        <svg viewBox="0 0 16 16" className="h-4 w-4" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" /></svg>
      </button>

      {step === "thanks" ? (
        <p id="rating-prompt-title" className="pr-8 text-sm font-medium text-slate-900" role="status">Thanks for the feedback!</p>
      ) : (
        <>
          <p id="rating-prompt-title" className="pr-8 text-sm font-semibold text-slate-900">How was your NearHome experience?</p>
          <div className="mt-2 flex items-center gap-1" role="radiogroup" aria-label="Rating" onMouseLeave={() => setHovered(null)}>
            {STARS.map((value) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={rating === value}
                aria-label={`${value} star${value === 1 ? "" : "s"} — ${STAR_LABELS[value - 1]}`}
                className="rounded p-0.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-600"
                onMouseEnter={() => setHovered(value)}
                onFocus={() => setHovered(value)}
                onBlur={() => setHovered(null)}
                onClick={() => chooseRating(value)}
              >
                <svg viewBox="0 0 24 24" className={`h-7 w-7 transition-colors ${value <= shown ? "text-amber-500" : "text-slate-300"}`} aria-hidden="true">
                  <path fill="currentColor" d="M12 2.5l2.9 6.06 6.6.8-4.86 4.57 1.24 6.55L12 17.27l-5.88 3.21 1.24-6.55L2.5 9.36l6.6-.8L12 2.5z" />
                </svg>
              </button>
            ))}
            <span className="ml-2 text-xs text-slate-600" aria-hidden="true">{shown ? STAR_LABELS[shown - 1] : ""}</span>
          </div>

          {step === "comment" && (
            <div className="mt-3 space-y-2">
              <label htmlFor="rating-prompt-comment" className="block text-xs text-slate-600">Anything we could improve? (optional)</label>
              <textarea
                id="rating-prompt-comment"
                rows={2}
                maxLength={2000}
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                className="w-full rounded-lg border border-slate-300 bg-white p-2 text-sm focus:border-teal-600 focus:outline-none focus:ring-1 focus:ring-teal-600"
              />
              <div className="flex justify-end gap-2">
                <button type="button" className="px-3 py-1.5 text-sm text-slate-600 hover:text-slate-900" onClick={() => setStep("thanks")}>Skip</button>
                <button type="button" className="nh-primary px-3 py-1.5 text-sm" disabled={save.isPending} onClick={sendComment}>
                  {save.isPending ? "Sending…" : "Send"}
                </button>
              </div>
            </div>
          )}
          {save.isError && <p className="mt-2 text-xs text-red-700" role="alert">Couldn&apos;t save your rating. Please try again.</p>}
        </>
      )}
    </div>
  );
}
