"use client";

import { Grid2X2, List, Search, SlidersHorizontal, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { getApi, toKnowledgeItems } from "./api";
import { DeleteAssetDialog } from "./DeleteAssetDialog";
import type { Asset, AssetDeletion, KnowledgeItem, Note, SourceKind } from "./types";
import { PageTitle, PendingState, SourceIcon, StatusBadge } from "./ui";

type FilterType = "All" | "Notes" | "Sources";
type ViewMode = "grid" | "list";
type SourceFilter = "recent" | "IG" | "TH" | "PDF";
const filters: Array<{ value: FilterType; label: string }> = [{ value: "All", label: "全部" }, { value: "Notes", label: "筆記" }, { value: "Sources", label: "來源" }];
const kindName: Record<SourceKind, string> = { PDF: "PDF 文件", IG: "Instagram", TH: "Threads", NOTE: "個人筆記" };
const sourceFilterOptions: Array<{ value: SourceFilter; label: string }> = [{ value: "recent", label: "最新加入" }, { value: "IG", label: "IG Reels" }, { value: "TH", label: "Threads" }, { value: "PDF", label: "PDF" }];

export function LibraryPage() {
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [filter, setFilter] = useState<FilterType>("All");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [sourceFilterOpen, setSourceFilterOpen] = useState(false);
  const [activeSourceFilter, setActiveSourceFilter] = useState<SourceFilter | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletions, setDeletions] = useState<AssetDeletion[]>([]);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeItem | null>(null);

  const loadData = useCallback(async () => {
    const [assets, notes, deletionRows] = await Promise.all([
      getApi<Asset[]>("/api/assets?limit=100"),
      getApi<Note[]>("/api/notes?limit=100"),
      getApi<AssetDeletion[]>("/api/assets/deletions"),
    ]);
    setItems(toKnowledgeItems(assets, notes));
    setDeletions(deletionRows);
    setError(null);
  }, []);

  useEffect(() => {
    let active = true;
    void loadData()
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "知識庫讀取失敗。"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [loadData]);

  const hasActiveDeletion = deletions.some((item) => ["pending", "deleting"].includes(item.status));
  useEffect(() => {
    if (!hasActiveDeletion) return;
    const timer = window.setInterval(() => {
      void loadData().catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "刪除狀態讀取失敗。");
      });
    }, 2500);
    return () => window.clearInterval(timer);
  }, [hasActiveDeletion, loadData]);

  const filtered = useMemo(() => items.filter((item) => (
    (filter === "All" || (filter === "Notes" ? item.category === "note" : item.category === "source"))
    && (!activeSourceFilter || (activeSourceFilter === "recent" ? Date.now() - new Date(item.updatedAt).getTime() < 7 * 86400000 : item.kind === activeSourceFilter))
    && (!query.trim() || `${item.title} ${item.excerpt}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  )), [activeSourceFilter, filter, items, query]);
  const selected = filtered.find((item) => item.id === selectedId) ?? filtered[0] ?? null;
  const selectedDeletion = selected
    ? deletions.find((item) => item.asset_id_snapshot === selected.id)
    : null;

  function handleDeletionRequested(deletion: AssetDeletion) {
    setDeletions((current) => [deletion, ...current.filter((item) => item.id !== deletion.id)]);
    setDeleteTarget(null);
    void loadData().catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "刪除狀態讀取失敗。");
    });
  }

  return (
    <div className="animate-page">
      <PageTitle title="知識庫" />
      <section aria-label="主題">
        <div className="flex justify-end pb-3"><span className="text-xs font-medium text-muted">共 {items.length} 筆真實資料</span></div>
        <div className="grid gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-4">
          <button type="button" className="min-h-36 bg-surface p-4 text-left ring-1 ring-inset ring-ink"><div className="grid grid-cols-3 gap-1">{["PDF", "TH", "NOTE"].map((label) => <span key={label} className="grid aspect-square place-items-center rounded-sm border border-line bg-subtle text-[9px] text-muted">{label}</span>)}</div><h3 className="mt-4 text-sm font-semibold">全部知識</h3><p className="mt-1 text-[11px] text-muted">{items.length} 個項目</p></button>
          <div className="grid min-h-36 place-items-center bg-surface p-4 text-center sm:col-span-3"><div><p className="text-sm font-semibold">產業主題分類尚未串接</p><p className="mt-2 text-xs text-muted">Topics API 完成後才會顯示真實分類。</p></div></div>
        </div>
      </section>

      <section className="mt-9">
        <div className="flex flex-col gap-3 border-b border-line pb-3 sm:flex-row sm:items-center">
          <div className="flex min-w-0 flex-1 items-center gap-2 rounded-md bg-subtle px-3 py-2.5 focus-within:ring-1 focus-within:ring-ink"><Search className="h-4 w-4 text-muted" /><input value={query} onChange={(event) => setQuery(event.target.value)} aria-label="搜尋 Library" className="min-w-0 flex-1 bg-transparent text-xs outline-none placeholder:text-faint" placeholder="搜尋所有收藏…" /></div>
          <div className="flex items-center gap-1 overflow-x-auto">
            {filters.map((item) => <button type="button" key={item.value} onClick={() => setFilter(item.value)} className={`shrink-0 rounded-md px-3 py-2 text-xs font-medium ${filter === item.value ? "bg-elevated text-ink" : "text-muted hover:bg-subtle hover:text-ink"}`}>{item.label}</button>)}
            <div className="relative shrink-0"><button type="button" aria-expanded={sourceFilterOpen} aria-haspopup="menu" onClick={() => setSourceFilterOpen((open) => !open)} className={`flex items-center gap-1 rounded-md px-3 py-2 text-xs hover:bg-subtle ${activeSourceFilter ? "bg-indigo-50 text-indigo-700" : "text-muted"}`}><SlidersHorizontal className="h-3.5 w-3.5" />篩選{activeSourceFilter ? ` · ${sourceFilterOptions.find((item) => item.value === activeSourceFilter)?.label}` : ""}</button>{sourceFilterOpen ? <div role="menu" aria-label="內容篩選" className="absolute right-0 top-10 z-20 w-40 rounded-lg border border-line bg-surface p-1.5 shadow-soft">{sourceFilterOptions.map((item) => <button type="button" key={item.value} role="menuitemradio" aria-checked={activeSourceFilter === item.value} onClick={() => { setActiveSourceFilter((current) => current === item.value ? null : item.value); setSourceFilterOpen(false); }} className={`flex w-full items-center justify-between rounded-md px-2.5 py-2 text-left text-xs transition hover:bg-subtle ${activeSourceFilter === item.value ? "text-indigo-700" : "text-muted"}`}><span>{item.label}</span><span className={`h-2 w-2 rounded-full ${activeSourceFilter === item.value ? "bg-indigo-500" : "border border-line"}`} /></button>)}</div> : null}</div>
            <span className="mx-1 h-5 w-px bg-line" /><button type="button" aria-label="網格檢視" aria-pressed={viewMode === "grid"} onClick={() => setViewMode("grid")} className={`rounded-md p-2 ${viewMode === "grid" ? "bg-elevated text-ink" : "text-muted hover:bg-subtle"}`}><Grid2X2 className="h-4 w-4" /></button><button type="button" aria-label="列表檢視" aria-pressed={viewMode === "list"} onClick={() => setViewMode("list")} className={`rounded-md p-2 ${viewMode === "list" ? "bg-elevated text-ink" : "text-muted hover:bg-subtle"}`}><List className="h-4 w-4" /></button>
          </div>
        </div>
        {error ? <p role="alert" className="mt-5 text-sm text-rose-700">{error}</p> : null}
        <div className="grid gap-8 pt-5 xl:grid-cols-[minmax(0,1fr)_330px]">
          <div><div className="mb-4 flex items-center justify-between"><h2 className="text-sm font-semibold">全部知識</h2><span className="text-[11px] text-muted">{filtered.length} 個項目</span></div>
            {loading ? <p className="py-8 text-sm text-muted">正在讀取知識庫…</p> : null}
            {!loading && filtered.length && viewMode === "grid" ? <div role="region" aria-label="知識網格" className="grid gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-2 2xl:grid-cols-3">{filtered.map((item) => <button type="button" key={item.id} onClick={() => setSelectedId(item.id)} className={`group min-h-64 bg-surface p-4 text-left transition hover:bg-subtle ${selected?.id === item.id ? "ring-1 ring-inset ring-ink" : ""}`}><div className="flex h-24 items-center justify-center rounded-md bg-subtle"><SourceIcon kind={item.kind} size="lg" /></div><p className="mt-4 text-[10px] text-muted">{kindName[item.kind]}</p><h3 className="mt-2 line-clamp-2 text-sm font-semibold leading-5">{item.title}</h3><p className="mt-2 line-clamp-2 text-xs leading-5 text-muted">{item.excerpt}</p><div className="mt-4 flex items-center justify-between gap-2"><span className="truncate text-[10px] text-faint">{item.meta}</span><StatusBadge status={item.status} /></div></button>)}</div> : null}
            {!loading && filtered.length && viewMode === "list" ? <div role="region" aria-label="知識列表" className="divide-y divide-line border-y border-line">{filtered.map((item) => <button type="button" key={item.id} onClick={() => setSelectedId(item.id)} className={`grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 px-2 py-4 text-left transition hover:bg-subtle ${selected?.id === item.id ? "bg-subtle" : ""}`}><SourceIcon kind={item.kind} size="sm" /><div className="min-w-0"><h3 className="truncate text-sm font-semibold">{item.title}</h3><p className="mt-1 truncate text-[11px] text-muted">{kindName[item.kind]} · {item.meta}</p></div><StatusBadge status={item.status} /></button>)}</div> : null}
            {!loading && !filtered.length ? <PendingState title="這個篩選沒有內容" description="沒有符合條件的真實資料；可清除搜尋或篩選後再試。" /> : null}
          </div>
          <article className="self-start border-t border-line pt-5 xl:sticky xl:top-20 xl:border-l xl:border-t-0 xl:pl-7 xl:pt-0">{selected ? <><div className="flex items-start justify-between gap-3"><SourceIcon kind={selected.kind} size="lg" />{selected.category === "source" && selected.kind === "PDF" ? <button type="button" aria-label="刪除 PDF" disabled={selectedDeletion?.status === "pending" || selectedDeletion?.status === "deleting"} onClick={() => setDeleteTarget(selected)} className="rounded-md p-2 text-muted transition hover:bg-rose-50 hover:text-rose-700 disabled:cursor-not-allowed disabled:opacity-40"><Trash2 className="h-4 w-4" /></button> : null}</div><p className="mt-5 text-[11px] text-muted">{selected.category === "note" ? "可編輯筆記" : kindName[selected.kind]}</p><h2 className="mt-2 text-xl font-semibold leading-8 tracking-[-0.02em]">{selected.title}</h2><p className="mt-2 text-xs text-muted">{selected.meta}</p>{selectedDeletion ? <div className={`mt-5 rounded-lg border p-3 text-xs leading-5 ${selectedDeletion.status === "failed" ? "border-rose-200 bg-rose-50 text-rose-800" : "border-amber-200 bg-amber-50 text-amber-900"}`}><p className="font-semibold">{selectedDeletion.status === "pending" ? "等待後端刪除" : selectedDeletion.status === "deleting" ? "後端正在刪除" : selectedDeletion.status === "failed" ? "刪除失敗，可重新嘗試" : selectedDeletion.mode === "remove_original" ? "原始 PDF 已移除" : "永久刪除已完成"}</p>{selectedDeletion.status === "failed" ? <p className="mt-1">{selectedDeletion.error_message ?? "後端未能完成清理，資料仍維持不可檢索狀態。"}</p> : null}</div> : null}<div className="mt-6 border-y border-line py-5"><p className="text-[11px] font-medium text-muted">內容摘要</p><p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-ink">{selected.excerpt}</p></div><p className="mt-6 text-[11px] leading-5 text-faint">只顯示目前 API 回傳內容；引用統計與主題關聯尚未串接。</p></> : <PendingState title="尚未選擇內容" description="選擇左側項目後查看詳細資料。" />}</article>
        </div>
      </section>
      {deleteTarget ? <DeleteAssetDialog item={deleteTarget} onClose={() => setDeleteTarget(null)} onRequested={handleDeletionRequested} /> : null}
    </div>
  );
}
