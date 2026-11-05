import { notFound } from "next/navigation";
import { Suspense } from "react";

import { AiEditorE2EHarness } from "@/components/notes/AiEditorE2EHarness";

export default function AiEditorE2EPage() {
  if (process.env.ENABLE_AI_E2E_HARNESS !== "1") notFound();
  return (
    <Suspense fallback={<p>Loading editor harness…</p>}>
      <AiEditorE2EHarness />
    </Suspense>
  );
}
