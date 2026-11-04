"use client";

import { AlertCircle, CheckCircle2, FileText, Flag, Play, Save, Tags } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { getApi } from "./api";
import { instagramCitation } from "@/lib/instagram";
import { instagramReelCitation } from "@/lib/instagram-reel";
import { isResearchTerminal, researchWorkflowProgress } from "@/lib/research";
import { threadsCitationUrl } from "@/lib/threads";
import type { ResearchRun, ResearchRunSummary, Topic } from "./types";
import { PageTitle, PendingState, PrimaryButton, SourceIcon } from "./ui";

function researchError(code: string | null) {
  const messages: Record<string, string> = {
    insufficient_evidence: "內部知識庫找不到足夠的原始 Evidence；Research Draft 未建立。",
    unsupported_research_finding: "研究結果無法由逐字原始引用支持；Research Draft 未建立。",
    invalid_research_plan: "研究計畫無效，未執行研究。",
  };
  return code ? messages[code] ?? "Research Agent 執行失敗，請稍後重試。" : null;
}

function projectDate(value: string) {
  return new Date(value).toLocaleDateString("zh-TW", { year: "numeric", month: "2-digit", day: "2-digit" });
}

function TopicPill({ name }: { name: string }) {
  return <span className="rounded-full bg-indigo-50 px-2.5 py-1 text-[11px] font-medium text-indigo-700">{name}</span>;
}

