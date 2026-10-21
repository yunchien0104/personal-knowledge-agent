import { Suspense } from "react";

import { ResearchPage } from "@/components/personal-brain/ResearchPage";

export default function Page() {
  return <Suspense fallback={<p className="text-sm text-muted">載入研究工作區…</p>}><ResearchPage /></Suspense>;
}
