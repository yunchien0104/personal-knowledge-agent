"use client";

import { ArrowUp, BookOpen, ChevronRight, MessageSquareText, RotateCcw } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { getApi } from "./api";
import type { RetrievalResult } from "./types";
import { PendingState, SourceIcon } from "./ui";

const suggestions = ["最近收藏中，有哪些內容談到 AI 基礎建設？", "整理 Agent 導入評估的共同觀點", "哪些筆記仍缺少可靠來源？"];

function resultKind(result: RetrievalResult) {
  return result.resultType === "source_note" ? "NOTE" as const : "PDF" as const;
}

export function AskPage() {
  const params = useSearchParams();
  const [draft, setDraft] = useState(params.get("q") ?? "");
  const [results, setResults] = useState<RetrievalResult[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("輸入問題後，系統會搜尋你的真實 Source Notes、Claims 與 Evidence。");
  const selected = results.find((item) => item.resultId === activeId) ?? results[0] ?? null;

  useEffect(() => {
    const query = params.get("q");
    if (query) setDraft(query);
  }, [params]);

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = draft.trim();
    if (query.length < 2) {
      setMessage("問題至少需要 2 個字元。");
      return;
    }
    setLoading(true);
    setMessage("");
    try {
      const data = await getApi<RetrievalResult[]>(`/api/retrieval?q=${encodeURIComponent(query)}&limit=20`);
      setResults(data);
      setActiveId(data[0]?.resultId ?? null);
      setMessage(data.length ? "目前只完成知識檢索；答案生成尚未串接。" : "找不到符合的真實知識內容。");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "知識檢索失敗。");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="animate-page">
      <div className="mx-auto mb-5 max-w-4xl">
        <form onSubmit={search} className="flex flex-col gap-3 border-b border-line pb-5 sm:flex-row sm:items-end sm:justify-between"><div className="flex-1"><div className="flex items-end gap-2 rounded-lg border border-line bg-surface p-2 focus-within:border-ink"><textarea value={draft} onChange={(event) => setDraft(event.target.value)} rows={2} aria-label="輸入知識問題" className="min-h-[44px] flex-1 resize-none bg-transparent px-2 py-2 text-sm outline-none placeholder:text-faint" placeholder="輸入問題，或從下方選擇一個方向…" /><button type="submit" disabled={loading} aria-label="送出知識問題" className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-indigo-500 text-white hover:bg-indigo-700 disabled:opacity-50"><ArrowUp className="h-4 w-4" /></button></div></div><div className="shrink-0 rounded-md px-3 py-2 text-xs text-muted">範圍：全部知識</div></form>
        <div className="mt-3 flex flex-wrap gap-2">{suggestions.map((question) => <button type="button" key={question} onClick={() => setDraft(question)} className="rounded-full border border-line bg-surface px-4 py-2.5 text-left text-xs font-medium text-muted shadow-sm transition hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700">{question}</button>)}</div>
      </div>

      <div className="mx-auto max-w-4xl overflow-hidden rounded-lg border border-line bg-surface">
        <div className="grid min-h-[620px] lg:grid-cols-[minmax(0,1.35fr)_360px]">
          <section className="flex min-w-0 flex-col border-b border-line lg:border-b-0 lg:border-r">
            <header className="flex items-center justify-between border-b border-line px-5 py-4"><div><h2 className="text-sm font-semibold">依知識庫回答</h2><p className="mt-1 text-[11px] text-muted">目前範圍：全部知識</p></div><button type="button" aria-label="重新開始對話" onClick={() => { setDraft(""); setResults([]); setActiveId(null); setMessage("輸入問題後，系統會搜尋你的真實 Source Notes、Claims 與 Evidence。"); }} className="rounded-md p-2 text-muted hover:bg-subtle"><RotateCcw className="h-4 w-4" /></button></header>
            <div className="flex-1 px-5 py-6 sm:px-8"><p role="status" className="text-left text-xs font-semibold text-ink">{loading ? "正在搜尋知識庫…" : message}</p>{results.length ? <article className="mt-8 max-w-3xl"><div className="flex items-center gap-2 text-xs font-medium text-muted"><BookOpen className="h-4 w-4" />檢索到 {results.length} 筆內容</div><div className="reading-copy mt-4 space-y-5">{results.slice(0, 5).map((result) => <section key={result.resultId}><h3 className="font-semibold">{result.title}</h3><p className="mt-1 text-sm text-muted">{result.snippet}</p></section>)}</div><p className="mt-5 flex items-center gap-2 text-[11px] text-faint"><MessageSquareText className="h-3.5 w-3.5" />答案生成尚未串接；以上為真實檢索結果，不是 AI 回答。</p></article> : <div className="mt-8"><PendingState title="等待知識問題" description="送出問題後才會呼叫現有 Retrieval API。" /></div>}</div>
          </section>
          <aside className="bg-subtle"><header className="border-b border-line px-5 py-4"><div className="flex items-center justify-between"><h2 className="text-sm font-semibold">引用來源</h2><span className="text-[10px] text-muted">{results.reduce((count, item) => count + item.citations.length, 0)} 筆</span></div><p className="mt-1 text-[11px] text-muted">選擇結果查看可追溯脈絡</p></header><div className="p-4">{results.length ? <><div className="space-y-1">{results.map((result) => <button type="button" key={result.resultId} onClick={() => setActiveId(result.resultId)} className={`flex w-full items-center gap-3 rounded-md p-3 text-left transition ${selected?.resultId === result.resultId ? "bg-surface text-ink" : "text-muted hover:bg-elevated"}`}><SourceIcon kind={resultKind(result)} size="sm" /><div className="min-w-0 flex-1"><p className="truncate text-xs font-semibold">{result.title}</p><p className="mt-1 text-[10px] text-muted">{result.contentOrigin} · {result.citations.length} 引用</p></div><ChevronRight className="h-4 w-4 text-faint" /></button>)}</div>{selected ? <article className="mt-5 border-t border-line pt-5"><p className="text-xs font-medium text-muted">來源預覽</p><h3 className="mt-3 text-lg font-semibold">{selected.title}</h3><p className="mt-1 text-xs text-muted">Revision {selected.revisionId}</p><blockquote className="mt-5 border-l border-ink pl-4 text-sm leading-7 text-ink">{selected.snippet}</blockquote><p className="mt-5 rounded-md bg-amber-50 px-3 py-2.5 text-[11px] leading-5 text-amber-700">{selected.citations.length ? `${selected.citations.length} 筆可追溯 Evidence` : "這筆結果目前沒有 Citation。"}</p></article> : null}</> : <p className="text-xs leading-5 text-muted">尚無檢索結果。</p>}</div></aside>
        </div>
      </div>
    </div>
  );
}
