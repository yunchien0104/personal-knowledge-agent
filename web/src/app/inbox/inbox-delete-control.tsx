"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { getApi } from "@/components/personal-brain/api";
import { DeleteAssetDialog } from "@/components/personal-brain/DeleteAssetDialog";
import type { AssetDeletion, KnowledgeItem } from "@/components/personal-brain/types";

export function InboxDeleteControl({
  assetId,
  fileName,
  sourceStatus,
  initialDeletion,
}: {
  assetId: string;
  fileName: string;
  sourceStatus: string;
  initialDeletion: AssetDeletion | null;
}) {
  const router = useRouter();
  const [deletion, setDeletion] = useState(initialDeletion);
  const [open, setOpen] = useState(false);
  const [pollError, setPollError] = useState<string | null>(null);
  const active = deletion?.status === "pending" || deletion?.status === "deleting";

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => {
      void getApi<AssetDeletion[]>(`/api/assets/deletions?asset_id=${encodeURIComponent(assetId)}`)
        .then((rows) => {
          const latest = rows[0] ?? null;
          setDeletion(latest);
          setPollError(null);
          if (latest && !["pending", "deleting"].includes(latest.status)) router.refresh();
        })
        .catch(() => setPollError("暫時無法更新刪除狀態，請重新整理。"));
    }, 2500);
    return () => window.clearInterval(timer);
  }, [active, assetId, router]);

  const item: KnowledgeItem = {
    id: assetId,
    kind: "PDF",
    category: "source",
    title: fileName,
    meta: "Inbox PDF",
    status: "已完成",
    excerpt: "",
    updatedAt: "",
    sourceStatus,
  };

  return (
    <>
      {deletion ? <p>刪除狀態：{deletion.status}</p> : null}
      {pollError ? <p role="alert">{pollError}</p> : null}
      <button type="button" disabled={active} onClick={() => setOpen(true)}>
        {deletion?.status === "failed" ? "重試刪除" : sourceStatus === "source_removed" ? "永久刪除全部" : "刪除 PDF"}
      </button>
      {open ? <DeleteAssetDialog
        item={item}
        onClose={() => setOpen(false)}
        onRequested={(requested) => {
          setDeletion(requested);
          setOpen(false);
          router.refresh();
        }}
      /> : null}
    </>
  );
}
