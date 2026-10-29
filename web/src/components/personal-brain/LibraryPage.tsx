"use client";

import { ExternalLink, FileText, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  shouldPollSourceNoteWorkspaces,
  sourceNoteDisplayState,
  type SourceNoteWorkspace,
} from "@/lib/source-notes";

import { assetTitle, getApi, getApiEnvelope, sourceKind, supportsSourceNote } from "./api";
import { DeleteAssetDialog } from "./DeleteAssetDialog";
import type { Asset, AssetDeletion, KnowledgeItem, SourceKind } from "./types";
import { PageTitle, PendingState, SourceIcon, StatusBadge } from "./ui";

const kindLabel: Record<SourceKind, string> = { PDF: "PDF", IG: "Instagram", TH: "Threads", NOTE: "筆記" };

async function fetchSourceNoteWorkspaces(assets: Asset[]) {
  const eligible = assets.filter((asset) => supportsSourceNote(asset.source_type));
  const rows = await Promise.all(eligible.map(async (asset) => {
    try {
      return [asset.id, await getApi<SourceNoteWorkspace>(`/api/assets/${asset.id}/source-note`)] as const;
    } catch {
      return [asset.id, null] as const;
    }
  }));
  return Object.fromEntries(rows) as Record<string, SourceNoteWorkspace | null>;
}

function readableStatus(asset: Asset) {
  if (["ready", "completed"].includes(asset.status)) return "已完成" as const;
  if (["processing", "pending"].includes(asset.status)) return "處理中" as const;
  if (asset.status === "deleting") return "刪除中" as const;
  if (asset.status === "deletion_failed") return "刪除失敗" as const;
  if (["failed", "unsupported"].includes(asset.status)) return "失敗" as const;
  return "待檢視" as const;
}

function deletionItem(asset: Asset): KnowledgeItem {
  return {
    id: asset.id, kind: sourceKind(asset.source_type), category: "source", title: assetTitle(asset),
    meta: new Date(asset.created_at).toLocaleDateString("zh-TW"), status: readableStatus(asset),
    excerpt: "原始來源", updatedAt: asset.updated_at, sourceStatus: asset.status,
    activeRevisionId: asset.active_revision_id, sourceUrl: asset.source_url,
  };
}

