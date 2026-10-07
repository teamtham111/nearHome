"use client";

import { ChevronLeft, ChevronRight, X } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import React, { useEffect, useRef, useState } from "react";
import type { ReactNode, RefObject } from "react";

interface PasteHelpStep {
  heading: string;
  instructions: ReactNode;
  commands?: ReadonlyArray<{ platform: string; shortcut: string }>;
  reassurance?: ReactNode;
  image: string;
  imageAlt: string;
  shortcut: string;
}

const STEPS: ReadonlyArray<PasteHelpStep> = [
  {
    heading: "Select the whole listing page",
    instructions: "Open the property listing and click anywhere on the page. Then select the entire page:",
    commands: [
      { platform: "Mac", shortcut: "⌘ + A" },
      { platform: "Windows", shortcut: "Ctrl + A" },
    ],
    image: "/paste-guide/select-page.jpg",
    imageAlt: "A browser screenshot of a sample HDB listing with all page text selected",
    shortcut: "⌘ + A  or  Ctrl + A",
  },
  {
    heading: "Copy everything",
    instructions: "With the page still selected, copy everything:",
    commands: [
      { platform: "Mac", shortcut: "⌘ + C" },
      { platform: "Windows", shortcut: "Ctrl + C" },
    ],
    image: "/paste-guide/copy-page.jpg",
    imageAlt: "A browser screenshot of selected details on a sample HDB listing",
    shortcut: "⌘ + C  or  Ctrl + C",
  },
  {
    heading: "Paste it into NearHome",
    instructions: "Return to NearHome, click inside the listing-text box, then paste:",
    commands: [
      { platform: "Mac", shortcut: "⌘ + V" },
      { platform: "Windows", shortcut: "Ctrl + V" },
    ],
    reassurance: (
      <>
        <strong className="font-bold text-slate-800">Don&apos;t worry if it looks messy</strong> — we&apos;ll organise it for you.
      </>
    ),
    image: "/paste-guide/paste-nearhome.jpg",
    imageAlt: "A screenshot of messy copied webpage text pasted into NearHome's listing-text field",
    shortcut: "⌘ + V  or  Ctrl + V",
  },
  {
    heading: "Review the formatted details",
    instructions: (
      <>
        Select <strong>Add a flat</strong>. NearHome organises the listing into clear fields for you to review and edit before confirming.
      </>
    ),
    image: "/paste-guide/review-nearhome.jpg",
    imageAlt: "A screenshot of NearHome's neatly formatted listing review fields",
    shortcut: "Review and confirm",
  },
] as const;

interface PasteHelpDialogProps {
  open: boolean;
  onClose: () => void;
  returnFocusRef: RefObject<HTMLButtonElement | null>;
}

const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

export function PasteHelpDialog({ open, onClose, returnFocusRef }: PasteHelpDialogProps) {
  const [stepIndex, setStepIndex] = useState(0);
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;

    const previousOverflow = document.body.style.overflow;
    const returnFocus = returnFocusRef.current;
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setStepIndex(0);
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;

      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      ).filter((element) => !element.hasAttribute("disabled"));
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      returnFocus?.focus();
    };
  }, [open, returnFocusRef]);

  if (!open) return null;

  const step = STEPS[stepIndex];
  const isLastStep = stepIndex === STEPS.length - 1;
  const closeDialog = () => {
    setStepIndex(0);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/50 p-0 sm:items-center sm:p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) closeDialog();
      }}
    >
      <div
        ref={dialogRef}
        className="flex max-h-[96dvh] w-full flex-col overflow-hidden rounded-t-2xl bg-white shadow-2xl sm:max-h-[calc(100dvh-2rem)] sm:max-w-2xl sm:rounded-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="paste-help-dialog-title"
        aria-describedby="paste-help-step-copy"
      >
        <div className="flex items-center justify-between gap-4 border-b border-slate-200 px-5 py-4 sm:px-6">
          <div>
            <h2 id="paste-help-dialog-title" className="text-lg font-bold text-blue-950 sm:text-xl">How to paste listing text</h2>
            <p className="mt-1 text-xs text-slate-500">Copy the full listing page; NearHome will organise the useful details.</p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            className="nh-ghost-icon-button"
            aria-label="Close paste guide"
            onClick={closeDialog}
          >
            <X size={20} aria-hidden="true" />
          </button>
        </div>

        <div className="overflow-y-auto px-5 py-5 sm:px-6 sm:py-6" aria-live="polite">
          <div className="relative overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
            <Image
              src={step.image}
              alt={step.imageAlt}
              width={1200}
              height={675}
              unoptimized
              className="aspect-[16/9] w-full object-cover"
            />
            <span className="absolute bottom-3 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-lg bg-slate-950/90 px-4 py-2 text-sm font-bold text-white shadow-lg sm:bottom-4 sm:text-base">
              {step.shortcut}
            </span>
          </div>
          <p className="mt-5 text-xs font-bold uppercase tracking-wider text-teal-700">
            Step {stepIndex + 1} of {STEPS.length}
          </p>
          <h3 className="mt-1 text-xl font-bold text-slate-900 sm:text-2xl">{step.heading}</h3>
          <div id="paste-help-step-copy" className="mt-2 text-sm leading-6 text-slate-600 sm:text-base">
            <p>{step.instructions}</p>
            {step.commands && (
              <ul className="mt-3 space-y-1.5" aria-label="Keyboard shortcuts">
                {step.commands.map((command) => (
                  <li key={command.platform} className="flex items-baseline gap-2">
                    <span className="min-w-20">{command.platform}:</span>
                    <strong className="font-bold text-slate-900"><kbd>{command.shortcut}</kbd></strong>
                  </li>
                ))}
              </ul>
            )}
            {step.reassurance && <p className="mt-3">{step.reassurance}</p>}
          </div>
        </div>

        <div className="border-t border-slate-200 bg-slate-50 px-5 py-4 sm:px-6">
          <div className="mb-4 text-center">
            <Link href="/how-it-works#copy-and-paste" className="text-sm font-semibold text-teal-800 hover:underline focus:outline-none focus:ring-2 focus:ring-teal-600 focus:ring-offset-2" onClick={closeDialog}>
              View the complete guide →
            </Link>
          </div>
          <div className="flex items-center justify-center gap-2" aria-label={`Step ${stepIndex + 1} of ${STEPS.length}`}>
            {STEPS.map((item, index) => (
              <span
                key={item.heading}
                className={`h-2 rounded-full transition-all ${index === stepIndex ? "w-6 bg-teal-700" : "w-2 bg-slate-300"}`}
                aria-current={index === stepIndex ? "step" : undefined}
              />
            ))}
          </div>
          <div className="mt-4 flex items-center justify-between gap-3">
            <button
              type="button"
              className="nh-secondary"
              disabled={stepIndex === 0}
              onClick={() => setStepIndex((current) => Math.max(0, current - 1))}
            >
              <ChevronLeft className="mr-1" size={18} aria-hidden="true" />
              Back
            </button>
            <button
              type="button"
              className="nh-primary min-w-28"
              onClick={() => {
                if (isLastStep) closeDialog();
                else setStepIndex((current) => Math.min(STEPS.length - 1, current + 1));
              }}
            >
              {isLastStep ? "Got it" : "Next"}
              {!isLastStep && <ChevronRight className="ml-1" size={18} aria-hidden="true" />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
