"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  addManualListing,
  confirmListing,
  discardListingInput,
  deleteListing,
  geocodeAddress,
  getComparison,
  getSession,
  saveBuyerProfile,
  smartPaste,
  startEnrichment,
} from "@/lib/api";
import type { GeocodeSuggestion, SessionListing } from "@/lib/api";
import { buildSmartPasteRequest } from "@/lib/smart-paste";
import { waitForEnrichmentComplete } from "@/lib/wait-for-enrichment";
import { ComparisonView } from "@/components/comparison-view";
import { SmartPasteProgress } from "@/components/smart-paste-progress";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { EnrichmentProgress } from "@/components/enrichment-progress";
import { WorkflowStepper } from "@/components/workflow-stepper";

const PRIORITY_VALUES = [
  "AFFORDABILITY",
  "SPACE",
  "LEASE",
  "PUBLIC_TRANSPORT",
  "DRIVING",
  "SCHOOLS",
  "FAIR_PRICE",
] as const;

const PRIORITY_LABELS: Record<(typeof PRIORITY_VALUES)[number], string> = {
  AFFORDABILITY: "Affordability",
  SPACE: "Space",
  LEASE: "Remaining lease",
  PUBLIC_TRANSPORT: "Public transport",
  DRIVING: "Driving",
  SCHOOLS: "Schools",
  FAIR_PRICE: "Fair price",
};

const SCHOOL_NAME_PATTERN = /\b(school|college|institution|institute|academy|madrasah|polytechnic)\b/i;

const priorityValueSchema = z.enum(PRIORITY_VALUES);
const optionalPrioritySchema = z.union([priorityValueSchema, z.literal("")]);
const optionalPositiveNumber = z.preprocess(
  (value) => (value === "" || value == null ? undefined : value),
  z.coerce.number().positive().optional(),
);

const profileSchema = z.object({
  max_budget: z.coerce.number().positive(),
  main_transport_mode: z.enum(["MAINLY_PUBLIC_TRANSPORT", "MAINLY_DRIVING", "BOTH"]),
  priority_1: priorityValueSchema,
  priority_2: optionalPrioritySchema,
  priority_3: optionalPrioritySchema,
});

const listingSchema = z.object({
  display_name: z.string().optional(),
  address: z.string().min(3),
  asking_price: z.coerce.number().positive(),
  floor_area_sqm: z.coerce.number().positive(),
  flat_type: z.string().min(2),
  flat_type_raw: z.string().optional(),
  listing_flat_subtype: z.string().optional(),
  flat_model: z.string().optional(),
  storey_range: z.string().optional(),
  remaining_lease_years: optionalPositiveNumber,
});

type ProfileForm = z.infer<typeof profileSchema>;
type ListingForm = z.infer<typeof listingSchema>;
type SegmentOption<T extends string> = { value: T; label: string };

