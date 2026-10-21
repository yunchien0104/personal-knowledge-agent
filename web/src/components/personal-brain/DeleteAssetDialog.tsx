"use client";

import { AlertTriangle, FileX2, Trash2, X } from "lucide-react";
import { useState } from "react";

import { getApi } from "./api";
import type { AssetDeletion, KnowledgeItem } from "./types";
import { PrimaryButton, SoftButton } from "./ui";

type DeleteMode = AssetDeletion["mode"];

export function DeleteAssetDialog({
  item,
  onClose,
  onRequested,
}: {
  item: KnowledgeItem;
  onClose: () => void;
  onRequested: (deletion: AssetDeletion) => void;
}) {
  const [mode, setMode] = useState<DeleteMode>(
    item.sourceStatus === "source_removed" ? "purge_all" : "remove_original",
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function requestDeletion() {
    setSubmitting(true);
    setError(null);
    try {
      const deletion = await getApi<AssetDeletion>("/api/assets/deletions", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          asset_id: item.id,
          mode,
          request_key: crypto.randomUUID(),
        }),
      });
      onRequested(deletion);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "無法開始刪除，請稍後再試。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/45 p-4" role="presentation">
      <section
        aria-labelledby="delete-asset-title"
        aria-modal="true"
        className="w-full max-w-xl rounded-xl border border-line bg-surface p-6 shadow-soft"
        role="dialog"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-[11px] font-medium text-rose-700">刪除 PDF</p>
            <h2 id="delete-asset-title" className="mt-2 text-xl font-semibold">{item.title}</h2>
          </div>
          <button type="button" onClick={onClose} aria-label="關閉刪除確認" className="rounded-md p-2 text-muted hover:bg-subtle"><X className="h-4 w-4" /></button>
        </div>

        <div className="mt-6 grid gap-3">
          <button
            type="button"
            disabled={item.sourceStatus === "source_removed"}
            onClick={() => setMode("remove_original")}
            className={`rounded-lg border p-4 text-left transition ${mode === "remove_original" ? "border-indigo-400 bg-indigo-50/60" : "border-line hover:bg-subtle"} disabled:cursor-not-allowed disabled:opacity-45`}
          >
            <span className="flex items-center gap-2 text-sm font-semibold"><FileX2 className="h-4 w-4" />移除原始 PDF</span>
            <span className="mt-2 block text-xs leading-5 text-muted">保留來源紀錄、摘要、筆記與研究歷史；所有原文引用會標記為無法驗證。</span>
          </button>
          <button
            type="button"
            onClick={() => setMode("purge_all")}
            className={`rounded-lg border p-4 text-left transition ${mode === "purge_all" ? "border-rose-400 bg-rose-50/70" : "border-line hover:bg-subtle"}`}
          >
            <span className="flex items-center gap-2 text-sm font-semibold text-rose-700"><Trash2 className="h-4 w-4" />永久刪除全部</span>
            <span className="mt-2 block text-xs leading-5 text-muted">刪除 PDF、解析內容與此來源專屬的筆記／Claims；跨來源研究會保留，但相關引用將失效。</span>
          </button>
        </div>

        <div className="mt-5 flex gap-3 rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-900"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><p>刪除由後端 Queue 執行，完成前狀態會顯示為 pending 或 deleting。Supabase 備份與稽核紀錄仍依服務保留政策保存，無法同步抹除。</p></div>
        {error ? <p role="alert" className="mt-4 text-sm text-rose-700">{error}</p> : null}
        <div className="mt-6 flex justify-end gap-2">
          <SoftButton type="button" onClick={onClose} disabled={submitting}>取消</SoftButton>
          <PrimaryButton type="button" onClick={requestDeletion} disabled={submitting} className={mode === "purge_all" ? "bg-rose-600 hover:bg-rose-700" : ""}>{submitting ? "正在送出…" : mode === "purge_all" ? "確認永久刪除" : "確認移除原檔"}</PrimaryButton>
        </div>
      </section>
    </div>
  );
}
