"use client";

import { AlertCircle, CheckCircle2, Circle, Clock3, FileText, Flag, Play, Quote } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { getApi } from "./api";
import type { ResearchRun, ResearchRunSummary } from "./types";
import { PageTitle, PendingState, PrimaryButton, SoftButton, SourceIcon } from "./ui";

function researchError(code: string | null) {
  const messages: Record<string, string> = {
    insufficient_evidence: "內部知識庫找不到足夠的原始 Evidence；Research Draft 未建立。",
    unsupported_research_finding: "研究結果無法由逐字原始引用支持；Research Draft 未建立。",
    invalid_research_plan: "研究計畫無效，未執行研究。",
  };
  return code ? messages[code] ?? "Research Agent 執行失敗，請稍後重試。" : null;
}

export function ResearchPage() {
  const params = useSearchParams();
  const [tab, setTab] = useState("進行中");
  const [question, setQuestion] = useState(params.get("q") ?? "");
  const [runs, setRuns] = useState<ResearchRunSummary[]>([]);
  const [selected, setSelected] = useState<ResearchRun | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refreshRuns() {
    const data = await getApi<ResearchRunSummary[]>("/api/research");
    setRuns(data);
    return data;
  }

  async function loadRun(id: string) {
    const data = await getApi<ResearchRun>(`/api/research?id=${encodeURIComponent(id)}`);
    setSelected(data);
  }

  useEffect(() => {
    let active = true;
    void getApi<ResearchRunSummary[]>("/api/research")
      .then(async (data) => {
        if (!active) return;
        setRuns(data);
        if (data[0]) {
          const run = await getApi<ResearchRun>(`/api/research?id=${encodeURIComponent(data[0].id)}`);
          if (active) setSelected(run);
        }
      })
      .catch((reason: unknown) => { if (active) setMessage(reason instanceof Error ? reason.message : "研究資料讀取失敗。"); });
    return () => { active = false; };
  }, []);

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
      setMessage("研究已排入既有背景處理流程；只會使用已授權的內部知識與既有 Worker。");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "無法啟動研究。");
    } finally {
      setBusy(false);
    }
  }

  const running = selected ? ["queued", "running", "processing", "planned"].includes(selected.status) : false;
  const completed = selected?.status === "completed";
  const planEntries = selected?.plan ? Object.entries(selected.plan) : [];

  return (
    <div className="animate-page">
      <PageTitle title="研究工作區" />
      <form onSubmit={submit} className="mb-6 flex flex-col gap-3 rounded-[10px] border border-line bg-surface p-4 sm:flex-row sm:items-end"><label className="min-w-0 flex-1 text-xs font-medium text-muted">研究問題<textarea value={question} onChange={(event) => setQuestion(event.target.value)} minLength={3} maxLength={500} required rows={2} className="mt-2 w-full resize-none rounded-md border border-line bg-subtle px-3 py-2.5 text-sm text-ink outline-none focus:border-indigo-200" placeholder="輸入你要研究的問題…" /></label><PrimaryButton type="submit" disabled={busy}>{busy ? "建立中…" : <><Play className="h-4 w-4" />開始研究</>}</PrimaryButton></form>
      {message ? <p role="status" aria-live="polite" className="mb-5 rounded-md bg-subtle px-4 py-3 text-xs leading-5 text-muted">{message}</p> : null}

      <div className="mb-5 flex w-fit items-center gap-1 rounded-md bg-elevated p-1">{["進行中", "計畫", "草稿"].map((item) => <button type="button" key={item} onClick={() => setTab(item)} className={`rounded px-3 py-1.5 text-xs font-medium transition ${tab === item ? "bg-surface text-ink shadow-sm" : "text-muted"}`}>{item}</button>)}</div>

      {!selected ? <PendingState title="尚無研究專案" description="輸入問題並明確按下「開始研究」後，才會建立真實 Research Run。" /> : (
        <div className="overflow-hidden rounded-[10px] border border-line bg-surface">
          <header className="border-b border-line px-5 py-6 sm:px-8"><div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between"><div><div className="flex items-center gap-2"><span className={`rounded px-2 py-1 text-[10px] font-medium ${completed ? "bg-emerald-50 text-emerald-700" : running ? "bg-indigo-50 text-indigo-700" : "bg-amber-50 text-amber-700"}`}>{selected.status}</span><span className="text-[11px] text-muted">Research Run · {selected.id.slice(0, 8)}</span></div><h2 className="mt-4 max-w-3xl text-[24px] font-semibold leading-9 tracking-[-0.025em]">{selected.question}</h2><p className="mt-2 max-w-2xl text-sm leading-6 text-muted">建立時間：{new Date(selected.createdAt).toLocaleString("zh-TW")}</p></div><button type="button" onClick={() => void refreshRuns()} className="shrink-0 rounded-md border border-line px-3 py-2 text-xs font-medium text-muted hover:bg-subtle">重新整理清單</button></div><div className="mt-6 flex flex-wrap gap-x-7 gap-y-3 border-t border-line pt-4 text-xs text-muted"><span>狀態 <strong className="ml-1 text-ink">{selected.status}</strong></span><span>引用 <strong className="ml-1 text-ink">{selected.citations.length} 筆</strong></span><span>最近更新 <strong className="ml-1 text-ink">{new Date(selected.updatedAt).toLocaleString("zh-TW")}</strong></span></div><div className="mt-3 h-1 overflow-hidden bg-elevated"><div className="h-full bg-indigo-500 transition-all duration-700" style={{ width: completed ? "100%" : selected.plan ? "45%" : running ? "18%" : "0%" }} /></div></header>

          <div className="grid lg:grid-cols-[200px_minmax(0,1fr)_300px]">
            <aside className="border-b border-line bg-subtle p-4 lg:border-b-0 lg:border-r"><p className="px-2 text-xs font-medium text-muted">研究專案</p><nav className="mt-2 space-y-0.5">{runs.map((run, index) => <button type="button" key={run.id} onClick={() => void loadRun(run.id)} className={`flex w-full items-start gap-2 rounded-md px-2.5 py-2 text-left text-xs font-medium ${selected.id === run.id ? "bg-indigo-50 text-indigo-700" : "text-muted hover:bg-elevated"}`}><span className="w-4 shrink-0 text-[10px] text-faint">{String(index + 1).padStart(2, "0")}</span><span className="line-clamp-2">{run.question}</span></button>)}</nav></aside>
            <article className="min-w-0 border-b border-line px-5 py-6 sm:px-7 lg:border-b-0 lg:border-r">
              <h3 className="text-lg font-semibold">{tab === "草稿" ? "研究草稿" : tab === "計畫" ? "研究計畫" : "研究進度"}</h3>
              {tab === "草稿" ? selected.note ? <div className="reading-copy mt-5 whitespace-pre-wrap">{selected.note.body}</div> : <div className="mt-5"><PendingState title="草稿尚未產生" description="背景 Worker 完成且證據通過檢查後，真實草稿才會顯示。" /></div> : planEntries.length ? <div className="mt-6 space-y-5">{planEntries.map(([key, value], index) => <div key={key} className="relative flex gap-4"><span className="grid h-7 w-7 shrink-0 place-items-center rounded-full border border-indigo-200 bg-indigo-50 text-indigo-700">{completed ? <CheckCircle2 className="h-4 w-4" /> : index === 0 ? <Clock3 className="h-4 w-4" /> : <Circle className="h-3 w-3" />}</span><div className="min-w-0"><p className="text-sm font-semibold">{key}</p><pre className="mt-1 overflow-x-auto whitespace-pre-wrap text-xs leading-5 text-muted">{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre></div></div>)}</div> : <div className="mt-5"><PendingState title="研究計畫尚未產生" description="目前顯示真實執行狀態；Worker 建立計畫後會顯示於此。" /></div>}
              {selected.errorCode ? <p role="alert" className="mt-6 rounded-md bg-rose-50 px-4 py-3 text-xs text-rose-700">{researchError(selected.errorCode)}</p> : null}
            </article>
            <aside className="p-5"><section><div className="flex items-center justify-between"><h3 className="text-sm font-semibold">重點來源</h3><span className="text-[10px] text-muted">{selected.citations.length} 筆</span></div>{selected.citations.length ? <div className="mt-2 divide-y divide-line">{selected.citations.slice(0, 5).map((citation) => <div key={citation.id} className="flex items-center gap-3 py-3"><SourceIcon kind="PDF" size="sm" /><div className="min-w-0"><p className="truncate text-xs font-semibold">Evidence {citation.evidenceId.slice(0, 8)}</p><p className="mt-1 text-[10px] text-muted">{citation.validityStatus} · {citation.relationType}</p></div></div>)}</div> : <p className="mt-3 text-xs leading-5 text-muted">尚未選出真實 Evidence。</p>}</section><section className="mt-6 border-t border-line pt-5"><p className="flex items-center gap-2 text-xs font-medium text-amber-700"><AlertCircle className="h-4 w-4" />研究不確定性</p><p className="mt-2 text-xs leading-5 text-muted">未串接獨立的不確定性欄位；請以草稿內容與 Citation validity 為準。</p></section><section className="mt-6 border-t border-line pt-5"><div className="flex items-center gap-2"><FileText className="h-4 w-4 text-indigo-600" /><h3 className="text-sm font-semibold">草稿摘要</h3></div><p className="mt-3 line-clamp-6 whitespace-pre-wrap text-xs leading-5 text-muted">{selected.note?.body ?? "草稿尚未產生。"}</p><SoftButton type="button" className="mt-4 w-full" disabled={!selected.note}><FileText className="h-4 w-4" />{selected.note ? "草稿已建立" : "等待研究草稿"}</SoftButton></section></aside>
          </div>
        </div>
      )}

      <div className="mt-5 flex flex-col gap-4 border-y border-line py-5 sm:flex-row sm:items-center sm:justify-between"><div className="flex items-start gap-3"><Flag className="mt-0.5 h-4 w-4 shrink-0 text-indigo-600" /><h2 className="text-sm font-semibold">研究完成後，由你決定如何使用草稿</h2></div><button type="button" disabled className="shrink-0 rounded-md border border-line px-4 py-2.5 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-40"><Quote className="mr-2 inline h-3.5 w-3.5" />另存筆記尚未串接</button></div>
    </div>
  );
}
