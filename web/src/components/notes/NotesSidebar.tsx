"use client";

import {
  BookOpen,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  FileSearch,
  FileText,
  FlaskConical,
  Loader2,
  Plus,
  Search,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

import {
  KNOWLEDGE_SOURCE_LABELS,
  REFERENCE_CATEGORY_LABELS,
  type KnowledgeSourceType,
  type ReferenceCategory,
  type ReferenceSource,
} from "@/lib/reference-sources";

export interface NotesSidebarProps {
  isOpen: boolean;
  onClose: () => void;
  currentNoteId?: string;
  onInsertContent?: (text: string) => void;
}

type ImportOperation = "reference" | "partial" | "full";

const PAGE_SIZE = 20;
const categoryIcons = {
  research: FlaskConical,
  knowledge: BookOpen,
  personal: FileText,
} satisfies Record<ReferenceCategory, typeof BookOpen>;

const knowledgeFilters: KnowledgeSourceType[] = [
  "all",
  "pdf",
  "instagram_reel",
  "instagram_post",
  "threads",
];

export function NotesSidebar({
  isOpen,
  onClose,
  currentNoteId,
  onInsertContent,
}: NotesSidebarProps) {
  const [category, setCategory] = useState<ReferenceCategory>("research");
  const [sourceType, setSourceType] = useState<KnowledgeSourceType>("all");
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<ReferenceSource[]>([]);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [loadedRequestKey, setLoadedRequestKey] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [selectionText, setSelectionText] = useState<Record<string, string>>({});
  const [importingKey, setImportingKey] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState("");
  const requestKey = `${category}:${sourceType}:${query}:${currentNoteId ?? ""}`;
  const loading = isOpen && loadedRequestKey !== requestKey;

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(searchInput.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    if (!isOpen) return;
    const controller = new AbortController();
    const params = new URLSearchParams({
      category,
      sourceType: category === "knowledge" ? sourceType : "all",
      query,
      limit: String(PAGE_SIZE),
      offset: "0",
    });
    if (currentNoteId) params.set("currentNoteId", currentNoteId);

    fetch(`/api/personal-notes/references?${params}`, { signal: controller.signal })
      .then(async (response) => {
        const json = await response.json();
        if (!response.ok) throw new Error(json?.error?.message ?? "載入引用來源失敗");
        return json as { data?: ReferenceSource[]; meta?: { total?: number } };
      })
      .then((json) => {
        setItems(json.data ?? []);
        setTotal(json.meta?.total ?? 0);
        setOffset(json.data?.length ?? 0);
        setExpandedId(null);
        setErrorMessage("");
        setLoadedRequestKey(requestKey);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setItems([]);
        setTotal(0);
        setErrorMessage(error instanceof Error ? error.message : "載入引用來源失敗");
        setLoadedRequestKey(requestKey);
      });
    return () => controller.abort();
  }, [category, currentNoteId, isOpen, query, requestKey, sourceType]);

  async function loadMore() {
    if (loadingMore || items.length >= total) return;
    setLoadingMore(true);
    setErrorMessage("");
    const params = new URLSearchParams({
      category,
      sourceType: category === "knowledge" ? sourceType : "all",
      query,
      limit: String(PAGE_SIZE),
      offset: String(offset),
    });
    if (currentNoteId) params.set("currentNoteId", currentNoteId);
    try {
      const response = await fetch(`/api/personal-notes/references?${params}`);
      const json = await response.json();
      if (!response.ok) throw new Error(json?.error?.message ?? "載入更多來源失敗");
      const next = (json.data ?? []) as ReferenceSource[];
      setItems((current) => [...current, ...next]);
      setOffset((current) => current + next.length);
      setTotal(json.meta?.total ?? total);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "載入更多來源失敗");
    } finally {
      setLoadingMore(false);
    }
  }

  async function insertSource(item: ReferenceSource, operation: ImportOperation) {
    if (!currentNoteId) {
      setErrorMessage("請先儲存目前筆記，再插入引用。");
      return;
    }
    const partialText = selectionText[item.id]?.trim();
    if (operation === "partial" && !partialText) {
      setErrorMessage("請先在預覽區輸入或貼上要插入的來源片段。");
      return;
    }

    const key = `${item.id}:${operation}`;
    setImportingKey(key);
    setErrorMessage("");
    try {
      const response = await fetch("/api/personal-notes/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          note_id: currentNoteId,
          source_kind: category,
          source_id: item.id,
          operation,
          ...(partialText ? { selection_text: partialText } : {}),
        }),
      });
      const json = await response.json();
      if (!response.ok) throw new Error(json?.error?.message ?? "插入引用失敗");

      const date = new Date(item.created_at).toLocaleDateString("zh-TW");
      const sourceLabel = item.source_type
        ? KNOWLEDGE_SOURCE_LABELS[item.source_type]
        : REFERENCE_CATEGORY_LABELS[item.category];
      if (operation === "reference") {
        onInsertContent?.(`【${sourceLabel}：${item.title}｜${date}】`);
      } else if (operation === "partial") {
        onInsertContent?.(`${partialText}\n\n— 引用自《${item.title}》`);
      } else {
        onInsertContent?.(`${item.content}\n\n— 匯入自《${item.title}》`);
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "插入引用失敗");
    } finally {
      setImportingKey(null);
    }
  }

  if (!isOpen) return null;

  return (
    <aside
      aria-label="插入引用"
      className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-neutral-200 bg-white shadow-2xl dark:border-neutral-800 dark:bg-neutral-900"
    >
      <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3 dark:border-neutral-800">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-neutral-900 dark:text-neutral-100">
          <FileSearch className="h-4 w-4 text-indigo-600 dark:text-indigo-400" />
          插入引用
        </h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="關閉引用側欄"
          className="rounded p-1.5 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-800 focus-visible:outline-2 focus-visible:outline-indigo-500 dark:hover:bg-neutral-800 dark:hover:text-neutral-100"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid grid-cols-3 border-b border-neutral-200 dark:border-neutral-800">
        {(Object.keys(REFERENCE_CATEGORY_LABELS) as ReferenceCategory[]).map((value) => {
          const Icon = categoryIcons[value];
          return (
            <button
              type="button"
              key={value}
              onClick={() => {
                setCategory(value);
                if (value !== "knowledge") setSourceType("all");
              }}
              className={`flex items-center justify-center gap-1.5 border-b-2 px-2 py-2.5 text-xs transition focus-visible:outline-2 focus-visible:outline-indigo-500 ${
                category === value
                  ? "border-indigo-600 font-semibold text-indigo-600 dark:border-indigo-400 dark:text-indigo-400"
                  : "border-transparent text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-200"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {REFERENCE_CATEGORY_LABELS[value]}
            </button>
          );
        })}
      </div>

      {category === "knowledge" && (
        <div className="flex gap-1.5 overflow-x-auto border-b border-neutral-200 px-3 py-2 dark:border-neutral-800">
          {knowledgeFilters.map((value) => (
            <button
              type="button"
              key={value}
              onClick={() => setSourceType(value)}
              className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] focus-visible:outline-2 focus-visible:outline-indigo-500 ${
                sourceType === value
                  ? "bg-indigo-600 text-white dark:bg-indigo-500"
                  : "bg-neutral-100 text-neutral-600 hover:bg-neutral-200 dark:bg-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-700"
              }`}
            >
              {KNOWLEDGE_SOURCE_LABELS[value]}
            </button>
          ))}
        </div>
      )}

      <div className="border-b border-neutral-200 p-3 dark:border-neutral-800">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-neutral-400" />
          <input
            type="search"
            placeholder="搜尋標題或內容…"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            className="w-full rounded-md border border-neutral-200 bg-neutral-50 py-2 pl-8 pr-3 text-xs text-neutral-900 outline-none placeholder:text-neutral-400 focus-visible:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500/20 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-100"
          />
        </div>
        <div className="mt-2 flex items-center justify-between text-[10px] text-neutral-400">
          <span>最新加入優先</span>
          <span>{total} 筆</span>
        </div>
        {errorMessage && (
          <p role="alert" className="mt-2 rounded bg-red-50 px-2 py-1.5 text-[11px] text-red-700 dark:bg-red-950/40 dark:text-red-300">
            {errorMessage}
          </p>
        )}
      </div>

      <div className="flex-1 space-y-2.5 overflow-y-auto p-3">
        {loading ? (
          <div className="flex h-40 flex-col items-center justify-center gap-2 text-xs text-neutral-400">
            <Loader2 className="h-5 w-5 animate-spin text-indigo-600" />
            正在載入引用來源…
          </div>
        ) : items.length === 0 ? (
          <div className="py-12 text-center text-xs text-neutral-400">
            查無符合條件的{REFERENCE_CATEGORY_LABELS[category]}
          </div>
        ) : (
          items.map((item) => {
            const expanded = expandedId === item.id;
            return (
              <article key={item.id} className="rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-950">
                <div className="flex items-start justify-between gap-2">
                  <h3 className="line-clamp-2 text-xs font-semibold text-neutral-900 dark:text-neutral-100">{item.title}</h3>
                  {item.source_type && (
                    <span className="shrink-0 rounded bg-neutral-100 px-1.5 py-0.5 text-[10px] text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
                      {KNOWLEDGE_SOURCE_LABELS[item.source_type]}
                    </span>
                  )}
                </div>
                <p className="mt-1.5 line-clamp-3 text-[11px] leading-relaxed text-neutral-500 dark:text-neutral-400">
                  {item.summary || "（無摘要）"}
                </p>
                <div className="mt-2 flex items-center justify-between text-[10px] text-neutral-400">
                  <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleDateString("zh-TW")}</time>
                  <button
                    type="button"
                    onClick={() => setExpandedId(expanded ? null : item.id)}
                    className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-neutral-600 hover:bg-neutral-100 focus-visible:outline-2 focus-visible:outline-indigo-500 dark:text-neutral-300 dark:hover:bg-neutral-800"
                  >
                    {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                    預覽
                  </button>
                </div>

                {expanded && (
                  <div className="mt-2 border-t border-neutral-100 pt-2 dark:border-neutral-800">
                    <div className="max-h-48 overflow-y-auto whitespace-pre-wrap rounded bg-neutral-50 p-2 text-[11px] leading-relaxed text-neutral-600 dark:bg-neutral-900 dark:text-neutral-300">
                      {item.content || "（無內容）"}
                    </div>
                    {item.source_url && (
                      <a
                        href={item.source_url}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-2 inline-flex items-center gap-1 text-[10px] text-indigo-600 hover:underline dark:text-indigo-400"
                      >
                        <ExternalLink className="h-3 w-3" />
                        原始來源
                      </a>
                    )}
                    <label className="mt-2 block text-[10px] font-medium text-neutral-500 dark:text-neutral-400">
                      要插入的部分內容
                      <textarea
                        value={selectionText[item.id] ?? ""}
                        onChange={(event) => setSelectionText((current) => ({ ...current, [item.id]: event.target.value }))}
                        placeholder="從上方內容複製要插入的片段"
                        rows={3}
                        className="mt-1 w-full resize-y rounded border border-neutral-200 bg-white p-2 text-[11px] text-neutral-800 outline-none focus-visible:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500/20 dark:border-neutral-700 dark:bg-neutral-950 dark:text-neutral-200"
                      />
                    </label>
                  </div>
                )}

                <div className="mt-3 flex flex-wrap gap-1.5 border-t border-neutral-100 pt-2 dark:border-neutral-800">
                  {([
                    ["reference", "插入引用"],
                    ["partial", "插入部分"],
                    ["full", "匯入整篇"],
                  ] as const).map(([operation, label]) => {
                    const key = `${item.id}:${operation}`;
                    return (
                      <button
                        type="button"
                        key={operation}
                        disabled={importingKey !== null}
                        onClick={() => void insertSource(item, operation)}
                        className="inline-flex items-center gap-1 rounded bg-indigo-50 px-2 py-1 text-[11px] font-medium text-indigo-700 hover:bg-indigo-100 disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-indigo-500 dark:bg-indigo-950/60 dark:text-indigo-300"
                      >
                        {importingKey === key ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
                        {label}
                      </button>
                    );
                  })}
                </div>
              </article>
            );
          })
        )}

        {!loading && items.length < total && (
          <button
            type="button"
            disabled={loadingMore}
            onClick={() => void loadMore()}
            className="flex w-full items-center justify-center gap-1.5 rounded-md border border-neutral-200 py-2 text-xs text-neutral-600 hover:bg-neutral-50 disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-indigo-500 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            {loadingMore && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            載入更多
          </button>
        )}
      </div>
    </aside>
  );
}
