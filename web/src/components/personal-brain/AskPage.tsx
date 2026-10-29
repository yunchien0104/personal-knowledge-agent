"use client";

import { ArrowUp, BookOpen, ChevronRight, MessageSquareText, RotateCcw } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { instagramCitation } from "@/lib/instagram";
import { instagramReelCitation } from "@/lib/instagram-reel";
import { isResearchReady, safeResearchError, waitForResearchRun } from "@/lib/research";
import { knowledgeResultTitle, knowledgeSourceKind, originLabel } from "@/lib/retrieval";
import { threadsCitationUrl } from "@/lib/threads";

import { getApi } from "./api";
import type { ResearchCitation, ResearchRun, RetrievalResult } from "./types";
import { PendingState, SourceIcon } from "./ui";

const suggestions = ["最近收藏中，有哪些內容談到 AI 基礎建設？", "整理 Agent 導入評估的共同觀點", "哪些筆記仍缺少可靠來源？"];

function resultKind(result: RetrievalResult) {
  return knowledgeSourceKind(result);
}

function citationUrl(citation: ResearchCitation) {
  return threadsCitationUrl(citation.locator)
    ?? instagramCitation(citation.locator)?.sourceUrl
    ?? instagramReelCitation(citation.locator, citation.sourceUrl)?.sourceUrl
    ?? citation.sourceUrl;
}

function citationKind(citation: ResearchCitation) {
  const kind = citation.locator.kind;
  if (kind === "threads_post") return "TH" as const;
  if (typeof kind === "string" && (kind.startsWith("instagram_") || kind === "video_segment")) {
    return "IG" as const;
  }
  return "PDF" as const;
}