function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: SegmentOption<T>[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label={label}>
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={selected}
            onClick={() => onChange(option.value)}
            className={`min-h-11 flex-1 rounded-lg border px-4 text-sm font-medium transition focus:outline-none focus:ring-2 focus:ring-teal-600 focus:ring-offset-2 sm:flex-none ${selected ? "border-teal-700 bg-teal-50 text-teal-900 shadow-sm" : "border-slate-300 bg-white text-slate-700 hover:border-teal-400 hover:bg-slate-50"}`}
          >
            {selected && <span aria-hidden="true">✓ </span>}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

function displayTime(value: string) {
  const [hours, minutes] = value.split(":").map(Number);
  if (!Number.isFinite(hours) || !Number.isFinite(minutes)) return value;
  const suffix = hours >= 12 ? "PM" : "AM";
  return `${hours % 12 || 12}:${String(minutes).padStart(2, "0")} ${suffix}`;
}

function isSchoolSuggestion(suggestion: GeocodeSuggestion) {
  return SCHOOL_NAME_PATTERN.test(`${suggestion.main_text} ${suggestion.description}`);
}

export default function SessionPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;
  const qc = useQueryClient();
  const [inputMode, setInputMode] = useState<"manual" | "paste">("manual");
  const [pasteText, setPasteText] = useState("");
  const [listingInputId, setListingInputId] = useState<string | null>(null);
  const [pasteEvidence, setPasteEvidence] = useState<Record<string, Array<{ value: unknown; source_snippet?: string }>>>({});
  const [pasteError, setPasteError] = useState<string | null>(null);
  const [pasteSourceUrl, setPasteSourceUrl] = useState<string | null>(null);
  const pasteInputRef = useRef<HTMLTextAreaElement>(null);
  const manualEntryTabRef = useRef<HTMLButtonElement>(null);
  const smartPasteTabRef = useRef<HTMLButtonElement>(null);
  const pasteGenerationRef = useRef(0);

  const [locLabel, setLocLabel] = useState("Work");
  const [locQuery, setLocQuery] = useState("");
  const [locSuggestions, setLocSuggestions] = useState<GeocodeSuggestion[]>([]);
  const [selectedPlace, setSelectedPlace] = useState<GeocodeSuggestion | null>(null);
  const [locSearchError, setLocSearchError] = useState<string | null>(null);
  const [locSearchPending, setLocSearchPending] = useState(false);
  const [locSearchAttempt, setLocSearchAttempt] = useState(0);
  const locSearchAbortRef = useRef<AbortController | null>(null);
  const [locDay, setLocDay] = useState<"WEEKDAY" | "WEEKEND">("WEEKDAY");
  const [locTime, setLocTime] = useState("08:00");
  const [locMode, setLocMode] = useState<"PUBLIC_TRANSPORT" | "DRIVING" | "BOTH">("PUBLIC_TRANSPORT");
  const [destinationOpen, setDestinationOpen] = useState(false);
  const [destinationRemovalPending, setDestinationRemovalPending] = useState(false);
  const [schoolsMatter, setSchoolsMatter] = useState(false);
  const [namedSchools, setNamedSchools] = useState<string[]>([]);
  const [schoolQuery, setSchoolQuery] = useState("");
  const [schoolSuggestions, setSchoolSuggestions] = useState<GeocodeSuggestion[]>([]);
  const [schoolSearchError, setSchoolSearchError] = useState<string | null>(null);
  const [schoolSearchPending, setSchoolSearchPending] = useState(false);
  const schoolSearchAbortRef = useRef<AbortController | null>(null);
  const [profileSaved, setProfileSaved] = useState(false);
  const [listingSavedMsg, setListingSavedMsg] = useState<string | null>(null);
  const [pasteWarnings, setPasteWarnings] = useState<string[]>([]);
  const [pasteFieldSources, setPasteFieldSources] = useState<Record<string, string>>({});
  const [pasteInitialCanonical, setPasteInitialCanonical] = useState<{ flat_type?: string; flat_model?: string }>({});
  const [pendingRemoval, setPendingRemoval] = useState<SessionListing | null>(null);
  const shortlistHeadingRef = useRef<HTMLHeadingElement>(null);
  const addFlatHeadingRef = useRef<HTMLHeadingElement>(null);

  const sessionQuery = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => getSession(sessionId),
  });

  const savedListings = sessionQuery.data?.listings ?? [];
  const listingCount = sessionQuery.data?.listing_count ?? 0;
  const isDemo = sessionQuery.data?.demo_mode ?? false;

  const comparisonQuery = useQuery({
    queryKey: ["comparison", sessionId],
    queryFn: ({ signal }) => getComparison(sessionId, signal),
    enabled: listingCount >= 2,
    refetchInterval: 8000,
  });

  const profileForm = useForm<ProfileForm>({
    resolver: zodResolver(profileSchema),
    mode: "onChange",
    defaultValues: {
      max_budget: 730000,
      main_transport_mode: "MAINLY_PUBLIC_TRANSPORT",
      priority_1: "AFFORDABILITY",
      priority_2: "",
      priority_3: "",
    },
  });

  const listingForm = useForm<ListingForm>({
    resolver: zodResolver(listingSchema),
    mode: "onChange",
    defaultValues: { flat_type: "4 ROOM", storey_range: "" },
  });

  useEffect(() => {
    const profile = sessionQuery.data?.buyer_profile;
    if (!profile) return;

    setProfileSaved(true);
    setSchoolsMatter(profile.schools_matter);
    setNamedSchools(
      profile.named_schools?.length
        ? profile.named_schools
        : profile.named_school
          ? [profile.named_school]
        : [],
    );
    const savedLocation = profile.important_locations?.[0];
    if (savedLocation) {
      setLocLabel(savedLocation.label);
      setLocQuery(savedLocation.formatted_address ?? "");
      setLocDay(savedLocation.usual_day_type ?? "WEEKDAY");
      setLocMode((savedLocation.transport_mode as typeof locMode | null) ?? "PUBLIC_TRANSPORT");
      setLocTime(savedLocation.departure_time_local?.slice(0, 5) || "08:00");
      if (
        savedLocation.place_id &&
        savedLocation.formatted_address &&
        savedLocation.latitude != null &&
        savedLocation.longitude != null
      ) {
        setSelectedPlace({
          place_id: savedLocation.place_id,
          description: savedLocation.formatted_address,
          main_text: savedLocation.label,
          formatted_address: savedLocation.formatted_address,
          latitude: savedLocation.latitude,
          longitude: savedLocation.longitude,
        });
      }
      setDestinationOpen(false);
    }

    const savedPriorities = profile.priorities ?? [];
    const validPriorities = savedPriorities.filter(
      (value): value is (typeof PRIORITY_VALUES)[number] =>
        PRIORITY_VALUES.includes(value as (typeof PRIORITY_VALUES)[number]),
    );
    profileForm.reset({
      max_budget: profile.max_budget,
      main_transport_mode: profile.main_transport_mode as ProfileForm["main_transport_mode"],
      priority_1: validPriorities[0] ?? "AFFORDABILITY",
      priority_2: validPriorities[1] ?? "",
      priority_3: validPriorities[2] ?? "",
    });
  }, [profileForm, sessionQuery.data]);

  const selectedPriorities = profileForm.watch(["priority_1", "priority_2", "priority_3"]);

  const orderedPriorities = selectedPriorities.filter(
    (value): value is (typeof PRIORITY_VALUES)[number] => Boolean(value),
  );

  function setOrderedPriorities(priorities: Array<(typeof PRIORITY_VALUES)[number]>) {
    profileForm.setValue("priority_1", priorities[0] ?? "AFFORDABILITY", { shouldValidate: true });
    profileForm.setValue("priority_2", priorities[1] ?? "", { shouldValidate: true });
    profileForm.setValue("priority_3", priorities[2] ?? "", { shouldValidate: true });
  }

  function replacePriority(index: number, value: (typeof PRIORITY_VALUES)[number]) {
    const next = [...orderedPriorities];
    next[index] = value;
    setOrderedPriorities(next);
  }

  function removePriority(index: number) {
    if (orderedPriorities.length <= 1) return;
    setOrderedPriorities(orderedPriorities.filter((_, priorityIndex) => priorityIndex !== index));
  }

  function movePriority(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= orderedPriorities.length) return;
    const next = [...orderedPriorities];
    [next[index], next[target]] = [next[target], next[index]];
    setOrderedPriorities(next);
  }

  const saveProfile = useMutation({
    mutationFn: (values: ProfileForm) => {
      if (locQuery.trim() && !selectedPlace) {
        throw new Error("Choose a confirmed location from the address results before saving the profile.");
      }
      if (schoolQuery.trim()) {
        throw new Error("Choose each named school from the Singapore address results before saving the profile.");
      }
      const important_locations = selectedPlace
        ? [
            {
              label: locLabel,
              place_id: selectedPlace.place_id,
              formatted_address: selectedPlace.formatted_address,
              latitude: selectedPlace.latitude,
              longitude: selectedPlace.longitude,
              usual_day_type: locDay,
              departure_time_local: locTime + ":00",
              transport_mode: locMode,
            },
          ]
        : [];
      return saveBuyerProfile(sessionId, {
        max_budget: values.max_budget,
        main_transport_mode: values.main_transport_mode,
        priorities: [values.priority_1, values.priority_2, values.priority_3]
          .filter(Boolean)
          .map((priority_type) => ({ priority_type })),
        important_locations,
        schools_matter: schoolsMatter,
        named_schools: namedSchools,
      });
    },
    onSuccess: () => {
      setProfileSaved(true);
      qc.invalidateQueries({ queryKey: ["session", sessionId] });
      qc.invalidateQueries({ queryKey: ["comparison", sessionId] });
    },
  });

  const addListing = useMutation({
    mutationFn: (values: ListingForm) => addManualListing(sessionId, values),
    onSuccess: async () => {
      listingForm.reset({ flat_type: "4 ROOM", storey_range: "" });
      setListingSavedMsg("Listing saved.");
      qc.invalidateQueries({ queryKey: ["session", sessionId] });
      qc.invalidateQueries({ queryKey: ["comparison", sessionId] });
    },
  });

  const pasteExtract = useMutation({
    mutationFn: () => smartPaste(sessionId, buildSmartPasteRequest(pasteText)),
    onMutate: () => {
      setPasteError(null);
      return { generation: pasteGenerationRef.current };
    },
    onSuccess: (data, _variables, context) => {
      if (context?.generation !== pasteGenerationRef.current) return;
      setListingInputId(data.listing_input_id);
      setPasteSourceUrl(data.sourceUrl ?? null);
      setPasteWarnings(data.extraction_warnings ?? []);
      setPasteFieldSources(data.field_sources ?? {});
      const fields: Record<string, unknown> = data.suggested_values ?? {};
      for (const [k, v] of Object.entries(data.candidates)) {
        if (!fields[k] && v[0]) fields[k] = v[0].value;
      }
      setPasteEvidence(data.evidence_by_field ?? {});
      setPasteInitialCanonical({
        flat_type: fields.flat_type ? String(fields.flat_type) : undefined,
        flat_model: fields.flat_model ? String(fields.flat_model) : undefined,
      });
      listingForm.reset({
        address: String(fields.address ?? ""),
        asking_price: Number(fields.asking_price ?? 0) || undefined,
        floor_area_sqm: Number(fields.floor_area_sqm ?? 0) || undefined,
        flat_type: String(fields.flat_type ?? ""),
        flat_type_raw: String(fields.flat_type_raw ?? fields.flat_type ?? ""),
        listing_flat_subtype: String(fields.listing_flat_subtype ?? ""),
        flat_model: String(fields.flat_model ?? ""),
        storey_range: "",
        remaining_lease_years: fields.remaining_lease_years ? Number(fields.remaining_lease_years) : undefined,
      });
      setInputMode("paste");
    },
    onError: (error, _variables, context) => {
      if (context?.generation === pasteGenerationRef.current) {
        setPasteError(error instanceof Error ? error.message : String(error));
      }
    },
  });

  function resetSmartPaste() {
    pasteGenerationRef.current += 1;
    setPasteText("");
    setListingInputId(null);
    setPasteEvidence({});
    setPasteWarnings([]);
    setPasteFieldSources({});
    setPasteInitialCanonical({});
    setPasteError(null);
    setPasteSourceUrl(null);
    listingForm.reset({ flat_type: "4 ROOM", storey_range: "" });
    pasteExtract.reset();
    confirmFromPaste.reset();
    setInputMode("paste");
    requestAnimationFrame(() => pasteInputRef.current?.focus());
  }

  const confirmFromPaste = useMutation({
    mutationFn: (values: ListingForm) =>
      confirmListing(sessionId, {
        listing_input_id: listingInputId,
        source_url: pasteSourceUrl,
        raw_listing_subtype: values.listing_flat_subtype || undefined,
        flat_type_source: values.flat_type !== pasteInitialCanonical.flat_type
          ? "user_confirmed"
          : (pasteFieldSources.flat_type ?? "listing_text"),
        flat_model_source: values.flat_model
          ? values.flat_model !== pasteInitialCanonical.flat_model
            ? "user_confirmed"
            : (pasteFieldSources.flat_model ?? "listing_structured_data")
          : undefined,
        storey_source: values.storey_range ? "user" : undefined,
        ...values,
      }),
    onSuccess: async () => {
      resetSmartPaste();
      setListingSavedMsg("Listing saved.");
      qc.invalidateQueries({ queryKey: ["session", sessionId] });
      qc.invalidateQueries({ queryKey: ["comparison", sessionId] });
    },
  });

  const discardSmartPaste = useMutation({
    mutationFn: async () => {
      if (!listingInputId) return;
      await discardListingInput(sessionId, listingInputId);
    },
    onSuccess: () => {
      resetSmartPaste();
    },
    onError: (error) => {
      setPasteError(error instanceof Error ? error.message : "Could not discard this extraction. Please retry.");
    },
  });

  const enrich = useMutation({
    mutationFn: async () => {
      const result = await startEnrichment(sessionId);
      if (result.mode === "queued") {
        await waitForEnrichmentComplete(sessionId);
      }
      return result;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["comparison", sessionId] });
      qc.invalidateQueries({ queryKey: ["session", sessionId] });
    },
  });

  const removeListing = useMutation({
    mutationFn: (listingId: string) => deleteListing(sessionId, listingId),
    onMutate: async (listingId) => {
      await qc.cancelQueries({ queryKey: ["session", sessionId] });
      await qc.cancelQueries({ queryKey: ["comparison", sessionId] });
      const previousSession = qc.getQueryData<Awaited<ReturnType<typeof getSession>>>(["session", sessionId]);
      qc.setQueryData<Awaited<ReturnType<typeof getSession>>>(["session", sessionId], (current) => {
        if (!current) return current;
        const listings = current.listings.filter((listing) => listing.listing_id !== listingId);
        return { ...current, listings, listing_count: listings.length };
      });
      qc.removeQueries({ queryKey: ["comparison", sessionId], exact: true });
      return { previousSession };
    },
    onError: (_error, _listingId, context) => {
      if (context?.previousSession) {
        qc.setQueryData(["session", sessionId], context.previousSession);
      }
    },
    onSuccess: () => {
      setPendingRemoval(null);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["session", sessionId] });
      qc.invalidateQueries({ queryKey: ["comparison", sessionId] });
    },
  });

  function formatPrice(n: number) {
    return `S$${n.toLocaleString("en-SG")}`;
  }

  function selectInputMode(mode: "manual" | "paste", moveFocus = false) {
    setInputMode(mode);
    if (moveFocus) {
      window.requestAnimationFrame(() => (mode === "manual" ? manualEntryTabRef : smartPasteTabRef).current?.focus());
    }
  }

  function handleLocationQueryChange(q: string) {
    setLocQuery(q);
    setSelectedPlace(null);
    setLocSearchError(null);
  }

  function selectPlace(place: GeocodeSuggestion) {
    setSelectedPlace(place);
    setLocSuggestions([]);
    setLocSearchError(null);
    setLocQuery(place.formatted_address);
  }

  function clearDestination() {
    locSearchAbortRef.current?.abort();
    setLocLabel("Work");
    setLocQuery("");
    setLocSuggestions([]);
    setSelectedPlace(null);
    setLocSearchError(null);
    setLocSearchPending(false);
    setLocDay("WEEKDAY");
    setLocTime("08:00");
    setLocMode("PUBLIC_TRANSPORT");
    setDestinationOpen(false);
    setProfileSaved(false);
  }

  useEffect(() => {
    const query = locQuery.trim();
    locSearchAbortRef.current?.abort();
    locSearchAbortRef.current = null;

    if (query.length < 2) {
      setLocSuggestions([]);
      setLocSearchPending(false);
      return;
    }

    if (selectedPlace?.formatted_address === query) {
      setLocSuggestions([]);
      setLocSearchPending(false);
      return;
    }

    const controller = new AbortController();
    locSearchAbortRef.current = controller;
    const timeout = window.setTimeout(() => {
      setLocSearchPending(true);
      geocodeAddress(query, controller.signal)
        .then((result) => {
          if (controller.signal.aborted) return;
          setLocSuggestions(result.suggestions);
          setLocSearchError(
            result.suggestions.length > 0
              ? null
              : "No matching Singapore location found. Check the address and try again.",
          );
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          setLocSuggestions([]);
          setLocSearchError(error instanceof Error ? error.message : "Address search failed. Please retry.");
        })
        .finally(() => {
          if (!controller.signal.aborted) setLocSearchPending(false);
        });
    }, 350);

    return () => {
      window.clearTimeout(timeout);
      controller.abort();
      if (locSearchAbortRef.current === controller) locSearchAbortRef.current = null;
    };
  }, [locQuery, locSearchAttempt, selectedPlace]);

  function retryLocationSearch() {
    setLocSearchError(null);
    setLocSearchAttempt((attempt) => attempt + 1);
  }

  function selectSchool(place: GeocodeSuggestion) {
    const schoolName = place.main_text.trim();
    if (!schoolName) return;
    if (namedSchools.some((school) => school.localeCompare(schoolName, undefined, { sensitivity: "accent" }) === 0)) {
      setSchoolSearchError("That school is already selected.");
      return;
    }
    setNamedSchools((schools) => [...schools, schoolName]);
    setSchoolQuery("");
    setSchoolSuggestions([]);
    setSchoolSearchError(null);
  }

  useEffect(() => {
    const query = schoolQuery.trim();
    schoolSearchAbortRef.current?.abort();
    schoolSearchAbortRef.current = null;

    if (query.length < 2) {
      setSchoolSuggestions([]);
      setSchoolSearchPending(false);
      return;
    }

    const controller = new AbortController();
    schoolSearchAbortRef.current = controller;
    const timeout = window.setTimeout(() => {
      setSchoolSearchPending(true);
      geocodeAddress(query, controller.signal)
        .then((result) => {
          if (controller.signal.aborted) return;
          const suggestions = result.suggestions.filter(isSchoolSuggestion);
          setSchoolSuggestions(suggestions);
          setSchoolSearchError(
            suggestions.length > 0
              ? null
              : "No matching Singapore school was found. Choose a result instead of entering a name manually.",
          );
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          setSchoolSuggestions([]);
          setSchoolSearchError(error instanceof Error ? error.message : "School search failed. Please retry.");
        })
        .finally(() => {
          if (!controller.signal.aborted) setSchoolSearchPending(false);
        });
    }, 350);

    return () => {
      window.clearTimeout(timeout);
      controller.abort();
      if (schoolSearchAbortRef.current === controller) schoolSearchAbortRef.current = null;
    };
  }, [schoolQuery]);

  const workflowStep: "profile" | "listings" | "compare" = listingCount >= 2 ? "compare" : profileSaved ? "listings" : "profile";

  return (
    <div className="nh-workflow-grid space-y-8">
      <div className="space-y-4">
        <Link href="/" className="text-sm text-teal-700 hover:underline">← Home</Link>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="nh-section-kicker">Comparison workspace</p>
            <h2 className="mt-1 text-2xl font-semibold tracking-tight">Build your shortlist</h2>
            <p className="mt-1 text-sm text-slate-600">Set your decision context, add 2–5 confirmed flats, then review the evidence.</p>
          </div>
          <p className="text-sm text-slate-500">{listingCount}/5 flats added</p>
        </div>
        <WorkflowStepper current={workflowStep} profileSaved={Boolean(profileSaved || sessionQuery.data?.profile_saved)} listingCount={listingCount} sessionId={sessionId} />
      </div>

      <section id="buyer-profile" className="nh-card">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-2xl font-semibold tracking-tight">Preferences</h3>
            <p className="mt-1 text-sm text-slate-600">Choose what NearHome should prioritise when comparing flats that meet your requirements.</p>
          </div>
          {(profileSaved || sessionQuery.data?.profile_saved) && (
            <span className="rounded-full bg-green-100 px-3 py-1 text-xs font-medium text-green-800">
              Profile saved
            </span>
          )}
        </div>
        <form
          className="mt-7 space-y-7"
          onSubmit={profileForm.handleSubmit((v) => {
            setProfileSaved(false);
            saveProfile.mutate(v);
          })}
        >
          <section className="rounded-lg border border-slate-200 bg-slate-50/70 p-4">
            <p className="text-sm font-semibold text-slate-900">Budget</p>
            <p className="mt-1 text-sm text-slate-600">Your maximum purchase budget is used to assess affordability for every flat.</p>
            <label className="nh-label mt-4 block max-w-md">
              Maximum purchase budget
              <input type="number" inputMode="numeric" className="nh-input" {...profileForm.register("max_budget")} />
              <span className="nh-helper">Enter the highest purchase price you are comfortable with, in Singapore dollars.</span>
            </label>
          </section>
          <section aria-labelledby="transport-mode-heading">
            <h4 id="transport-mode-heading" className="text-base font-semibold text-slate-900">Main transport mode</h4>
            <p className="mt-1 text-sm text-slate-600">This helps NearHome emphasise the way you normally get around.</p>
            <div className="mt-4">
              <SegmentedControl
                label="Main transport mode"
                value={profileForm.watch("main_transport_mode")}
                options={[
                  { value: "MAINLY_PUBLIC_TRANSPORT", label: "Public transport" },
                  { value: "MAINLY_DRIVING", label: "Driving" },
                  { value: "BOTH", label: "Both" },
                ]}
                onChange={(value) => profileForm.setValue("main_transport_mode", value, { shouldValidate: true })}
              />
            </div>
          </section>
          <section aria-labelledby="decision-priorities-heading">
            <h4 id="decision-priorities-heading" className="text-base font-semibold text-slate-900">Decision priorities</h4>
            <p className="mt-1 text-sm text-slate-600">Choose up to three factors, ordered from most to least important.</p>
            <ol className="mt-4 space-y-2" aria-label="Decision priorities">
              {orderedPriorities.map((priority, index) => (
                <li key={priority} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-white p-3">
                  <span className="flex h-7 w-7 items-center justify-center rounded-full bg-teal-50 text-sm font-semibold text-teal-800" aria-label={`Priority ${index + 1}`}>{index + 1}</span>
                  <select
                    className="nh-select mt-0 min-w-48 flex-1"
                    aria-label={`Priority ${index + 1} factor`}
                    value={priority}
                    onChange={(event) => replacePriority(index, event.target.value as (typeof PRIORITY_VALUES)[number])}
                  >
                    {PRIORITY_VALUES.filter((value) => value === priority || !orderedPriorities.includes(value)).map((value) => (
                      <option key={value} value={value}>{PRIORITY_LABELS[value]}</option>
                    ))}
                  </select>
                  <div className="flex items-center gap-1">
                    <button type="button" className="nh-icon-button" onClick={() => movePriority(index, -1)} disabled={index === 0} aria-label={`Move ${PRIORITY_LABELS[priority]} up`} title="Move up">↑</button>
                    <button type="button" className="nh-icon-button" onClick={() => movePriority(index, 1)} disabled={index === orderedPriorities.length - 1} aria-label={`Move ${PRIORITY_LABELS[priority]} down`} title="Move down">↓</button>
                    <button type="button" className="nh-icon-button" onClick={() => removePriority(index)} disabled={orderedPriorities.length <= 1} aria-label={`Remove ${PRIORITY_LABELS[priority]}`} title="Remove priority">×</button>
                  </div>
                </li>
              ))}
            </ol>
            {orderedPriorities.length < 3 ? (
              <label className="nh-label mt-3 block max-w-sm">
                Add another priority ({3 - orderedPriorities.length} available)
                <select
                  className="nh-select"
                  aria-label="Add another priority"
                  defaultValue=""
                  onChange={(event) => {
                    const value = event.target.value as (typeof PRIORITY_VALUES)[number];
                    if (value) setOrderedPriorities([...orderedPriorities, value]);
                    event.currentTarget.value = "";
                  }}
                >
                  <option value="">Choose a factor…</option>
                  {PRIORITY_VALUES.filter((value) => !orderedPriorities.includes(value)).map((value) => (
                    <option key={value} value={value}>{PRIORITY_LABELS[value]}</option>
                  ))}
                </select>
              </label>
            ) : <p className="mt-3 text-xs text-slate-500">You can rank up to three priorities. Remove one to choose another.</p>}
          </section>
          <section className="nh-optional-section" aria-labelledby="regular-destination-heading">
            <div className="flex flex-wrap items-center gap-2">
              <h4 id="regular-destination-heading" className="text-base font-semibold text-slate-900">Regular destination</h4>
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">Optional</span>
            </div>
            <p className="mt-1 text-sm text-slate-600">Add a workplace, school or other frequently visited location to compare journey times.</p>
            {!destinationOpen && !selectedPlace && (
              <button type="button" className="mt-4 nh-secondary" onClick={() => setDestinationOpen(true)} aria-expanded="false">+ Add regular destination</button>
            )}
            {!destinationOpen && selectedPlace && (
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4">
                <div>
                  <p className="font-medium text-slate-900">{locLabel || "Regular destination"}</p>
                  <p className="mt-1 text-sm text-slate-700">{selectedPlace.formatted_address}</p>
                  <p className="mt-1 text-xs text-slate-500">{locDay === "WEEKDAY" ? "Weekdays" : "Weekends"} at {displayTime(locTime)} · {locMode === "PUBLIC_TRANSPORT" ? "Public transport" : locMode === "DRIVING" ? "Driving" : "Both modes"}</p>
                </div>
                <div className="flex gap-2">
                  <button type="button" className="nh-secondary" onClick={() => setDestinationOpen(true)}>Edit</button>
                  <button type="button" className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700 hover:border-red-300 hover:text-red-700" onClick={() => setDestinationRemovalPending(true)}>Remove</button>
                </div>
              </div>
            )}
            {destinationOpen && (
              <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
                <div className="grid gap-4 md:grid-cols-2">
                  <label className="nh-label">Label <input className="nh-input" placeholder="e.g. Work" value={locLabel} onChange={(e) => setLocLabel(e.target.value)} /></label>
                  <label className="nh-label md:col-span-2">Search Singapore address <input className="nh-input" placeholder="Search address…" value={locQuery} onChange={(e) => handleLocationQueryChange(e.target.value)} aria-describedby={locSearchError ? "location-search-error" : undefined} /></label>
                  {locSearchPending && <p className="text-xs text-slate-500 md:col-span-2">Searching Singapore addresses…</p>}
                  {locSuggestions.length > 0 && <ul className="rounded-lg border border-slate-200 bg-white text-sm md:col-span-2">{locSuggestions.map((s) => <li key={s.place_id}><button type="button" className="w-full px-3 py-2 text-left hover:bg-slate-50" onClick={() => selectPlace(s)}>{s.description}</button></li>)}</ul>}
                  {locSearchError && <div className="flex items-center gap-2 text-xs text-red-700 md:col-span-2" role="alert" id="location-search-error"><span>{locSearchError}</span><button type="button" className="font-medium underline" onClick={retryLocationSearch} disabled={locSearchPending}>Retry</button></div>}
                  <label className="nh-label">Typical day <select className="nh-select" value={locDay} onChange={(e) => setLocDay(e.target.value as "WEEKDAY" | "WEEKEND")}><option value="WEEKDAY">Weekday</option><option value="WEEKEND">Weekend</option></select></label>
                  <label className="nh-label">Arrival time <input type="time" className="nh-input" value={locTime} onChange={(e) => setLocTime(e.target.value)} /></label>
                  <div className="md:col-span-2"><p className="text-sm font-medium text-slate-800">Preferred travel mode</p><div className="mt-2"><SegmentedControl label="Preferred destination travel mode" value={locMode} options={[{ value: "PUBLIC_TRANSPORT", label: "Public transport" }, { value: "DRIVING", label: "Driving" }, { value: "BOTH", label: "Both" }]} onChange={setLocMode} /></div></div>
                </div>
                {selectedPlace && <p className="mt-3 text-xs text-green-700">Confirmed: {selectedPlace.formatted_address}</p>}
                <p className="mt-3 text-xs text-slate-500">Area-level driving connectivity is assessed separately, even without a destination.</p>
                <div className="mt-4 flex flex-wrap justify-end gap-2">
                  {selectedPlace && <button type="button" className="mr-auto text-sm text-red-700 underline" onClick={() => setDestinationRemovalPending(true)}>Remove destination</button>}
                  <button type="button" className="nh-secondary" onClick={() => selectedPlace ? setDestinationOpen(false) : clearDestination()}>Cancel</button>
                  <button type="button" className="nh-primary" disabled={!selectedPlace} onClick={() => setDestinationOpen(false)}>Done</button>
                </div>
              </div>
            )}
          </section>
          <section className="rounded-xl border border-slate-200 bg-slate-50/70 p-4" aria-labelledby="schools-preference-heading">
            <div className="flex items-start justify-between gap-3">
              <label className="flex cursor-pointer items-start gap-3" id="schools-preference-heading">
                <input aria-label="Schools matter to me — compare nearby schools" className="mt-1 h-4 w-4 rounded border-slate-300 text-teal-700 focus:ring-teal-600" type="checkbox" checked={schoolsMatter} onChange={(e) => setSchoolsMatter(e.target.checked)} />
                <span><span className="block text-sm font-semibold text-slate-900">Compare nearby schools</span><span className="mt-1 block text-sm text-slate-600">Include school access in your comparison and optionally select specific schools.</span></span>
              </label>
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">Optional</span>
            </div>
          {schoolsMatter && (
            <section className="mt-4 block border-t border-slate-200 pt-4 text-sm" aria-labelledby="named-schools-heading">
              <h4 id="named-schools-heading" className="font-medium">Named schools</h4>
              <p className="mt-1 text-xs text-slate-500">Search Singapore schools and select a confirmed OneMap result. Manual school names cannot be saved.</p>
              {namedSchools.length > 0 && (
                <ul className="mt-2 space-y-2" aria-label="Selected named schools">
                  {namedSchools.map((school) => (
                    <li key={school} className="flex items-center justify-between gap-2 rounded border border-slate-200 bg-slate-50 px-3 py-2">
                      <span>{school}</span>
                      <button type="button" className="text-sm text-slate-700 underline" aria-label={`Remove ${school}`} onClick={() => setNamedSchools((schools) => schools.filter((value) => value !== school))}>Remove</button>
                    </li>
                  ))}
                </ul>
              )}
              {namedSchools.length < 10 && (
                <div className="mt-2">
                  <label className="nh-label">
                    Search for a named school
                    <input
                      className="nh-input"
                      aria-describedby={schoolSearchError ? "school-search-error" : undefined}
                      placeholder="e.g. Raffles Institution"
                      value={schoolQuery}
                      onChange={(event) => {
                        setSchoolQuery(event.target.value);
                        setSchoolSearchError(null);
                      }}
                    />
                  </label>
                  {schoolSearchPending && <p className="mt-1 text-xs text-slate-500">Searching Singapore schools…</p>}
                  {schoolSuggestions.length > 0 && (
                    <ul className="mt-2 rounded border bg-white text-sm" aria-label="Named school search results">
                      {schoolSuggestions.map((suggestion) => (
                        <li key={suggestion.place_id}>
                          <button type="button" className="w-full px-3 py-2 text-left hover:bg-slate-50" onClick={() => selectSchool(suggestion)}>
                            <span className="block font-medium">{suggestion.main_text}</span>
                            <span className="block text-xs text-slate-500">{suggestion.description}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                  {schoolSearchError && <p className="mt-1 text-xs text-red-700" role="alert" id="school-search-error">{schoolSearchError}</p>}
                </div>
              )}
            </section>
          )}
          </section>
          <div className="flex flex-wrap items-center justify-end gap-3 border-t border-slate-200 pt-6">
            <button
              type="submit"
              className="nh-primary"
              disabled={saveProfile.isPending || !profileForm.formState.isValid}
            >
              {saveProfile.isPending ? "Saving…" : profileSaved || sessionQuery.data?.profile_saved ? "Update profile" : "Save profile and continue"}
            </button>
            {!profileForm.formState.isValid && <p className="text-xs text-slate-500">Enter a positive budget and choose a first priority to continue.</p>}
            {saveProfile.isError && (
              <p className="text-sm text-red-600" role="alert">{String(saveProfile.error.message)}</p>
            )}
          </div>
        </form>
      </section>

      {savedListings.length > 0 && (
        <section className="nh-card" aria-labelledby="saved-listings-heading">
          <h3 ref={shortlistHeadingRef} tabIndex={-1} id="saved-listings-heading" className="text-lg font-medium">
            Your shortlist ({savedListings.length}/5)
          </h3>
          {listingSavedMsg && (
            <p className="mt-2 text-sm text-green-700" role="status">{listingSavedMsg}</p>
          )}
          <ul className="mt-3 space-y-2">
            {savedListings.map((l) => (
              <li key={l.listing_id} className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 p-3 text-sm">
                <div>
                  <p className="font-medium text-slate-900">{l.display_name}</p>
                  <p className="text-slate-600">{l.address}</p>
                  <p className="mt-1 text-slate-700">
                    {formatPrice(l.asking_price)} · {l.floor_area_sqm} sqm · {l.flat_type}
                  </p>
                </div>
                <button
                  type="button"
                  className="shrink-0 rounded-lg border border-slate-300 px-3 py-2 text-xs text-slate-600 hover:border-red-300 hover:text-red-700 disabled:opacity-50"
                  aria-label={`Remove flat ${l.address}`}
                  onClick={() => {
                    removeListing.reset();
                    setPendingRemoval(l);
                  }}
                  disabled={removeListing.isPending}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {listingCount < 5 && (
        <section className="nh-card" aria-labelledby="add-flat-heading">
          <h3 ref={addFlatHeadingRef} tabIndex={-1} id="add-flat-heading" className="text-xl font-semibold tracking-tight">
            Add a flat
          </h3>
          <p className="mt-1 text-sm text-slate-600">
            Choose manual entry or Smart Paste to add a flat to your shortlist.
          </p>
          <div className="mt-5 inline-flex rounded-xl border border-slate-200 bg-slate-50 p-1" role="tablist" aria-label="How to add a flat">
            <button ref={manualEntryTabRef} type="button" role="tab" aria-selected={inputMode === "manual"} aria-controls="manual-listing-form" id="manual-entry-tab" className={`nh-tab ${inputMode === "manual" ? "nh-tab-active" : ""}`} onClick={() => selectInputMode("manual")} onKeyDown={(event) => { if (["ArrowRight", "ArrowDown", "End"].includes(event.key)) { event.preventDefault(); selectInputMode("paste", true); } }}>Manual entry</button>
            <button ref={smartPasteTabRef} type="button" role="tab" aria-selected={inputMode === "paste"} aria-controls="smart-paste-panel" id="smart-paste-tab" className={`nh-tab ${inputMode === "paste" ? "nh-tab-active" : ""}`} onClick={() => selectInputMode("paste")} onKeyDown={(event) => { if (["ArrowLeft", "ArrowUp", "Home"].includes(event.key)) { event.preventDefault(); selectInputMode("manual", true); } }}>Smart Paste</button>
          </div>

          {inputMode === "paste" && !listingInputId && (
            <div id="smart-paste-panel" role="tabpanel" aria-labelledby="smart-paste-tab" className="mt-5 max-w-3xl space-y-3">
              <p className="text-sm text-slate-600">Paste a PropertyGuru, 99.co or other listing URL, or copied listing text. You will review every extracted field before it is added.</p>
              <textarea
                ref={pasteInputRef}
                className="nh-textarea h-40 font-mono"
                value={pasteText}
                onChange={(e) => setPasteText(e.target.value)}
                placeholder="Paste any property listing URL (PropertyGuru, 99.co, EdgeProp, etc.) or copied listing text here…"
              />
              <button type="button" className="nh-primary" onClick={() => pasteExtract.mutate()} disabled={pasteExtract.isPending || !pasteText.trim()}>
                {pasteExtract.isPending ? "Extracting…" : "Add a flat"}
              </button>
              <SmartPasteProgress
                active={pasteExtract.isPending}
                sourceType={buildSmartPasteRequest(pasteText).sourceType}
              />
              {pasteError && (
                <p className="text-sm text-red-600" role="alert">{pasteError}</p>
              )}
            </div>
          )}

          {inputMode === "paste" && listingInputId && pasteWarnings.length > 0 && (
            <div className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
              {pasteWarnings.map((w, i) => (
                <p key={i}>{w}</p>
              ))}
            </div>
          )}

          {(inputMode === "manual" || listingInputId) && (
            <form
              id="manual-listing-form"
              role="tabpanel"
              aria-labelledby="manual-entry-tab"
              className="mt-5 grid gap-5 md:grid-cols-2"
              onSubmit={listingForm.handleSubmit((v) =>
                listingInputId ? confirmFromPaste.mutate(v) : addListing.mutate(v),
              )}
            >
              {listingInputId && (
                <>
                  <div className="md:col-span-2 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-teal-100 bg-teal-50/60 p-3">
                    <p className="text-sm text-teal-800">Review extracted fields, edit any value, then confirm.</p>
                  </div>
                  {Object.keys(pasteEvidence).length > 0 && (
                    <div className="md:col-span-2 rounded border border-slate-200 bg-slate-50 p-3 text-xs">
                      <p className="font-medium text-slate-700">Source evidence</p>
                      <ul className="mt-2 space-y-2">
                        {Object.entries(pasteEvidence).map(([field, items]) => (
                          <li key={field}>
                            <span className="font-medium capitalize">{field.replace(/_/g, " ")}:</span>{" "}
                            {items.map((item, i) => (
                              <span key={i} className="text-slate-600">
                                {String(item.value)}
                                {item.source_snippet ? ` (“${item.source_snippet.slice(0, 60)}…”)` : ""}
                                {i < items.length - 1 ? "; " : ""}
                              </span>
                            ))}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              )}
              <label className="nh-label md:col-span-2">
                Address <span className="text-red-700">*</span>
                <input className="nh-input" {...listingForm.register("address")} />
              </label>
              <label className="nh-label">
                Asking price <span className="text-red-700">*</span>
                <span className="relative mt-1 block"><span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-sm text-slate-500">S$</span><input type="number" inputMode="numeric" className="nh-input mt-0 pl-10" {...listingForm.register("asking_price")} /></span>
              </label>
              <label className="nh-label">
                Floor area (sqm) <span className="text-red-700">*</span>
                <input type="number" inputMode="decimal" step="0.1" className="nh-input" {...listingForm.register("floor_area_sqm")} />
              </label>
              <label className="nh-label">
                Flat type <span className="text-red-700">*</span>
                <input className="nh-input" placeholder="e.g. 4 ROOM" {...listingForm.register("flat_type")} />
                <span className="nh-helper">The broad HDB type, such as 3 ROOM, 4 ROOM or 5 ROOM.</span>
              </label>
              <label className="nh-label">
                Listing subtype <span className="font-normal text-slate-500">(optional)</span>
                <input className="nh-input" placeholder="e.g. 4A, 5STD" {...listingForm.register("listing_flat_subtype")} />
                <span className="nh-helper">A listing-specific code. It is different from the HDB flat model.</span>
              </label>
              <label className="nh-label">
                HDB flat model <span className="font-normal text-slate-500">(optional)</span>
                <input className="nh-input" placeholder="e.g. Model A" {...listingForm.register("flat_model")} />
                {pasteFieldSources.flat_model === "derived_from_subtype" && listingForm.watch("listing_flat_subtype") && (
                  <span className="mt-1 block text-xs text-slate-500">
                    Derived from listing subtype “{listingForm.watch("listing_flat_subtype")}”. Review or edit before saving.
                  </span>
                )}
              </label>
              <label className="nh-label">
                Storey range <span className="font-normal text-slate-500">(optional)</span>
                <select className="nh-select" {...listingForm.register("storey_range")}>
                  <option value="">Not provided</option>
                  {[
                    "01–03", "04–06", "07–09", "10–12", "13–15", "16–18", "19–21", "22–24", "25+",
                  ].map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
                <span className="nh-helper">Storey is entered by you only; Smart Paste never fills it.</span>
              </label>
              <label className="nh-label">
                Remaining lease (years) <span className="font-normal text-slate-500">(optional)</span>
                <input type="number" inputMode="decimal" step="0.1" className="nh-input" {...listingForm.register("remaining_lease_years")} />
                <span className="nh-helper">Leave blank if it is not known; NearHome can estimate it from transaction evidence.</span>
              </label>
              <div className="md:col-span-2 flex flex-wrap items-center justify-end gap-3 border-t border-slate-200 pt-5">
                <button
                  type="submit"
                  className="nh-primary"
                  disabled={addListing.isPending || confirmFromPaste.isPending || !listingForm.formState.isValid}
                >
                  {listingInputId
                    ? confirmFromPaste.isPending
                      ? "Confirming…"
                      : "Confirm listing"
                    : addListing.isPending
                      ? "Adding…"
                      : "Add listing"}
                </button>
                {listingInputId && (
                  <button
                    type="button"
                    className="nh-secondary"
                    onClick={() => discardSmartPaste.mutate()}
                    disabled={discardSmartPaste.isPending || confirmFromPaste.isPending}
                  >
                    {discardSmartPaste.isPending ? "Discarding…" : "Discard listing"}
                  </button>
                )}
                {(addListing.isError || confirmFromPaste.isError) && (
                  <p className="text-sm text-red-600" role="alert">
                    {String((addListing.error ?? confirmFromPaste.error)?.message)}
                  </p>
                )}
                {discardSmartPaste.isError && (
                  <p className="text-sm text-red-600" role="alert">{pasteError}</p>
                )}
                {!listingForm.formState.isValid && !addListing.isPending && !confirmFromPaste.isPending && <p className="mr-auto text-xs text-slate-500">Complete the required address, price, area and flat type to add this flat.</p>}
              </div>
            </form>
          )}
        </section>
      )}

      {listingCount >= 2 && (
        <section className="nh-card">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-medium">Enrichment</h3>
            <div className="flex flex-wrap items-center gap-2">
              <Link
                href={`/session/${sessionId}/comparison`}
                className="nh-secondary"
              >
                Open comparison
              </Link>
              <button
                type="button"
                className="nh-primary"
                onClick={() => enrich.mutate()}
                disabled={enrich.isPending}
              >
                {enrich.isPending ? "Running enrichment…" : "Run enrichment"}
              </button>
            </div>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            Geocoding, fair-price, transport, driving and journey estimates
            {isDemo ? " (demo fixtures when API keys unavailable)" : " using live data sources"}
          </p>
          {enrich.isError && (
            <p className="mt-2 text-sm text-red-600" role="alert">{String(enrich.error.message)}</p>
          )}
          {enrich.isPending && <EnrichmentProgress sessionId={sessionId} />}
        </section>
      )}

      {listingCount >= 2 && comparisonQuery.data && (
        <ComparisonView data={comparisonQuery.data} session={sessionQuery.data} />
      )}

      {listingCount === 1 && (
        <p className="text-sm text-slate-600">Add another flat to compare this listing.</p>
      )}

      <ConfirmDialog
        open={pendingRemoval !== null}
        title="Remove this flat?"
        message={pendingRemoval ? `Remove ${pendingRemoval.address} from your shortlist? Any confirmed details, enrichment results and notes saved for this flat will also be removed from this comparison.` : ""}
        pending={removeListing.isPending}
        error={removeListing.isError ? removeListing.error.message : null}
        onCancel={() => {
          removeListing.reset();
          setPendingRemoval(null);
        }}
        onConfirm={() => {
          if (pendingRemoval) removeListing.mutate(pendingRemoval.listing_id);
        }}
        fallbackFocusRef={addFlatHeadingRef}
      />
      <ConfirmDialog
        open={destinationRemovalPending}
        title="Remove regular destination?"
        message="Remove this destination from the profile form? Select Update profile afterwards to save the change."
        confirmLabel="Remove destination"
        onCancel={() => setDestinationRemovalPending(false)}
        onConfirm={() => {
          clearDestination();
          setDestinationRemovalPending(false);
        }}
      />
    </div>
  );
}
