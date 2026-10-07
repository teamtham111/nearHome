// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import React, { useRef, useState } from "react";
import { PasteHelpDialog } from "./paste-help-dialog";

function GuideHarness() {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);

  return (
    <>
      <button ref={buttonRef} type="button" onClick={() => setOpen(true)}>
        How to paste listing text
      </button>
      <PasteHelpDialog
        open={open}
        onClose={() => setOpen(false)}
        returnFocusRef={buttonRef}
      />
    </>
  );
}

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
});

describe("PasteHelpDialog", () => {
  it("opens on step 1 and navigates through all four steps", () => {
    render(<GuideHarness />);
    const trigger = screen.getByRole("button", { name: "How to paste listing text" });

    fireEvent.click(trigger);
    expect(screen.getByRole("dialog", { name: "How to paste listing text" })).toBeVisible();
    expect(screen.getByRole("link", { name: "View the complete guide →" })).toHaveAttribute("href", "/how-it-works#copy-and-paste");
    expect(screen.getByText("Step 1 of 4")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Select the whole listing page" })).toBeVisible();
    const shortcuts = screen.getByRole("list", { name: "Keyboard shortcuts" });
    expect(shortcuts).toHaveTextContent("Mac:⌘ + A");
    expect(shortcuts).toHaveTextContent("Windows:Ctrl + A");
    expect(shortcuts.querySelectorAll("strong")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Back" })).toBeDisabled();
    expect(document.body).toHaveStyle({ overflow: "hidden" });

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Step 2 of 4")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Copy everything" })).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByText("Step 1 of 4")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Step 3 of 4")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Paste it into NearHome" })).toBeVisible();
    const reassurance = screen.getByText(/don't worry if it looks messy/i);
    expect(reassurance).toBeVisible();
    expect(reassurance.tagName).toBe("STRONG");

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Step 4 of 4")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Review the formatted details" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Got it" })).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Got it" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(document.body.style.overflow).toBe("");
  });

  it("closes with the visible close button and restores trigger focus", () => {
    render(<GuideHarness />);
    const trigger = screen.getByRole("button", { name: "How to paste listing text" });
    fireEvent.click(trigger);

    const closeButton = screen.getByRole("button", { name: "Close paste guide" });
    expect(closeButton).toHaveFocus();
    fireEvent.click(closeButton);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("traps focus and closes with Escape", () => {
    render(<GuideHarness />);
    const trigger = screen.getByRole("button", { name: "How to paste listing text" });
    fireEvent.click(trigger);

    const closeButton = screen.getByRole("button", { name: "Close paste guide" });
    const nextButton = screen.getByRole("button", { name: "Next" });
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(nextButton).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(closeButton).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
