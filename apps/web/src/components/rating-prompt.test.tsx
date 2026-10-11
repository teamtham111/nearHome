// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";

const saveSessionFeedback = vi.fn();
vi.mock("@/lib/api", () => ({ saveSessionFeedback: (...args: unknown[]) => saveSessionFeedback(...args) }));

import { RATING_PROMPT_STORAGE_KEY, RatingPrompt } from "./rating-prompt";

function renderPrompt() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RatingPrompt sessionId="session-1" delayMs={1000} />
    </QueryClientProvider>,
  );
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("RatingPrompt", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.localStorage.clear();
    saveSessionFeedback.mockReset();
    saveSessionFeedback.mockResolvedValue({ rating: 4, comment: null });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("appears after the delay and records that it was shown", () => {
    renderPrompt();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    act(() => vi.advanceTimersByTime(1000));

    expect(screen.getByRole("dialog", { name: "How was your NearHome experience?" })).toBeInTheDocument();
    expect(window.localStorage.getItem(RATING_PROMPT_STORAGE_KEY)).not.toBeNull();
  });

  it("never appears again once shown", () => {
    window.localStorage.setItem(RATING_PROMPT_STORAGE_KEY, "2026-10-07T00:00:00.000Z");
    renderPrompt();

    act(() => vi.advanceTimersByTime(5000));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("saves the star rating immediately, then the optional comment", async () => {
    renderPrompt();
    act(() => vi.advanceTimersByTime(1000));

    fireEvent.click(screen.getByRole("radio", { name: /4 stars/ }));
    await flush();
    expect(saveSessionFeedback).toHaveBeenCalledWith("session-1", { rating: 4 });

    fireEvent.change(screen.getByLabelText(/Anything we could improve/), { target: { value: "  More detail  " } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await flush();

    expect(saveSessionFeedback).toHaveBeenLastCalledWith("session-1", { rating: 4, comment: "More detail" });
    expect(screen.getByRole("status")).toHaveTextContent("Thanks for the feedback!");

    act(() => vi.advanceTimersByTime(2500));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes without saving when dismissed", () => {
    renderPrompt();
    act(() => vi.advanceTimersByTime(1000));

    fireEvent.click(screen.getByRole("button", { name: "Close rating" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(saveSessionFeedback).not.toHaveBeenCalled();
  });
});