export function AskPage() {
  const params = useSearchParams();
  const [draft, setDraft] = useState(params.get("q") ?? "");
  const [results, setResults] = useState<RetrievalResult[]>([]);
  const [answer, setAnswer] = useState<ResearchRun | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("輸入問題後，系統會搜尋你的真實 Source Notes、Claims 與 Evidence。");
  const selected = results.find((item) => item.resultId === activeId) ?? results[0] ?? null;

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = draft.trim();
    if (query.length < 2) {
      setMessage("問題至少需要 2 個字元。");
      return;
    }
    setLoading(true);
    setAnswer(null);
    setMessage("");
    try {
      const [data, created] = await Promise.all([
        getApi<RetrievalResult[]>(`/api/retrieval?q=${encodeURIComponent(query)}&limit=20`),
        getApi<{ id: string }>("/api/research", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ question: query, request_key: crypto.randomUUID() }),
        }),
      ]);
      setResults(data);
      setActiveId(data[0]?.resultId ?? null);
      setMessage(data.length ? "已找到相關知識，正在由既有 Research Worker 產生可追溯回答…" : "正在確認知識庫是否有足夠 Evidence…");
      const run = await waitForResearchRun(
        created.id,
        (id) => getApi<ResearchRun>(`/api/research?id=${encodeURIComponent(id)}`),
      );
      setAnswer(run);
      if (isResearchReady(run.status) && run.note) {
        setMessage(`回答已完成，並綁定 ${run.citations.length} 筆真實 Evidence。`);
      } else {
        setMessage(safeResearchError(run.errorCode) ?? "知識庫沒有足夠證據產生回答。");
      }
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
            <header className="flex items-center justify-between border-b border-line px-5 py-4"><div><h2 className="text-sm font-semibold">依知識庫回答</h2><p className="mt-1 text-[11px] text-muted">Hybrid Retrieval → AI Research → Citation 驗證</p></div><button type="button" aria-label="重新開始對話" onClick={() => { setDraft(""); setResults([]); setAnswer(null); setActiveId(null); setMessage("輸入問題後，系統會搜尋你的真實 Source Notes、Claims 與 Evidence。"); }} className="rounded-md p-2 text-muted hover:bg-subtle"><RotateCcw className="h-4 w-4" /></button></header>
            <div className="flex-1 px-5 py-6 sm:px-8"><p role="status" aria-live="polite" className="text-left text-xs font-semibold text-ink">{loading ? message || "正在搜尋知識庫並產生回答…" : message}</p>{answer?.note ? <article className="mt-8 max-w-3xl"><div className="flex items-center gap-2 text-xs font-medium text-muted"><MessageSquareText className="h-4 w-4" />AI 知識回答 · {answer.citations.length} 筆 Evidence</div><h3 className="mt-4 text-xl font-semibold">{answer.note.title}</h3><div className="reading-copy mt-4 whitespace-pre-wrap">{answer.note.body}</div><p className="mt-5 flex items-center gap-2 text-[11px] text-faint"><BookOpen className="h-3.5 w-3.5" />回答只使用目前帳號可讀的內部 Evidence，且引用已由 Research verification gate 檢查。</p></article> : results.length ? <article className="mt-8 max-w-3xl"><div className="flex items-center gap-2 text-xs font-medium text-muted"><BookOpen className="h-4 w-4" />檢索到 {results.length} 筆內容</div><div className="reading-copy mt-4 space-y-5">{results.slice(0, 5).map((result) => <section key={result.resultId}><h3 className="font-semibold">{knowledgeResultTitle(result)}</h3><p className="mt-1 text-sm text-muted">{result.snippet}</p></section>)}</div><p className="mt-5 flex items-center gap-2 text-[11px] text-faint"><MessageSquareText className="h-3.5 w-3.5" />{loading ? "Research Worker 正在產生可追溯回答。" : "未產生答案；以上僅為原始檢索結果。"}</p></article> : <div className="mt-8"><PendingState title="等待知識問題" description="送出後會依序執行 Retrieval、AI 生成與 Citation 驗證。" /></div>}</div>
          </section>
          <aside className="bg-subtle"><header className="border-b border-line px-5 py-4"><div className="flex items-center justify-between"><h2 className="text-sm font-semibold">引用來源</h2><span className="text-[10px] text-muted">{answer?.citations.length ?? results.reduce((count, item) => count + item.citations.length, 0)} 筆</span></div><p className="mt-1 text-[11px] text-muted">{answer?.citations.length ? "AI 回答實際使用的 Evidence" : "檢索候選內容"}</p></header><div className="p-4">{answer?.citations.length ? <div className="space-y-2">{answer.citations.map((citation) => { const url = citationUrl(citation); return <article key={citation.id} className="rounded-md bg-surface p-3"><div className="flex items-center gap-3"><SourceIcon kind={citationKind(citation)} size="sm" /><div className="min-w-0 flex-1"><p className="truncate text-xs font-semibold">Evidence {citation.evidenceId.slice(0, 8)}</p><p className="mt-1 text-[10px] text-muted">{citation.relationType} · {citation.validityStatus}</p></div></div><blockquote className="mt-3 border-l border-ink pl-3 text-xs leading-5 text-muted">{citation.quote}</blockquote>{url ? <a href={url} target="_blank" rel="noreferrer" className="mt-3 inline-block text-[11px] font-medium text-indigo-700 hover:underline">開啟原始來源</a> : null}</article>; })}</div> : results.length ? <><div className="space-y-1">{results.map((result) => <button type="button" key={result.resultId} onClick={() => setActiveId(result.resultId)} className={`flex w-full items-center gap-3 rounded-md p-3 text-left transition ${selected?.resultId === result.resultId ? "bg-surface text-ink" : "text-muted hover:bg-elevated"}`}><SourceIcon kind={resultKind(result)} size="sm" /><div className="min-w-0 flex-1"><p className="truncate text-xs font-semibold">{knowledgeResultTitle(result)}</p><p className="mt-1 text-[10px] text-muted">{originLabel(result.contentOrigin)} · {result.citations.length} 引用</p></div><ChevronRight className="h-4 w-4 text-faint" /></button>)}</div>{selected ? <article className="mt-5 border-t border-line pt-5"><p className="text-xs font-medium text-muted">來源預覽</p><h3 className="mt-3 text-lg font-semibold">{knowledgeResultTitle(selected)}</h3><p className="mt-1 text-xs text-muted">Revision {selected.revisionId}</p><blockquote className="mt-5 border-l border-ink pl-4 text-sm leading-7 text-ink">{selected.snippet}</blockquote><p className="mt-5 rounded-md bg-amber-50 px-3 py-2.5 text-[11px] leading-5 text-amber-700">{selected.citations.length ? `${selected.citations.length} 筆可追溯 Evidence` : "這筆結果目前沒有 Citation。"}</p></article> : null}</> : <p className="text-xs leading-5 text-muted">尚無檢索結果。</p>}</div></aside>
        </div>
      </div>
    </div>
  );
}