export function ResearchPage() {
  const params = useSearchParams();
  const [question, setQuestion] = useState(params.get("q") ?? "");
  const [runs, setRuns] = useState<ResearchRunSummary[]>([]);
  const [selected, setSelected] = useState<ResearchRun | null>(null);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [topicEditorOpen, setTopicEditorOpen] = useState(false);
  const [draftTopicIds, setDraftTopicIds] = useState<string[]>([]);
  const [draftNoteId, setDraftNoteId] = useState<string | null>(null);
  const [savingTopics, setSavingTopics] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refreshRuns = useCallback(async () => {
    const data = await getApi<ResearchRunSummary[]>("/api/research");
    setRuns(data);
    return data;
  }, []);

  const loadRun = useCallback(async (id: string) => {
    const data = await getApi<ResearchRun>(`/api/research?id=${encodeURIComponent(id)}`);
    setSelected(data);
    return data;
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([getApi<ResearchRunSummary[]>("/api/research"), getApi<Topic[]>("/api/topics")])
      .then(async ([runRows, topicRows]) => {
        if (!active) return;
        setRuns(runRows);
        setTopics(topicRows);
        if (runRows[0]) {
          const run = await getApi<ResearchRun>(`/api/research?id=${encodeURIComponent(runRows[0].id)}`);
          if (active) setSelected(run);
        }
      })
      .catch((reason: unknown) => {
        if (active) setMessage(reason instanceof Error ? reason.message : "研究資料讀取失敗。");
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!selected || isResearchTerminal(selected.status)) return;
    const runId = selected.id;
    const timer = window.setInterval(() => {
      void loadRun(runId).then((run) => {
        if (isResearchTerminal(run.status)) void refreshRuns();
      }).catch((reason: unknown) => {
        setMessage(reason instanceof Error ? reason.message : "研究進度更新失敗。");
      });
    }, 2_500);
    return () => window.clearInterval(timer);
  }, [loadRun, refreshRuns, selected]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const created = await getApi<{ id: string }>("/api/research", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question, request_key: crypto.randomUUID() }),
      });
      await refreshRuns();
      await loadRun(created.id);
      setMessage("研究已開始，進度會自動更新。");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "無法啟動研究。");
    } finally {
      setBusy(false);
    }
  }

  async function saveTopics() {
    if (!selected?.note) return;
    const topicIds = draftNoteId === selected.note.id ? draftTopicIds : selected.note.topics.map((topic) => topic.id);
    setSavingTopics(true);
    setMessage(null);
    try {
      await getApi(`/api/notes/${selected.note.id}/topics`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topicIds }),
      });
      await loadRun(selected.id);
      setTopicEditorOpen(false);
      setMessage("研究主題已更新。");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "研究主題無法儲存。");
    } finally {
      setSavingTopics(false);
    }
  }

  const progress = selected ? researchWorkflowProgress(selected) : null;
  const selectedTopicIds = selected?.note && draftNoteId === selected.note.id ? draftTopicIds : selected?.note?.topics.map((topic) => topic.id) ?? [];

  return (
    <div className="animate-page">
      <PageTitle title="研究工作區" />
      <form onSubmit={submit} className="mb-6 flex flex-col gap-3 rounded-[10px] border border-line bg-surface p-4 sm:flex-row sm:items-end">
        <label className="min-w-0 flex-1 text-xs font-medium text-muted">研究問題
          <textarea value={question} onChange={(event) => setQuestion(event.target.value)} minLength={3} maxLength={500} required rows={2} className="mt-2 w-full resize-none rounded-md border border-line bg-subtle px-3 py-2.5 text-sm text-ink outline-none focus:border-indigo-200" placeholder="輸入你要研究的問題…" />
        </label>
        <PrimaryButton type="submit" disabled={busy}>{busy ? "建立中…" : <><Play className="h-4 w-4" />開始研究</>}</PrimaryButton>
      </form>
      {message ? <p role="status" aria-live="polite" className="mb-5 rounded-md bg-subtle px-4 py-3 text-xs leading-5 text-muted">{message}</p> : null}

      {!selected || !progress ? <PendingState title="尚無研究專案" description="輸入問題並按下「開始研究」後，研究專案會顯示在這裡。" /> : (
        <div className="overflow-hidden rounded-[10px] border border-line bg-surface">
          <header className="border-b border-line px-5 py-6 sm:px-8">
            <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <span className={`rounded px-2 py-1 text-[10px] font-medium ${progress.state === "completed" ? "bg-emerald-50 text-emerald-700" : progress.state === "failed" ? "bg-rose-50 text-rose-700" : "bg-indigo-50 text-indigo-700"}`}>{progress.label}</span>
                <h2 className="mt-4 max-w-3xl text-[24px] font-semibold leading-9 tracking-[-0.025em]">{selected.question}</h2>
                <p className="mt-2 text-sm leading-6 text-muted">建立日期：{projectDate(selected.createdAt)}</p>
              </div>
              <button type="button" onClick={() => void Promise.all([refreshRuns(), loadRun(selected.id)])} className="shrink-0 rounded-md border border-line px-3 py-2 text-xs font-medium text-muted hover:bg-subtle">重新整理</button>
            </div>
            <div className="mt-6" aria-label="研究進度">
              <div className="mb-2 flex items-center justify-between text-xs"><span className="font-medium text-ink">{progress.label}</span><span className="tabular-nums text-muted">{progress.percent}%</span></div>
              <div className="h-2 overflow-hidden rounded-full bg-elevated" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.percent} aria-valuetext={`${progress.label} ${progress.percent}%`}>
                <div className={`h-full rounded-full transition-all duration-700 ${progress.state === "failed" ? "bg-rose-500" : progress.state === "completed" ? "bg-emerald-500" : "bg-indigo-500"}`} style={{ width: `${progress.percent}%` }} />
              </div>
            </div>
          </header>

          <div className="grid lg:grid-cols-[220px_minmax(0,1fr)_300px]">
            <aside className="border-b border-line bg-subtle p-4 lg:border-b-0 lg:border-r">
              <p className="px-2 text-xs font-medium text-muted">研究專案</p>
              <nav className="mt-2 space-y-1">{runs.map((run) => <button type="button" key={run.id} onClick={() => { setTopicEditorOpen(false); void loadRun(run.id); }} className={`w-full rounded-md px-2.5 py-2.5 text-left ${selected.id === run.id ? "bg-indigo-50 text-indigo-700" : "text-muted hover:bg-elevated"}`}><time dateTime={run.created_at} className="block text-[10px] font-medium text-faint">{projectDate(run.created_at)}</time><span className="mt-1 line-clamp-2 block text-xs font-medium leading-5">{run.question}</span></button>)}</nav>
            </aside>

            <article className="min-w-0 border-b border-line px-5 py-6 sm:px-7 lg:border-b-0 lg:border-r">
              {selected.note ? <>
                <div className="flex items-center gap-2 text-emerald-700"><CheckCircle2 className="h-4 w-4" /><h3 className="text-lg font-semibold text-ink">研究成果</h3></div>
                <div className="reading-copy mt-5 whitespace-pre-wrap">{selected.note.body}</div>
              </> : progress.state === "failed" ? <PendingState title="研究未完成" description="請查看錯誤說明；既有知識與其他研究專案不受影響。" /> : <section aria-label="目前研究階段" className="rounded-lg border border-indigo-100 bg-indigo-50/50 p-5"><p className="text-sm font-semibold text-ink">{progress.label}</p><p className="mt-2 text-xs leading-5 text-muted">系統正在背景處理，完成後研究成果會直接顯示在這個專案中。</p></section>}
              {selected.retrievalStatus === "degraded_embedding_unavailable" ? <p role="status" className="mt-6 rounded-md bg-amber-50 px-4 py-3 text-xs text-amber-800">語意檢索暫時不可用；本次結果已明確降級為索引式詞彙檢索。</p> : null}
              {selected.retrievalStatus === "insufficient" ? <p role="status" className="mt-6 rounded-md bg-amber-50 px-4 py-3 text-xs text-amber-800">目前沒有足夠的相關 Evidence，系統不會以低相關內容填滿結果。</p> : null}
              {selected.errorCode ? <p role="alert" className="mt-6 rounded-md bg-rose-50 px-4 py-3 text-xs text-rose-700">{researchError(selected.errorCode)}</p> : null}
            </article>

            <aside className="p-5">
              <section>
                <div className="flex items-center justify-between gap-3"><h3 className="text-sm font-semibold">主題分類</h3>{selected.note ? <button type="button" onClick={() => { setDraftNoteId(selected.note?.id ?? null); setDraftTopicIds(selected.note?.topics.map((topic) => topic.id) ?? []); setTopicEditorOpen((value) => !value); }} aria-expanded={topicEditorOpen} className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-[11px] font-semibold hover:bg-subtle"><Tags className="h-3.5 w-3.5" />編輯</button> : null}</div>
                <div className="mt-3 flex flex-wrap gap-1.5">{selected.note?.topics.length ? selected.note.topics.map((topic) => <TopicPill key={topic.id} name={topic.name} />) : <span className="text-[11px] text-amber-700">{selected.note ? "未分類" : "研究完成後可分類"}</span>}</div>
                {topicEditorOpen && selected.note ? <fieldset className="mt-4 rounded-lg border border-line bg-subtle p-3"><legend className="px-1 text-xs font-semibold">選擇主題</legend><div className="mt-2 max-h-56 space-y-2 overflow-y-auto">{topics.map((topic) => <label key={topic.id} className="flex items-start gap-2 text-xs text-muted"><input type="checkbox" checked={selectedTopicIds.includes(topic.id)} onChange={(event) => setDraftTopicIds(event.target.checked ? [...selectedTopicIds, topic.id] : selectedTopicIds.filter((id) => id !== topic.id))} className="mt-0.5" /><span><span className="font-medium text-ink">{topic.name}</span>{topic.description ? <span className="block text-[10px] leading-4 text-faint">{topic.description}</span> : null}</span></label>)}</div><div className="mt-4 flex gap-2"><button type="button" onClick={saveTopics} disabled={savingTopics} className="inline-flex items-center gap-1.5 rounded-md bg-indigo-500 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"><Save className="h-3.5 w-3.5" />{savingTopics ? "儲存中…" : "儲存"}</button><button type="button" onClick={() => { setDraftTopicIds(selected.note?.topics.map((topic) => topic.id) ?? []); setTopicEditorOpen(false); }} className="rounded-md border border-line px-3 py-2 text-xs">取消</button></div></fieldset> : null}
              </section>

              <section className="mt-6 border-t border-line pt-5">
                <div className="flex items-center justify-between"><h3 className="text-sm font-semibold">重點來源</h3><span className="text-[10px] text-muted">{selected.citations.length} 筆</span></div>
                {selected.citations.length ? <div className="mt-2 divide-y divide-line">{selected.citations.slice(0, 5).map((citation) => { const instagram = instagramCitation(citation.locator); const reel = instagramReelCitation(citation.locator, citation.sourceUrl); const sourceUrl = threadsCitationUrl(citation.locator) ?? instagram?.sourceUrl ?? reel?.sourceUrl ?? citation.sourceUrl; return <div key={citation.id} className="flex items-center gap-3 py-3"><SourceIcon kind={instagram || reel ? "IG" : sourceUrl?.includes("threads.net") ? "TH" : "PDF"} size="sm" /><div className="min-w-0"><p className="truncate text-xs font-semibold">{sourceUrl ? <a href={sourceUrl} target="_blank" rel="noreferrer" className="text-indigo-700 hover:underline">Evidence {citation.evidenceId.slice(0, 8)}</a> : <>Evidence {citation.evidenceId.slice(0, 8)}</>}</p>{instagram?.mediaId || reel?.mediaId ? <p className="mt-1 text-[10px]"><a href={`/api/assets/${citation.assetId}/evidence/${citation.evidenceId}/media`} target="_blank" rel="noreferrer" className="text-indigo-700 hover:underline">{instagram ? `第 ${instagram.imageIndex} 張私人圖片` : "Reel 私人 Keyframe"}</a></p> : null}{reel?.startSeconds !== null && reel?.startSeconds !== undefined ? <p className="mt-1 text-[10px] text-muted">{reel.startSeconds.toFixed(1)}–{reel.endSeconds?.toFixed(1)} 秒</p> : null}</div></div>; })}</div> : <p className="mt-3 text-xs leading-5 text-muted">尚未選出真實 Evidence。</p>}
              </section>
              <section className="mt-6 border-t border-line pt-5"><p className="flex items-center gap-2 text-xs font-medium text-amber-700"><AlertCircle className="h-4 w-4" />研究不確定性</p><p className="mt-2 text-xs leading-5 text-muted">請以研究內容與引用來源為準。</p></section>
              <section className="mt-6 border-t border-line pt-5"><div className="flex items-center gap-2"><FileText className="h-4 w-4 text-indigo-600" /><h3 className="text-sm font-semibold">研究筆記</h3></div><p className="mt-3 text-xs leading-5 text-muted">{selected.note ? "研究筆記已保存在這個研究專案中。" : "研究完成後會保存在這個專案中。"}</p></section>
            </aside>
          </div>
        </div>
      )}

      <div className="mt-5 flex items-start gap-3 border-y border-line py-5"><Flag className="mt-0.5 h-4 w-4 shrink-0 text-indigo-600" /><div><h2 className="text-sm font-semibold">研究成果集中保存在研究工作區</h2><p className="mt-1 text-xs leading-5 text-muted">研究筆記不會再混入「全部知識」清單；原始來源筆記仍留在知識庫供研究與問答使用。</p></div></div>
    </div>
  );
}
