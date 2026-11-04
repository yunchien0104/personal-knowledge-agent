"use client";

import React, { useState, useEffect, useMemo } from "react";
import Link from "next/link";
import {
  Plus,
  Search,
  Grid,
  List,
  FileText,
  Clock,
  Trash2,
  AlertCircle,
  RefreshCw,
  FolderOpen,
} from "lucide-react";

interface NoteSummary {
  id: string;
  title: string;
  updated_at: string;
  created_at: string;
  snippet?: string;
}

export default function NotesPage() {
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortBy, setSortBy] = useState<"updated_at" | "created_at" | "title">("updated_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");

  const [notes, setNotes] = useState<NoteSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  useEffect(() => {
    let active = true;
    fetch("/api/personal-notes")
      .then((res) => {
        if (!res.ok) throw new Error(`載入失敗 (${res.status})`);
        return res.json();
      })
      .then((json: { data?: { notes?: NoteSummary[] } }) => {
        if (!active) return;
        const list = json?.data?.notes || [];
        setNotes(list);
      })
      .catch((err: unknown) => {
        if (!active) return;
        const message = err instanceof Error ? err.message : "無法自伺服器取得筆記清單";
        setErrorMsg(message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [reloadKey]);

  async function handleDeleteNote(id: string) {
    setIsDeleting(true);
    try {
      const res = await fetch(`/api/personal-notes/${id}`, { method: "DELETE" });
      if (!res.ok) {
        throw new Error("刪除失敗");
      }
      setNotes((prev) => prev.filter((n) => n.id !== id));
      setDeletingId(null);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "刪除發生錯誤";
      alert(message);
    } finally {
      setIsDeleting(false);
    }
  }

  const filteredAndSortedNotes = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    const result = notes.filter((n) => {
      if (!q) return true;
      const matchTitle = n.title?.toLowerCase().includes(q);
      const matchSnippet = n.snippet?.toLowerCase().includes(q);
      return matchTitle || matchSnippet;
    });

    result.sort((a, b) => {
      const valA = a[sortBy] || "";
      const valB = b[sortBy] || "";
      if (sortBy === "title") {
        return sortOrder === "asc"
          ? valA.localeCompare(valB, "zh-Hant")
          : valB.localeCompare(valA, "zh-Hant");
      }
      const timeA = new Date(valA).getTime() || 0;
      const timeB = new Date(valB).getTime() || 0;
      return sortOrder === "asc" ? timeA - timeB : timeB - timeA;
    });

    return result;
  }, [notes, searchQuery, sortBy, sortOrder]);

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-neutral-900 dark:text-neutral-100">
            我的筆記工作區
          </h1>
          <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
            支援富文字、動態試算表與來源匯入，即時雙表同步儲存
          </p>
        </div>

        <Link
          href="/notes/new"
          className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-neutral-900 px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-neutral-800 transition dark:bg-neutral-100 dark:text-neutral-900 dark:hover:bg-neutral-200"
        >
          <Plus className="h-4 w-4" />
          建立新筆記
        </Link>
      </div>

      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-neutral-400" />
          <input
            type="text"
            placeholder="搜尋筆記標題或內文..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full rounded-lg border border-neutral-200 bg-white py-1.5 pl-9 pr-3 text-xs text-neutral-900 placeholder-neutral-400 focus:border-neutral-400 focus:outline-none dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100"
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as "updated_at" | "created_at" | "title")}
            className="rounded-lg border border-neutral-200 bg-white px-2.5 py-1.5 text-xs text-neutral-700 focus:outline-none dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-300"
          >
            <option value="updated_at">依更新時間</option>
            <option value="created_at">依建立時間</option>
            <option value="title">依標題文字</option>
          </select>

          <button
            onClick={() => setSortOrder((prev) => (prev === "asc" ? "desc" : "asc"))}
            className="rounded-lg border border-neutral-200 bg-white px-2.5 py-1.5 text-xs text-neutral-700 hover:bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            {sortOrder === "desc" ? "新到舊 ↓" : "舊到新 ↑"}
          </button>

          <div className="flex rounded-lg border border-neutral-200 bg-neutral-100 p-0.5 dark:border-neutral-800 dark:bg-neutral-800">
            <button
              onClick={() => setViewMode("grid")}
              className={`rounded px-2 py-1 text-xs ${
                viewMode === "grid"
                  ? "bg-white font-medium text-neutral-900 shadow-sm dark:bg-neutral-900 dark:text-neutral-100"
                  : "text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
              }`}
              title="卡片檢視"
            >
              <Grid className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={() => setViewMode("list")}
              className={`rounded px-2 py-1 text-xs ${
                viewMode === "list"
                  ? "bg-white font-medium text-neutral-900 shadow-sm dark:bg-neutral-900 dark:text-neutral-100"
                  : "text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
              }`}
              title="清單檢視"
            >
              <List className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="animate-pulse rounded-xl border border-neutral-200 bg-neutral-50 p-5 dark:border-neutral-800 dark:bg-neutral-900/40"
            >
              <div className="h-4 w-3/4 rounded bg-neutral-200 dark:bg-neutral-800 mb-3" />
              <div className="h-3 w-full rounded bg-neutral-200 dark:bg-neutral-800 mb-2" />
              <div className="h-3 w-5/6 rounded bg-neutral-200 dark:bg-neutral-800 mb-4" />
              <div className="h-2.5 w-1/3 rounded bg-neutral-200 dark:bg-neutral-800" />
            </div>
          ))}
        </div>
      ) : errorMsg ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-red-200 bg-red-50/50 p-8 text-center dark:border-red-900/50 dark:bg-red-950/20">
          <AlertCircle className="h-8 w-8 text-red-500 mb-2" />
          <p className="text-sm font-medium text-red-600 dark:text-red-400 mb-3">{errorMsg}</p>
          <button
            onClick={() => {
              setLoading(true);
              setErrorMsg(null);
              setReloadKey((prev) => prev + 1);
            }}
            className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            重新載入
          </button>
        </div>
      ) : notes.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-neutral-200 p-12 text-center dark:border-neutral-800">
          <FolderOpen className="h-10 w-10 text-neutral-400 mb-3" />
          <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
            目前尚無任何筆記
          </h3>
          <p className="mt-1 text-xs text-neutral-500 max-w-sm">
            點擊上方按鈕建立第一篇筆記，體驗富文字編輯、Excel 計算表與智慧匯入。
          </p>
          <Link
            href="/notes/new"
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-neutral-900 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-neutral-800 dark:bg-neutral-100 dark:text-neutral-900"
          >
            <Plus className="h-4 w-4" />
            立即建立
          </Link>
        </div>
      ) : filteredAndSortedNotes.length === 0 ? (
        <div className="rounded-xl border border-neutral-200 bg-white p-8 text-center text-xs text-neutral-500 dark:border-neutral-800 dark:bg-neutral-900">
          找不到符合「<span className="font-semibold text-neutral-900 dark:text-neutral-100">{searchQuery}</span>」的筆記。
        </div>
      ) : viewMode === "grid" ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filteredAndSortedNotes.map((note) => (
            <div
              key={note.id}
              className="group relative flex flex-col justify-between rounded-xl border border-neutral-200 bg-white p-5 transition hover:border-neutral-300 hover:shadow-sm dark:border-neutral-800 dark:bg-neutral-900 dark:hover:border-neutral-700"
            >
              <Link href={`/notes/${note.id}`} className="flex-1">
                <div className="flex items-start gap-2.5">
                  <FileText className="h-4 w-4 shrink-0 text-neutral-400 group-hover:text-neutral-900 dark:group-hover:text-neutral-100 transition mt-0.5" />
                  <h3 className="text-sm font-semibold text-neutral-900 group-hover:text-indigo-600 transition dark:text-neutral-100 dark:group-hover:text-indigo-400 line-clamp-1">
                    {note.title || "未命名筆記"}
                  </h3>
                </div>
                <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400 line-clamp-3 leading-relaxed">
                  {note.snippet || "點擊進入開始書寫內容..."}
                </p>
              </Link>

              <div className="mt-4 flex items-center justify-between border-t border-neutral-100 pt-3 text-[11px] text-neutral-400 dark:border-neutral-800/60">
                <span className="flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  {note.updated_at ? new Date(note.updated_at).toLocaleDateString("zh-TW") : "剛剛"}
                </span>
                <button
                  onClick={(e) => {
                    e.preventDefault();
                    setDeletingId(note.id);
                  }}
                  className="rounded p-1 text-neutral-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40 dark:hover:text-red-400 transition"
                  title="刪除筆記"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="divide-y divide-neutral-200 rounded-xl border border-neutral-200 bg-white dark:divide-neutral-800 dark:border-neutral-800 dark:bg-neutral-900">
          {filteredAndSortedNotes.map((note) => (
            <div
              key={note.id}
              className="group flex items-center justify-between px-4 py-3.5 transition hover:bg-neutral-50 dark:hover:bg-neutral-800/40"
            >
              <Link href={`/notes/${note.id}`} className="flex-1 min-w-0 pr-4">
                <div className="flex items-center gap-2">
                  <FileText className="h-4 w-4 shrink-0 text-neutral-400 group-hover:text-neutral-900 dark:group-hover:text-neutral-100 transition" />
                  <span className="truncate text-xs font-semibold text-neutral-900 group-hover:text-indigo-600 transition dark:text-neutral-100 dark:group-hover:text-indigo-400">
                    {note.title || "未命名筆記"}
                  </span>
                </div>
                {note.snippet && (
                  <p className="mt-1 truncate text-[11px] text-neutral-400 pl-6">
                    {note.snippet}
                  </p>
                )}
              </Link>

              <div className="flex items-center gap-4 text-xs text-neutral-400 shrink-0">
                <span className="hidden sm:inline-block text-[11px]">
                  {note.updated_at ? new Date(note.updated_at).toLocaleDateString("zh-TW") : "剛剛"}
                </span>
                <button
                  onClick={() => setDeletingId(note.id)}
                  className="rounded p-1 text-neutral-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40 dark:hover:text-red-400 transition"
                  title="刪除筆記"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {deletingId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 backdrop-blur-sm">
          <div className="w-full max-w-sm rounded-xl border border-neutral-200 bg-white p-5 shadow-lg dark:border-neutral-800 dark:bg-neutral-900">
            <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
              確認刪除筆記？
            </h3>
            <p className="mt-2 text-xs text-neutral-500 leading-relaxed">
              此操作將從 Supabase 雙表中永久移除該筆記及其所有歷史文件快照，無法復原。
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                disabled={isDeleting}
                onClick={() => setDeletingId(null)}
                className="rounded-lg border border-neutral-200 px-3 py-1.5 text-xs font-medium text-neutral-700 hover:bg-neutral-50 dark:border-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-800"
              >
                取消
              </button>
              <button
                disabled={isDeleting}
                onClick={() => handleDeleteNote(deletingId)}
                className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-50"
              >
                {isDeleting ? "刪除中..." : "確認刪除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
