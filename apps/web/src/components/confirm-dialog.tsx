"use client";

import { useEffect, useRef } from "react";
import type { RefObject } from "react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  pending?: boolean;
  error?: string | null;
  confirmLabel?: string;
  onCancel: () => void;
  onConfirm: () => void;
  fallbackFocusRef?: RefObject<HTMLElement | null>;
}

export function ConfirmDialog({
  open,
  title,
  message,
  pending = false,
  error,
  confirmLabel = "Remove flat",
  onCancel,
  onConfirm,
  fallbackFocusRef,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const confirmedRef = useRef(false);
  const onCancelRef = useRef(onCancel);
  const pendingRef = useRef(pending);

  useEffect(() => {
    onCancelRef.current = onCancel;
    pendingRef.current = pending;
  }, [onCancel, pending]);

  useEffect(() => {
    if (!open) return;
    confirmedRef.current = false;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const fallbackFocus = fallbackFocusRef?.current;
    cancelRef.current?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!pendingRef.current) {
          confirmedRef.current = false;
          onCancelRef.current();
        }
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = [cancelRef.current, confirmRef.current].filter(
        (element): element is HTMLButtonElement => Boolean(element && !element.disabled),
      );
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
      if (confirmedRef.current) {
        fallbackFocus?.focus();
      } else if (previousFocus?.isConnected) {
        previousFocus.focus();
      } else {
        fallbackFocus?.focus();
      }
    };
  }, [fallbackFocusRef, open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4" role="presentation">
      <div
        className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-message"
      >
        <h2 id="confirm-dialog-title" className="text-lg font-semibold text-slate-900">{title}</h2>
        <p id="confirm-dialog-message" className="mt-2 text-sm text-slate-700">{message}</p>
        {error && <p className="mt-3 text-sm text-red-700" role="alert">{error}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button
            ref={cancelRef}
            type="button"
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            onClick={() => {
              confirmedRef.current = false;
              onCancel();
            }}
            disabled={pending}
          >
            Cancel
          </button>
          <button
            ref={confirmRef}
            type="button"
            className="rounded-lg bg-red-700 px-4 py-2 text-sm text-white hover:bg-red-800 disabled:opacity-50"
            onClick={() => {
              confirmedRef.current = true;
              onConfirm();
            }}
            disabled={pending}
          >
            {pending ? "Removing…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
