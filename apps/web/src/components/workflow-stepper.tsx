import Link from "next/link";

type WorkflowStep = "profile" | "listings" | "compare";

type WorkflowStepperProps = {
  current: WorkflowStep;
  profileSaved: boolean;
  listingCount: number;
  sessionId: string;
};

const steps: Array<{ key: WorkflowStep; label: string; shortLabel: string }> = [
  { key: "profile", label: "Buyer profile", shortLabel: "Profile" },
  { key: "listings", label: "Add flats", shortLabel: "Flats" },
  { key: "compare", label: "Compare results", shortLabel: "Compare" },
];

export function WorkflowStepper({ current, profileSaved, listingCount, sessionId }: WorkflowStepperProps) {
  const currentIndex = steps.findIndex((step) => step.key === current);
  const completed = (step: WorkflowStep) => {
    if (step === "profile") return profileSaved;
    if (step === "listings") return listingCount >= 2;
    return false;
  };

  return (
    <nav aria-label="Comparison progress" className="nh-stepper">
      <ol className="flex items-center">
        {steps.map((step, index) => {
          const isCurrent = step.key === current;
          const isComplete = completed(step.key) && !isCurrent;
          const canNavigate = index < currentIndex;
          const target = step.key === "profile" ? `/session/${sessionId}#buyer-profile` : step.key === "listings" ? `/session/${sessionId}#add-flat-heading` : `/session/${sessionId}/comparison`;
          const content = (
            <>
              <span className={`nh-step-dot ${isCurrent ? "nh-step-dot-current" : isComplete ? "nh-step-dot-complete" : ""}`}>
                {isComplete ? "✓" : index + 1}
              </span>
              <span className={`hidden text-xs sm:inline ${isCurrent ? "font-semibold text-slate-900" : "text-slate-500"}`}>
                {step.label}
              </span>
              <span className={`text-xs sm:hidden ${isCurrent ? "font-semibold text-slate-900" : "text-slate-500"}`}>
                {step.shortLabel}
              </span>
            </>
          );
          return (
            <li key={step.key} className="flex min-w-0 flex-1 items-center last:flex-none">
              {canNavigate ? <Link href={target} className="flex items-center gap-2 rounded focus:outline-none focus:ring-2 focus:ring-teal-600">{content}</Link> : <div className="flex items-center gap-2" aria-current={isCurrent ? "step" : undefined}>{content}</div>}
              {index < steps.length - 1 && <span className={`mx-2 h-px flex-1 ${index < currentIndex ? "bg-teal-600" : "bg-slate-200"}`} aria-hidden="true" />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
