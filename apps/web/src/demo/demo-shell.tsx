"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { usePathname } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Analytics } from "@vercel/analytics/next";
import { DEMO_STEPS } from "./script";
import { resetDemoFixtures, restoreDemoFixtures, snapshotDemoFixtures } from "./fixtures";

// `speed` shrinks every animation delay while the walkthrough fast-forwards; polling never shrinks.
let speed = 1;
const delay = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, Math.round(ms * speed)));
const poll = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));
const FAST_SPEED = 0.1;
const playbackDelay = (ms: number) => delay(Math.round(ms / 0.75));
const formKey = "nearhome_interactive_demo_form";
const snapshotKey = (index: number) => `nearhome_interactive_demo_snapshot_${index}`;
const demoSections = ["Setup", "See matches", "Compare", "Your shortlist", "Explore area"];
const CONTROLS_RESERVE = 132;
const sectionOf = (step: (typeof DEMO_STEPS)[number]) => step.stage <= 2 ? 0
  : step.stage === 3 ? 1
    : step.stage === 4 ? 2
      : ["results-transport", "results-driving", "results-schools"].includes(step.id) ? 4 : 3;
// Scroll with our own short, timer-driven animation. Native smooth scrolling can stall or
// restart (hidden tabs, repeated calls), which left the cursor aimed at a moving target.
async function animateScroll(scroller: HTMLElement | null, top: number, instant: boolean) {
  const read = () => (scroller ? scroller.scrollTop : window.scrollY);
  const write = (value: number) => (scroller ? scroller.scrollTo({ top: value, behavior: "auto" }) : window.scrollTo({ top: value, behavior: "auto" }));
  const from = read();
  const target = Math.max(0, top);
  const duration = instant ? 0 : Math.min(450, 160 + Math.abs(target - from) * 0.35);
  if (!duration) { write(target); return; }
  const startedAt = performance.now();
  for (;;) {
    const t = Math.min(1, (performance.now() - startedAt) / duration);
    write(from + (target - from) * (1 - Math.pow(1 - t, 3)));
    if (t >= 1) break;
    await poll(16);
  }
  write(target);
}
const sectionStarts = demoSections.map((_, section) => DEMO_STEPS.findIndex((step) => sectionOf(step) === section));
const PRIORITY_STEPS: Record<string, { option: string; selection: string[] }> = {
  "priority-1": { option: "priority-option-affordability", selection: ["AFFORDABILITY"] },
  "priority-2": { option: "priority-option-space", selection: ["AFFORDABILITY", "SPACE"] },
  "priority-3": { option: "priority-option-transport", selection: ["AFFORDABILITY", "SPACE", "PUBLIC_TRANSPORT"] },
};
const PRIORITY_STAGES = ["start", "added", "picker"];

function demoRoute(route: string, step: number, extra: Record<string, string> = {}) {
  const params = new URLSearchParams({ demo: "1", demoStep: String(step) });
  if (new URLSearchParams(window.location.search).get("demoEmbed") === "1" || localStorage.getItem("nearhome_demo_embedded") === "1") params.set("demoEmbed", "1");
  for (const [key, value] of Object.entries(extra)) params.set(key, value);
  return `${route}?${params.toString()}`;
}

function readDemoForm(): Record<string, unknown> {
  try { return JSON.parse(localStorage.getItem(formKey) ?? "{}"); } catch { return {}; }
}

function updateDemoForm(patch: Record<string, unknown>) {
  localStorage.setItem(formKey, JSON.stringify({ ...readDemoForm(), ...patch }));
}

function saveStepSnapshot(index: number) {
  localStorage.setItem(snapshotKey(index), snapshotDemoFixtures() ?? "__EMPTY__");
  localStorage.setItem(`${snapshotKey(index)}_form`, localStorage.getItem(formKey) ?? "{}");
}

async function waitForDemoFlatCount(expected: number) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const state = JSON.parse(localStorage.getItem("nearhome_interactive_demo_state") ?? "{}") as { listings?: unknown[] };
      if ((state.listings?.length ?? 0) >= expected) return;
    } catch { /* Keep waiting for the demo fixture update. */ }
    await poll(100);
  }
  throw new Error("The demo listing was not added to the shortlist.");
}

