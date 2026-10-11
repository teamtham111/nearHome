import { notFound } from "next/navigation";

import { ResultsPreview } from "./results-preview";

// Development-only page for reviewing the results layout with sample data —
// no API, database or Docker required.
export default function ResultsPreviewPage() {
  if (process.env.NODE_ENV === "production") notFound();
  return <ResultsPreview />;
}
