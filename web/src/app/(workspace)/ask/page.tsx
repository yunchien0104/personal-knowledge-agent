import { Suspense } from "react";

import { AskPage } from "@/components/personal-brain/AskPage";

export default function Page() {
  return <Suspense fallback={<p className="text-sm text-muted">載入知識問答…</p>}><AskPage /></Suspense>;
}