function setInputValue(element: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string) {
  const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

export function DemoShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const [active, setActive] = useState(false);
  const [embeddedMode, setEmbeddedMode] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  const [pauseAtPages, setPauseAtPages] = useState(true);
  const [point, setPoint] = useState({ x: -40, y: -40 });
  const [reducedMotion, setReducedMotion] = useState(false);
  const [analyticsAllowed, setAnalyticsAllowed] = useState(false);
  const [keys, setKeys] = useState<string[]>([]);
  const [backRevision, setBackRevision] = useState(0);
  const [stepError, setStepError] = useState<string | null>(null);
  const [switching, setSwitching] = useState<"listing" | "nearhome" | null>(null);
  const captionRef = useRef<HTMLElement | null>(null);
  const fastTargetRef = useRef<number | null>(null);
  const busy = useRef(false);
  const playingRef = useRef(false);
  const pauseRef = useRef(true);
  const indexRef = useRef(0);
  const advanceRef = useRef<(() => void) | null>(null);
  const actionProgress = useRef<Record<string, string>>({});
  const actionTimes = useRef<Record<string, number>>({});
  const [captionPosition, setCaptionPosition] = useState({ left: 0, top: 0 });
  const step = DEMO_STEPS[Math.min(stepIndex, DEMO_STEPS.length - 1)];

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initialize = window.setTimeout(() => {
      const initialStep = Math.max(0, Math.min(DEMO_STEPS.length - 1, Number(params.get("demoStep") ?? 0) || 0));
      if (params.get("demoEmbed") === "1") {
        document.body.dataset.demoEmbedded = "true";
        localStorage.setItem("nearhome_demo_embedded", "1");
        setEmbeddedMode(true);
      }
      if (params.get("demo") === "1") {
        if (!params.has("demoStep") || initialStep === 0) {
          resetDemoFixtures();
          localStorage.removeItem(formKey);
          for (let i = 0; i < DEMO_STEPS.length; i += 1) { localStorage.removeItem(snapshotKey(i)); localStorage.removeItem(`${snapshotKey(i)}_form`); }
          sessionStorage.removeItem("nearhome_demo_clipboard");
        }
        localStorage.setItem("nearhome_interactive_demo", "1");
        document.body.dataset.demoActive = "true";
        setActive(true);
        setStepIndex(initialStep);
        indexRef.current = initialStep;
        saveStepSnapshot(initialStep);
      } else setAnalyticsAllowed(true);
      setReducedMotion(window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    }, 0);
    const startDemo = () => {
      resetDemoFixtures();
      localStorage.removeItem(formKey);
      for (let i = 0; i < DEMO_STEPS.length; i += 1) { localStorage.removeItem(snapshotKey(i)); localStorage.removeItem(`${snapshotKey(i)}_form`); }
      sessionStorage.removeItem("nearhome_demo_clipboard");
      document.body.dataset.demoActive = "true";
      saveStepSnapshot(0);
      setActive(true);
      setAnalyticsAllowed(false);
      setStepIndex(0);
      indexRef.current = 0;
    };
    window.addEventListener("nearhome-demo-start", startDemo);
    return () => { window.clearTimeout(initialize); window.removeEventListener("nearhome-demo-start", startDemo); };
  }, []);

  const exit = useCallback(() => {
    const embedded = new URLSearchParams(window.location.search).get("demoEmbed") === "1" || localStorage.getItem("nearhome_demo_embedded") === "1";
    localStorage.removeItem("nearhome_interactive_demo");
    localStorage.removeItem("nearhome_demo_embedded");
    delete document.body.dataset.demoActive;
    delete document.body.dataset.demoEmbedded;
    setEmbeddedMode(false);
    resetDemoFixtures();
    localStorage.removeItem(formKey);
    for (let i = 0; i < DEMO_STEPS.length; i += 1) { localStorage.removeItem(snapshotKey(i)); localStorage.removeItem(`${snapshotKey(i)}_form`); }
    sessionStorage.removeItem("nearhome_demo_clipboard");
    if (localStorage.getItem("nearhome_session_id") === "demo-session") localStorage.removeItem("nearhome_session_id");
    setActive(false);
    setAnalyticsAllowed(true);
    setPlaying(false);
    playingRef.current = false;
    if (embedded) window.parent.postMessage({ type: "nearhome-demo-exit" }, window.location.origin);
    router.push("/");
  }, [router]);

  const findTarget = useCallback(async (target?: string) => {
    if (!target) return null;
    const attempts = target.startsWith("comparison-") || target === "enrichment-progress-bar" ? 300 : 60;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      const found = document.querySelector<HTMLElement>(`[data-demo-id="${target}"]`);
      if (found) return found;
      await poll(100);
    }
    return null;
  }, []);

  const waitForActionInterval = useCallback(async (actionStartedAt: number, intervalMs: number) => {
    const scaledInterval = Math.round(intervalMs * speed);
    while (playingRef.current && Date.now() - actionStartedAt < scaledInterval) {
      await poll(Math.min(100, Math.max(0, scaledInterval - (Date.now() - actionStartedAt))));
    }
    return playingRef.current;
  }, []);

  const placeCaption = useCallback((rect: DOMRect) => {
    const margin = 16;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const card = captionRef.current;
    const width = card?.offsetWidth ?? Math.min(420, vw - margin * 2);
    const height = card?.offsetHeight ?? 200;
    const minTop = (document.querySelector(".nh-demo-browser-tabs") ? 34 + 32 : 34) + 12;
    const maxTop = Math.max(minTop, vh - 88 - height);
    const gap = 28;
    const clampLeft = (value: number) => Math.max(margin, Math.min(vw - width - margin, value));
    const clampTop = (value: number) => Math.max(minTop, Math.min(maxTop, value));
    const guard = { left: rect.left - 24, right: rect.right + 24, top: rect.top - 24, bottom: rect.bottom + 24 };
    const candidates = [
      { left: clampLeft(rect.right + gap), top: clampTop(rect.top - 8) },
      { left: clampLeft(rect.left - width - gap), top: clampTop(rect.top - 8) },
      { left: clampLeft(rect.left), top: clampTop(rect.bottom + gap) },
      { left: clampLeft(rect.left), top: clampTop(rect.top - height - gap) },
      { left: margin, top: minTop },
      { left: vw - width - margin, top: minTop },
      { left: margin, top: maxTop },
      { left: vw - width - margin, top: maxTop },
    ];
    const overlap = (c: { left: number; top: number }) => {
      const w = Math.min(c.left + width, guard.right) - Math.max(c.left, guard.left);
      const h = Math.min(c.top + height, guard.bottom) - Math.max(c.top, guard.top);
      return w > 0 && h > 0 ? w * h : 0;
    };
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const distance = (c: { left: number; top: number }) => Math.hypot(c.left + width / 2 - cx, c.top + height / 2 - cy);
    return candidates.reduce((best, c) => {
      const o = overlap(c);
      const bo = overlap(best);
      return o < bo || (o === bo && distance(c) < distance(best)) ? c : best;
    }, candidates[0]);
  }, []);

  const moveTo = useCallback(async (target: HTMLElement, quick = false, forceScroll = false) => {
    const cursor = document.querySelector<HTMLElement>(".nh-demo-cursor");
    cursor?.classList.toggle("is-quick-move", quick);
    const targetRect = target.getBoundingClientRect();
    const scroller = target.closest<HTMLElement>(".nh-demo-embedded-route");
    const viewportTop = scroller ? scroller.getBoundingClientRect().top : 0;
    const viewportBottom = scroller ? scroller.getBoundingClientRect().bottom : window.innerHeight;
    const topObstruction = document.querySelector(".nh-demo-browser-tabs") ? 74 : 40;
    const bottomObstruction = CONTROLS_RESERVE + 8;
    const visible = targetRect.top >= viewportTop + topObstruction && targetRect.bottom <= viewportBottom - bottomObstruction;
    if (forceScroll || !visible) {
      const safeHeight = Math.max(1, (scroller ? scroller.clientHeight : window.innerHeight) - topObstruction - bottomObstruction);
      const targetOffset = Math.max(16, (safeHeight - targetRect.height) / 2);
      const targetTop = scroller ? targetRect.top - viewportTop + scroller.scrollTop : targetRect.top + window.scrollY;
      await animateScroll(scroller, targetTop - topObstruction - targetOffset, reducedMotion || speed < 1);
    }
    if (!playingRef.current) return;
    const rect = target.getBoundingClientRect();
    setPoint({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
    setCaptionPosition(placeCaption(rect));
    await delay(reducedMotion ? 0 : Math.round((quick ? 350 : 700) / 0.75));
    if (!quick && !reducedMotion) await delay(Math.round(250 / 0.75));
    cursor?.classList.remove("is-quick-move");
  }, [placeCaption, reducedMotion]);

  // Layout can shift after a click (menus opening, rows being added), so re-aim the cursor
  // and make sure the element is on screen immediately before every scripted click.
  const aimAt = useCallback(async (element: HTMLElement) => {
    const scroller = element.closest<HTMLElement>(".nh-demo-embedded-route");
    const cursor = document.querySelector<HTMLElement>(".nh-demo-cursor");
    cursor?.classList.add("is-quick-move");
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const rect = element.getBoundingClientRect();
      const top = (scroller ? scroller.getBoundingClientRect().top : 0) + (document.querySelector(".nh-demo-browser-tabs") ? 74 : 40);
      const bottom = (scroller ? scroller.getBoundingClientRect().bottom : window.innerHeight) - (CONTROLS_RESERVE + 8);
      if (rect.top < top || rect.bottom > bottom) { await moveTo(element, true, true); continue; }
      setPoint({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
      // Let the cursor finish travelling, then confirm the element did not shift underneath it.
      await delay(reducedMotion ? 0 : Math.round(650 / 0.75));
      const after = element.getBoundingClientRect();
      if (Math.abs(after.top - rect.top) < 1.5 && Math.abs(after.left - rect.left) < 1.5) break;
    }
    cursor?.classList.remove("is-quick-move");
  }, [moveTo, reducedMotion]);

  const navigateWithSwitch = useCallback(async (route: string, step: number, extra: Record<string, string> = {}) => {
    const onListing = (path: string) => path.startsWith("/demo/listing/");
    if (onListing(window.location.pathname) !== onListing(route)) {
      setSwitching(onListing(route) ? "listing" : "nearhome");
      await delay(reducedMotion ? 1200 : Math.round(2400 / 0.75));
      router.push(demoRoute(route, step, extra));
      await delay(450);
      setSwitching(null);
    } else router.push(demoRoute(route, step, extra));
  }, [reducedMotion, router]);

  const advance = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    if (fastTargetRef.current === null) speed = 1;
    setPlaying(true);
    setStepError(null);
    playingRef.current = true;
    document.querySelectorAll(".nh-demo-highlight").forEach((element) => element.classList.remove("nh-demo-highlight"));
    const current = DEMO_STEPS[indexRef.current];
    if (!current) { busy.current = false; return; }
    try {
      if (window.location.pathname !== current.route) {
        await navigateWithSwitch(current.route, indexRef.current);
        await playbackDelay(650);
      }
      const progress = actionProgress.current[current.id];
      let targetId = current.target;
      if (current.id === "destination" && ["opened", "query-entered"].includes(progress ?? "")) targetId = "destination-query";
      if (current.id === "destination" && progress === "place-selected") targetId = "destination-done";
      const priorityStep = PRIORITY_STEPS[current.id];
      const flatCountBefore = (JSON.parse(localStorage.getItem("nearhome_interactive_demo_state") ?? "{}") as { listings?: unknown[] }).listings?.length ?? 0;
      if (priorityStep && progress === "added") targetId = "priority-picker";
      if (priorityStep && progress === "picker") targetId = priorityStep.option;
      if (["review-flat-1", "confirm-flat-1"].includes(current.id) && !document.querySelector("[data-demo-id='confirm-listing']")) {
        // Arrived here by jumping: the pasted-listing review form lives in page state, so rebuild it.
        const box = await findTarget("listing-text");
        if (box instanceof HTMLTextAreaElement) {
          setInputValue(box, window.sessionStorage.getItem("nearhome_demo_clipboard") || "HDB resale listing 1");
          await delay(50);
          (await findTarget("add-flat"))?.click();
          await findTarget("confirm-listing");
        }
      }
      const target = progress === "extracted" || progress === "confirmed" ? null : await findTarget(targetId);
      const completedTargetAction = current.action === "paste" && ["extracted", "confirmed"].includes(progress ?? "");
      if (current.target && !target && !completedTargetAction && !(current.action === "wait" && speed < 1)) throw new Error(`Could not find the demo target: ${current.target}`);
      if (target) {
        await moveTo(target);
        if (!playingRef.current) { busy.current = false; return; }
        target.classList.add("nh-demo-highlight");
        if (current.id === "results-fair-price") {
          target.querySelector("details")?.setAttribute("open", "");
        }
      }
      if (current.action === "type" && target instanceof HTMLInputElement && current.value) {
        target.focus();
        const resumeAt = target.value && current.value.startsWith(target.value) ? target.value.length : 0;
        if (!resumeAt) setInputValue(target, "");
        for (const character of current.value.slice(resumeAt)) {
          if (!playingRef.current) break;
          setInputValue(target, target.value + character);
          await delay(reducedMotion ? 8 : Math.round(40 / 0.75));
        }
        if (current.id === "budget") updateDemoForm({ max_budget: Number(target.value) });
        if (current.id === "school-search" && playingRef.current) {
          const suggestion = await findTarget("school-suggestion");
          target.classList.remove("nh-demo-highlight");
          if (suggestion) {
            await moveTo(suggestion, true, true);
            if (!playingRef.current) { busy.current = false; return; }
            suggestion.classList.add("nh-demo-highlight");
            await aimAt(suggestion);
            if (!playingRef.current) { busy.current = false; return; }
            suggestion.click();
            suggestion.classList.remove("nh-demo-highlight");
            updateDemoForm({ named_schools: ["Ang Mo Kio Primary School"] });
            await delay(reducedMotion ? 20 : Math.round(300 / 0.75));
          }
        }
      } else if (current.action === "click" && target) {
        if (current.id === "run") {
          target.click();
          for (let attempt = 0; attempt < 50 && !window.location.pathname.endsWith("/comparison"); attempt += 1) await poll(100);
        } else if (priorityStep) {
          const ids = ["priority-add", "priority-picker", priorityStep.option];
          for (let i = Math.max(0, PRIORITY_STAGES.indexOf(progress ?? "start")); i < ids.length; i += 1) {
            let element: HTMLElement | null = target;
            if (element && i > Math.max(0, PRIORITY_STAGES.indexOf(progress ?? "start"))) element = null;
            if (!element) {
              element = await findTarget(ids[i]);
              if (!element) throw new Error(`Could not find the demo target: ${ids[i]}`);
              await moveTo(element, true, true);
              if (!playingRef.current) { busy.current = false; return; }
              element.classList.add("nh-demo-highlight");
            }
            if (!reducedMotion) {
              document.querySelector(".nh-demo-cursor")?.classList.add("nh-demo-pulse");
              window.setTimeout(() => document.querySelector(".nh-demo-cursor")?.classList.remove("nh-demo-pulse"), 427);
            }
            await aimAt(element);
            if (!playingRef.current) { busy.current = false; return; }
            element.click();
            actionProgress.current[current.id] = PRIORITY_STAGES[i + 1] ?? "done";
            await playbackDelay(450);
            element.classList.remove("nh-demo-highlight");
            if (!playingRef.current) { busy.current = false; return; }
          }
          updateDemoForm({ priorities: priorityStep.selection });
        } else if (current.id === "destination" && ["opened", "query-entered"].includes(progress ?? "")) {
          // Resuming a paused destination step continues in its already-open field.
        } else if (current.id === "destination" && progress === "place-selected") {
          document.querySelector<HTMLButtonElement>("[data-demo-id='destination-done']")?.click();
        } else {
          if (current.id === "confirm-flat-1" && target instanceof HTMLButtonElement) for (let attempt = 0; target.disabled && attempt < 30; attempt += 1) await poll(100);
          if (!(current.id === "schools" && progress === "toggled")) { await aimAt(target); if (!playingRef.current) { busy.current = false; return; } target.click(); }
          if (current.id === "destination") actionProgress.current[current.id] = "opened";
          if (current.id === "schools") actionProgress.current[current.id] = "toggled";
          if (current.id === "transport") updateDemoForm({ main_transport_mode: "BOTH" });
          if (current.id === "schools") updateDemoForm({ schools_matter: true });
        }
        if (!reducedMotion) {
          document.querySelector(".nh-demo-cursor")?.classList.add("nh-demo-pulse");
          window.setTimeout(() => document.querySelector(".nh-demo-cursor")?.classList.remove("nh-demo-pulse"), 427);
        }
        await playbackDelay(350);
        if (!playingRef.current) { target.classList.remove("nh-demo-highlight"); busy.current = false; return; }
        if (current.id === "destination" && actionProgress.current[current.id] !== "place-selected") {
          const address = await findTarget("destination-query");
          if (address instanceof HTMLInputElement) {
            await moveTo(address, true, true);
            if (!playingRef.current) { busy.current = false; return; }
            address.classList.add("nh-demo-highlight");
            address.focus();
            const query = "Raffles Place";
            const resumeAt = address.value && query.startsWith(address.value) ? address.value.length : 0;
            if (!resumeAt) setInputValue(address, "");
            for (const character of query.slice(resumeAt)) {
              if (!playingRef.current) break;
              setInputValue(address, address.value + character);
              await delay(reducedMotion ? 8 : Math.round(40 / 0.75));
            }
            if (!playingRef.current) { busy.current = false; return; }
            actionProgress.current[current.id] = "query-entered";
            const suggestion = await findTarget("destination-suggestion");
            address.classList.remove("nh-demo-highlight");
            if (suggestion) {
              await moveTo(suggestion, true, true);
              if (!playingRef.current) { busy.current = false; return; }
              suggestion.classList.add("nh-demo-highlight");
              await aimAt(suggestion);
              if (!playingRef.current) { busy.current = false; return; }
              suggestion.click();
              actionProgress.current[current.id] = "place-selected";
              await delay(reducedMotion ? 20 : Math.round(250 / 0.75));
            }
            const done = await findTarget("destination-done");
            if (done && playingRef.current) {
              await moveTo(done, true, true);
              if (!playingRef.current) { busy.current = false; return; }
              done.classList.add("nh-demo-highlight");
              await aimAt(done);
              if (!playingRef.current) { busy.current = false; return; }
              done.click();
              done.classList.remove("nh-demo-highlight");
            }
            updateDemoForm({ important_location: { label: "Work", place_id: "demo-place", description: "Raffles Place, Singapore", main_text: "Raffles Place", formatted_address: "Raffles Place, Singapore", latitude: 1.284, longitude: 103.852, usual_day_type: "WEEKDAY", departure_time_local: "08:00:00", transport_mode: "PUBLIC_TRANSPORT" } });
          }
        }
        if (current.id === "confirm-flat-1") await waitForDemoFlatCount(flatCountBefore + 1).catch(() => undefined);
        if (current.id === "save-profile") await delay(reducedMotion ? 30 : Math.round(450 / 0.75));
      } else if (current.action === "copy" && target) {
        target.classList.add("nh-demo-selected");
        const listingPage = document.querySelector<HTMLElement>(".nh-demo-listing-page") ?? target;
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(listingPage);
        selection?.removeAllRanges();
        selection?.addRange(range);
        setKeys(["Ctrl + A"]);
        await delay(reducedMotion ? 50 : Math.round(450 / 0.75));
        if (playingRef.current) {
          window.sessionStorage.setItem("nearhome_demo_clipboard", listingPage.innerText);
          setKeys(["Ctrl + C"]);
          await delay(reducedMotion ? 50 : Math.round(450 / 0.75));
        }
        selection?.removeAllRanges();
      } else if (current.action === "paste") {
        const textarea = target instanceof HTMLTextAreaElement ? target : null;
        if (actionProgress.current[current.id] === "confirmed") {
          const confirmedAt = actionTimes.current[`${current.id}:confirmed`];
          if (confirmedAt && !await waitForActionInterval(confirmedAt, 3_000)) { busy.current = false; return; }
        } else if (["extracting", "extracted"].includes(actionProgress.current[current.id] ?? "")) {
          const confirmButton = await findTarget("confirm-listing");
          if (confirmButton instanceof HTMLButtonElement && playingRef.current && current.value !== "1") {
            if (actionProgress.current[current.id] !== "confirmed") {
              actionProgress.current[current.id] = "extracted";
              for (let attempt = 0; confirmButton.disabled && attempt < 30; attempt += 1) await poll(100);
              if (!playingRef.current) { busy.current = false; return; }
              await moveTo(confirmButton, true, true);
              if (!playingRef.current) { busy.current = false; return; }
              const addClickedAt = actionTimes.current[`${current.id}:add`] ?? Date.now();
              if (!await waitForActionInterval(addClickedAt, 3_000)) { busy.current = false; return; }
              confirmButton.classList.add("nh-demo-highlight");
              confirmButton.classList.add("nh-demo-click-feedback");
              window.setTimeout(() => confirmButton.classList.remove("nh-demo-click-feedback"), 600);
              const currentFlatCount = (JSON.parse(localStorage.getItem("nearhome_interactive_demo_state") ?? "{}") as { listings?: unknown[] }).listings?.length ?? 0;
              actionTimes.current[`${current.id}:confirmed`] = Date.now();
              confirmButton.click();
              actionProgress.current[current.id] = "confirmed";
              await waitForDemoFlatCount(currentFlatCount + 1);
              if (!await waitForActionInterval(actionTimes.current[`${current.id}:confirmed`], 3_000)) { busy.current = false; return; }
            }
          }
        } else if (textarea) {
          const listingNumber = current.value ?? "1";
          const copied = window.sessionStorage.getItem("nearhome_demo_clipboard") || `HDB resale listing ${listingNumber}\n${listingNumber === "1" ? "Blk 812 Tampines Avenue 4" : "Blk 166 Stirling Road"}\n4 ROOM · S$${listingNumber === "1" ? "638,000" : "715,000"} · 93 sqm · 82 years lease`;
          setKeys(["Ctrl + V"]);
          textarea.focus();
          setInputValue(textarea, copied);
          actionTimes.current[`${current.id}:pasted`] ??= Date.now();
          await delay(0);
          if (!playingRef.current) { textarea.classList.remove("nh-demo-highlight"); busy.current = false; return; }
          const addButton = await findTarget("add-flat");
          if (!(addButton instanceof HTMLButtonElement)) throw new Error("Could not find the Add a flat button.");
          await moveTo(addButton, true, true);
          if (!playingRef.current) { busy.current = false; return; }
          if (!await waitForActionInterval(actionTimes.current[`${current.id}:pasted`], 3_000)) { busy.current = false; return; }
          addButton.classList.add("nh-demo-highlight");
          addButton.classList.add("nh-demo-click-feedback");
          window.setTimeout(() => addButton.classList.remove("nh-demo-click-feedback"), 600);
          if (!reducedMotion) {
            document.querySelector(".nh-demo-cursor")?.classList.add("nh-demo-pulse");
            window.setTimeout(() => document.querySelector(".nh-demo-cursor")?.classList.remove("nh-demo-pulse"), 427);
          }
          actionProgress.current[current.id] = "extracting";
          actionTimes.current[`${current.id}:add`] = Date.now();
          addButton.click();
          const confirmButton = await findTarget("confirm-listing");
          if (confirmButton) actionProgress.current[current.id] = "extracted";
          if (!playingRef.current) { textarea.classList.remove("nh-demo-highlight"); busy.current = false; return; }
          if (current.value === "1") {
            // Flat one stops at the review form so the walkthrough can show a flagged field.
            const flagged = await findTarget("flag-lease");
            if (flagged && playingRef.current) await moveTo(flagged, true, true);
          } else if (confirmButton instanceof HTMLButtonElement) {
            for (let attempt = 0; confirmButton.disabled && attempt < 30; attempt += 1) await poll(100);
            if (!playingRef.current) { busy.current = false; return; }
            await moveTo(confirmButton, true, true);
            if (!playingRef.current) { busy.current = false; return; }
            if (!await waitForActionInterval(actionTimes.current[`${current.id}:add`], 3_000)) { busy.current = false; return; }
            confirmButton.classList.add("nh-demo-highlight");
            confirmButton.classList.add("nh-demo-click-feedback");
            window.setTimeout(() => confirmButton.classList.remove("nh-demo-click-feedback"), 600);
            const currentFlatCount = (JSON.parse(localStorage.getItem("nearhome_interactive_demo_state") ?? "{}") as { listings?: unknown[] }).listings?.length ?? 0;
            actionTimes.current[`${current.id}:confirmed`] = Date.now();
            confirmButton.click();
            actionProgress.current[current.id] = "confirmed";
            await waitForDemoFlatCount(currentFlatCount + 1);
            if (!await waitForActionInterval(actionTimes.current[`${current.id}:confirmed`], 3_000)) { busy.current = false; return; }
          }
        }
      } else if (current.action === "wait") {
        const endAt = Date.now() + Math.round(((current.holdMs ?? 1000) / 0.75) * speed);
        while (playingRef.current && Date.now() < endAt) await poll(50);
      }
      if (!playingRef.current) {
        if (indexRef.current !== DEMO_STEPS.length - 1) target?.classList.remove("nh-demo-highlight", "nh-demo-selected");
        busy.current = false;
        return;
      }
      if (indexRef.current !== DEMO_STEPS.length - 1) target?.classList.remove("nh-demo-highlight", "nh-demo-selected");
      const nextIndex = Math.min(indexRef.current + 1, DEMO_STEPS.length - 1);
      saveStepSnapshot(nextIndex);
      delete actionProgress.current[current.id];
      Object.keys(actionTimes.current).filter((key) => key.startsWith(`${current.id}:`)).forEach((key) => delete actionTimes.current[key]);
      indexRef.current = nextIndex;
      setStepIndex(nextIndex);
      busy.current = false;
      const fastTarget = fastTargetRef.current;
      const fastDone = fastTarget !== null && nextIndex >= fastTarget;
      if (fastDone) { fastTargetRef.current = null; speed = 1; }
      const shouldPause = fastDone || (fastTarget === null && current.pauseAfter && pauseRef.current);
      const nextStep = DEMO_STEPS[nextIndex];
      if (current.id === "run" && nextStep) {
        router.replace(demoRoute(nextStep.route, nextIndex, { run: "1" }), { scroll: false });
      }
      if (current.id === "results-loading" && nextStep) {
        router.replace(demoRoute(nextStep.route, nextIndex), { scroll: false });
      }
      if (nextStep && current.id !== "run" && current.id !== "results-loading") {
        const currentParams = new URLSearchParams(window.location.search);
        const routeNeedsDemoState = window.location.pathname !== nextStep.route
          || currentParams.get("demo") !== "1"
          || currentParams.get("demoStep") !== String(nextIndex)
          || (currentParams.get("demoEmbed") === "1" && new URLSearchParams(demoRoute(nextStep.route, nextIndex).split("?")[1]).get("demoEmbed") !== "1");
        if (routeNeedsDemoState) {
          if (window.location.pathname === nextStep.route) {
            // Same page, new step: only update the URL. A real navigation would remount the page and drop its in-progress state.
            window.history.replaceState(window.history.state, "", demoRoute(nextStep.route, nextIndex));
          } else await navigateWithSwitch(nextStep.route, nextIndex);
          if (shouldPause) await delay(reducedMotion ? 60 : Math.round(700 / 0.75));
        }
      }
      if (shouldPause || current.id === DEMO_STEPS[DEMO_STEPS.length - 1]?.id) {
        setPlaying(false);
        playingRef.current = false;
      } else if (playingRef.current) {
        await playbackDelay(250);
        advanceRef.current?.();
      }
    } catch (error) {
      busy.current = false;
      fastTargetRef.current = null;
      speed = 1;
      setSwitching(null);
      setPlaying(false);
      playingRef.current = false;
      setStepError(error instanceof Error ? error.message : "The walkthrough could not complete this step.");
    }
  }, [aimAt, findTarget, moveTo, navigateWithSwitch, reducedMotion, router, waitForActionInterval]);

  useEffect(() => { advanceRef.current = advance; }, [advance]);

  const endFast = () => { fastTargetRef.current = null; speed = 1; };
  const jumpTo = (requestedIndex: number) => {
    endFast();
    setSwitching(null);
    document.querySelectorAll(".nh-demo-highlight").forEach((element) => element.classList.remove("nh-demo-highlight", "nh-demo-selected"));
    const nextIndex = Math.max(0, Math.min(DEMO_STEPS.length - 1, requestedIndex));
    const fixtureSnapshot = localStorage.getItem(snapshotKey(nextIndex));
    restoreDemoFixtures(fixtureSnapshot === "__EMPTY__" || fixtureSnapshot === null ? null : fixtureSnapshot);
    actionProgress.current = {};
    actionTimes.current = {};
    const formSnapshot = localStorage.getItem(`${snapshotKey(nextIndex)}_form`);
    if (formSnapshot === null) localStorage.removeItem(formKey); else localStorage.setItem(formKey, formSnapshot);
    indexRef.current = nextIndex;
    setStepIndex(nextIndex);
    setBackRevision((revision) => revision + 1);
    setPlaying(false);
    playingRef.current = false;
    queryClient.clear();
    router.push(demoRoute(DEMO_STEPS[nextIndex].route, nextIndex));
  };
  // Skipping forward quietly performs every step in between at high speed, so the
  // sample data, form and route all match the step the person lands on.
  const skipTo = async (requestedIndex: number) => {
    const targetIndex = Math.max(0, Math.min(DEMO_STEPS.length - 1, requestedIndex));
    if (targetIndex < indexRef.current) { jumpTo(targetIndex); return; }
    if (targetIndex === indexRef.current) return;
    if (playingRef.current || busy.current) {
      playingRef.current = false;
      setPlaying(false);
      for (let attempt = 0; busy.current && attempt < 60; attempt += 1) await poll(100);
    }
    setStepError(null);
    fastTargetRef.current = targetIndex;
    speed = FAST_SPEED;
    void advance();
  };
  const next = () => { void skipTo(indexRef.current + 1); };
  const back = () => jumpTo(indexRef.current - 1);
  const togglePlay = () => {
    if (playingRef.current) {
      const cursor = document.querySelector<HTMLElement>(".nh-demo-cursor");
      if (cursor) {
        const rect = cursor.getBoundingClientRect();
        setPoint({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
      }
      playingRef.current = false;
      setPlaying(false);
      endFast();
    } else void advance();
  };

  useEffect(() => {
    // The caption's height changes with its text; keep it clear of the control bar and top bars.
    const card = captionRef.current;
    if (!card) return;
    if (!captionPosition.top) return;
    const minTop = (document.querySelector(".nh-demo-browser-tabs") ? 66 : 34) + 12;
    const maxTop = Math.max(minTop, window.innerHeight - CONTROLS_RESERVE - card.offsetHeight);
    if (captionPosition.top > maxTop + 1) setCaptionPosition((position) => ({ ...position, top: maxTop }));
  }, [stepIndex, captionPosition, playing, stepError, switching]);
  useEffect(() => { playingRef.current = playing; }, [playing]);
  useEffect(() => { pauseRef.current = pauseAtPages; }, [pauseAtPages]);
  useEffect(() => { indexRef.current = stepIndex; }, [stepIndex]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!active) return;
      if (event.code === "Space" && !/INPUT|TEXTAREA|SELECT/.test((event.target as HTMLElement).tagName)) { event.preventDefault(); togglePlay(); }
      if (event.key === "ArrowRight") next();
      if (event.key === "ArrowLeft") back();
      if (event.key === "Escape") exit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const activeDemoSection = step ? sectionOf(step) : 0;

  return <>
    <div key={backRevision} className={embeddedMode && active ? "nh-demo-embedded-shell" : "nh-demo-page-content"}>
      {embeddedMode && active && <aside className="nh-demo-sidebar" aria-label="Walkthrough sections">
        <span className="nh-demo-sidebar-brand">nearhome<span>.</span></span>
        <p className="nh-demo-sidebar-kicker">YOUR WALKTHROUGH</p>
        <ol>{demoSections.map((label, index) => <li key={label} className={index === activeDemoSection ? "is-current" : index < activeDemoSection ? "is-complete" : ""} aria-current={index === activeDemoSection ? "step" : undefined}>
          <button type="button" className="nh-demo-sidebar-link" onClick={() => { void skipTo(sectionStarts[index]); }} aria-label={`Go to ${label}`}>
            <span className="nh-demo-sidebar-marker">{index < activeDemoSection ? "✓" : index + 1}</span><span>{label}</span>
          </button>
        </li>)}</ol>
        <p className="nh-demo-sidebar-note">Move through the real NearHome flow at your own pace.</p>
      </aside>}
      <div className={embeddedMode && active ? "nh-demo-embedded-route" : "nh-demo-page-content"}>{children}</div>
    </div>
    {analyticsAllowed && !active && <Analytics />}
    {active && <>
      <div className="nh-demo-topbar"><span><i aria-hidden="true">●</i> Demo mode · Explore NearHome with sample data</span><button onClick={exit}>Exit demo <span aria-hidden="true">↗</span></button></div>
      {step?.stage === 3 && <div className="nh-demo-browser-tabs" aria-label="Demo browser tabs">
        <span className={pathname.startsWith("/demo/listing/") ? "" : "active"} aria-current={pathname.startsWith("/demo/listing/") ? undefined : "page"}>NearHome</span>
        <span className={pathname.startsWith("/demo/listing/") ? "active" : ""} aria-current={pathname.startsWith("/demo/listing/") ? "page" : undefined}>Property listing site</span>
        <small>{pathname.startsWith("/demo/listing/") ? "Switch to Property listing site" : "Switch back to NearHome"}</small>
      </div>}
      <div className={`nh-demo-cursor ${playing ? "is-moving" : ""} ${!playing ? "is-paused" : ""} ${reducedMotion ? "is-reduced" : ""}`} style={{ left: point.x, top: point.y }} aria-hidden="true" />
      {keys.length > 0 && <div className="nh-demo-keycaps" onAnimationEnd={() => setKeys([])}>{keys.map((key) => <kbd key={key}>{key}</kbd>)}</div>}
      {switching && <div className="nh-demo-switch" role="status" aria-live="assertive">
        <div className="nh-demo-switch-card">
          <p className="nh-demo-switch-eyebrow">{switching === "listing" ? "OPEN A NEW TAB" : "BACK TO NEARHOME"}</p>
          <div className="nh-demo-switch-tabs" aria-hidden="true">
            <span className={switching === "listing" ? "" : "active"}>NearHome</span>
            <b>→</b>
            <span className={switching === "listing" ? "active" : ""}>Property listing site</span>
          </div>
          <strong>{switching === "listing" ? "Switch to your property listing site" : "Switch back to NearHome"}</strong>
          <span>{switching === "listing" ? "In real life, you would open a property site in another browser tab, find a flat you like and copy its listing. We'll use a sample listing here." : "Paste what you copied. NearHome reads the price, floor area and lease for you to review."}</span>
        </div>
      </div>}
      {!switching && <aside ref={captionRef} className="nh-demo-caption" style={{ left: captionPosition.left || undefined, top: captionPosition.top || undefined, right: captionPosition.left ? "auto" : undefined, bottom: captionPosition.top ? "auto" : undefined }} aria-live="polite"><p>STEP {stepIndex + 1} · {(["HOME", "SETUP", "ADD FLATS", "COMPARE", "RESULTS"][step?.stage - 1] ?? "DEMO")}</p><strong>{step?.caption}</strong><span>{step?.why}</span>{stepError && <small>{stepError}</small>}{!playing && !stepError && <small>{step?.action === "wait" ? "Paused" : "Paused · take a look around, then continue when ready"}</small>}</aside>}
      <nav className="nh-demo-controls" aria-label="Demo controls">
        <div className="nh-demo-controls-row">
          <button type="button" className="nh-demo-skip" onClick={back} disabled={stepIndex === 0} aria-label="Previous step">← Back</button>
          <div className="nh-demo-progress">
            <span className="nh-demo-step-count">Step {stepIndex + 1} of {DEMO_STEPS.length}</span>
            <div className="nh-demo-dots" role="group" aria-label="Demo steps">{DEMO_STEPS.map((demoStep, index) => <button key={demoStep.id} aria-label={`Go to step ${index + 1}: ${demoStep.caption}`} aria-current={stepIndex === index ? "step" : undefined} className={stepIndex === index ? "active" : ""} onClick={() => { void skipTo(index); }} />)}</div>
          </div>
          <button type="button" className="nh-demo-skip" onClick={next} disabled={stepIndex >= DEMO_STEPS.length - 1} aria-label="Next step">Next →</button>
        </div>
        <div className="nh-demo-controls-row nh-demo-controls-row-play">
          <button type="button" className={`nh-demo-play ${playing ? "is-playing" : "is-paused"}`} onClick={togglePlay} aria-label={playing ? "Pause walkthrough" : "Play walkthrough"}><span aria-hidden="true">{playing ? "❚❚" : "▶"}</span> {playing ? "Pause" : "Play"}</button>
          <label className="nh-demo-pause-toggle"><input type="checkbox" checked={pauseAtPages} onChange={(event) => setPauseAtPages(event.target.checked)} /> Pause at each page</label>
        </div>
      </nav>
    </>}
  </>;
}