export function LibraryPage() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [deletions, setDeletions] = useState<AssetDeletion[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Asset | null>(null);
  const [workspaces, setWorkspaces] = useState<Record<string, SourceNoteWorkspace | null>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [total, setTotal] = useState(0);

  const loadData = useCallback(async () => {
    const [assetPayload, deletionRows] = await Promise.all([
      getApiEnvelope<Asset[]>("/api/assets?limit=100"),
      getApi<AssetDeletion[]>("/api/assets/deletions"),
    ]);
    setAssets(assetPayload.data ?? []);
    setTotal(assetPayload.meta?.total ?? 0);
    setDeletions(deletionRows);
    setError(null);
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([
      getApiEnvelope<Asset[]>("/api/assets?limit=100"),
      getApi<AssetDeletion[]>("/api/assets/deletions"),
    ]).then(([assetPayload, deletionRows]) => {
      if (!active) return;
      setAssets(assetPayload.data ?? []); setTotal(assetPayload.meta?.total ?? 0);
      setDeletions(deletionRows); setError(null);
    }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "Library 讀取失敗。"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [loadData]);

  const activeWork = assets.some((asset) => ["pending", "processing", "deleting"].includes(asset.status))
    || deletions.some((row) => ["pending", "deleting"].includes(row.status));
  useEffect(() => {
    if (!activeWork) return;
    const timer = window.setInterval(() => { void loadData(); }, 2500);
    return () => window.clearInterval(timer);
  }, [activeWork, loadData]);

  useEffect(() => {
    if (!assets.some((asset) => supportsSourceNote(asset.source_type))) return;
    let active = true;
    void fetchSourceNoteWorkspaces(assets).then((next) => {
      if (active) setWorkspaces(next);
    });
    return () => { active = false; };
  }, [assets]);

  const activeNoteGeneration = shouldPollSourceNoteWorkspaces(workspaces);
  useEffect(() => {
    if (!activeNoteGeneration) return;
    let active = true;
    const refresh = () => {
      void fetchSourceNoteWorkspaces(assets).then((next) => {
        if (active) setWorkspaces(next);
      });
    };
    const timer = window.setInterval(refresh, 2500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [activeNoteGeneration, assets]);

  const filtered = useMemo(() => assets.filter((asset) => {
    const term = query.trim().toLocaleLowerCase();
    return !term || `${assetTitle(asset)} ${asset.source_type}`.toLocaleLowerCase().includes(term);
  }), [assets, query]);

  async function openOriginal(asset: Asset) {
    setBusyId(asset.id); setError(null);
    try {
      const { url } = await getApi<{ url: string }>(`/api/assets/${asset.id}/original`);
      window.open(url, "_blank", "noopener,noreferrer");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "原始來源無法開啟。"); }
    finally { setBusyId(null); }
  }

  async function generateNote(asset: Asset) {
    setBusyId(asset.id); setError(null);
    try {
      await getApi(`/api/assets/${asset.id}/source-note`, { method: "POST" });
      const workspace = await getApi<SourceNoteWorkspace>(`/api/assets/${asset.id}/source-note`);
      setWorkspaces((current) => ({ ...current, [asset.id]: workspace }));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "筆記生成工作無法建立。"); }
    finally { setBusyId(null); }
  }

  return <div className="animate-page">
    <PageTitle title="Library" />
    <div className="mt-3 flex justify-end"><span className="text-xs font-medium text-muted">共 {total} 筆來源</span></div>
    <div className="mt-6 flex gap-2 border-b border-line pb-4"><input value={query} onChange={(event) => setQuery(event.target.value)} aria-label="搜尋原始資料" placeholder="搜尋來源名稱…" className="min-w-0 flex-1 rounded-md bg-subtle px-3 py-2.5 text-xs outline-none focus:ring-1 focus:ring-ink" /><button type="button" onClick={() => void loadData()} aria-label="重新整理" className="rounded-md border border-line p-2.5 text-muted hover:bg-subtle"><RefreshCw className="h-4 w-4" /></button></div>
    {error ? <p role="alert" className="mt-4 text-sm text-rose-700">{error}</p> : null}
    {loading ? <p className="py-10 text-sm text-muted">正在讀取 Library…</p> : null}
    {!loading && filtered.length ? <div role="table" aria-label="原始資料列表" className="mt-4 overflow-x-auto rounded-lg border border-line bg-surface">
      <div role="row" className="grid min-w-[760px] grid-cols-[minmax(220px,1fr)_120px_120px_110px_minmax(250px,auto)] gap-3 border-b border-line bg-subtle px-4 py-2 text-[10px] font-medium uppercase tracking-wide text-faint"><span>名稱</span><span>來源類型</span><span>加入日期</span><span>處理狀態</span><span>操作</span></div>
      {filtered.map((asset) => {
        const kind = sourceKind(asset.source_type);
        const workspace = workspaces[asset.id];
        const noteState = sourceNoteDisplayState(workspace ?? null);
        const deletion = deletions.find((row) => row.asset_id_snapshot === asset.id && ["pending", "deleting", "failed"].includes(row.status));
        return <div role="row" key={asset.id} className="grid min-w-[760px] grid-cols-[minmax(220px,1fr)_120px_120px_110px_minmax(250px,auto)] items-center gap-3 border-b border-line px-4 py-3 last:border-b-0"><div className="flex min-w-0 items-center gap-3"><SourceIcon kind={kind} size="sm" /><span className="truncate text-xs font-semibold">{assetTitle(asset)}</span></div><span className="text-xs text-muted">{kindLabel[kind]}</span><span className="text-xs text-muted">{new Date(asset.created_at).toLocaleDateString("zh-TW")}</span><StatusBadge status={deletion?.status === "failed" ? "刪除失敗" : readableStatus(asset)} /><div className="flex flex-wrap items-center gap-2"><button type="button" onClick={() => openOriginal(asset)} disabled={busyId === asset.id || asset.status === "deleting"} className="inline-flex items-center gap-1 text-[11px] font-semibold text-indigo-700 disabled:opacity-40"><ExternalLink className="h-3 w-3" />查看原檔／來源</button>{workspace?.note ? <Link href={`/assets/${asset.id}/source-note`} className="inline-flex items-center gap-1 text-[11px] font-semibold text-indigo-700"><FileText className="h-3 w-3" />查看筆記</Link> : supportsSourceNote(asset.source_type) ? <button type="button" onClick={() => generateNote(asset)} disabled={busyId === asset.id || asset.status !== "ready" || noteState === "generating"} className="inline-flex items-center gap-1 text-[11px] font-semibold text-indigo-700 disabled:opacity-40"><Sparkles className="h-3 w-3" />{noteState === "generating" ? "生成中" : noteState === "failed" ? "重試筆記" : "生成筆記"}</button> : null}{asset.source_type === "pdf" ? <button type="button" aria-label={`移除 ${assetTitle(asset)}`} onClick={() => setDeleteTarget(asset)} disabled={deletion?.status === "pending" || deletion?.status === "deleting"} className="inline-flex items-center gap-1 text-[11px] font-semibold text-rose-700 disabled:opacity-40"><Trash2 className="h-3 w-3" />移除</button> : null}</div></div>;
      })}
    </div> : null}
    {!loading && !filtered.length ? <PendingState title="Library 沒有來源" description="新增 PDF 或網址後，來源會在這裡顯示；已移除來源預設不顯示。" /> : null}
    {deleteTarget ? <DeleteAssetDialog item={deletionItem(deleteTarget)} onClose={() => setDeleteTarget(null)} onRequested={() => { setDeleteTarget(null); void loadData(); }} /> : null}
  </div>;
}
