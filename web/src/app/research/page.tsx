"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import {
  newResearchRequestKey,
  researchPdfPage,
  safeResearchError,
  type ResearchRun,
} from "@/lib/research";


type RunSummary = {
  id: string;
  question: string;
  status: string;
  created_at: string;
};

export default function ResearchPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<ResearchRun | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refreshRuns() {
    const response = await fetch("/api/research", { cache: "no-store" });
    const payload = await response.json();
    if (response.ok) setRuns(payload.data);
  }

  async function loadRun(id: string) {
    const response = await fetch(`/api/research?id=${encodeURIComponent(id)}`, { cache: "no-store" });
    const payload = await response.json();
    if (response.ok) setSelected(payload.data);
    else setMessage(payload?.error?.message ?? "無法讀取 Research Draft。");
  }

  useEffect(() => {
    let active = true;
    void fetch("/api/research", { cache: "no-store" })
      .then((response) => response.json())
      .then((payload) => {
        if (active && Array.isArray(payload?.data)) setRuns(payload.data);
      });
    return () => { active = false; };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    const form = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/research", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: form.get("question"),
          request_key: newResearchRequestKey(),
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        setMessage(payload?.error?.message ?? "無法啟動研究。");
        return;
      }
      setMessage("Research 已排入背景處理；這次只檢索你的內部知識，不會呼叫付費 Web 搜尋。");
      await refreshRuns();
      await loadRun(payload.data.id);
    } catch {
      setMessage("無法連線，請稍後再試。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main style={{ padding: "3rem", maxWidth: 900, margin: "0 auto" }}>
      <nav style={{ display: "flex", gap: "1rem", marginBottom: "2rem" }}>
        <Link href="/inbox">Inbox</Link><Link href="/search">Knowledge Search</Link>
      </nav>
      <h1>Research Agent</h1>
      <p>明確提交問題後，系統才會執行 Plan → Internal Retrieval → Evidence Selection → Synthesis。</p>
      <form onSubmit={submit} style={{ display: "grid", gap: "0.75rem" }}>
        <label htmlFor="research-question">研究問題</label>
        <textarea id="research-question" name="question" minLength={3} maxLength={500} required rows={4} />
        <button type="submit" disabled={busy}>{busy ? "建立中…" : "開始內部研究"}</button>
      </form>
      <p role="status" aria-live="polite">{message}</p>
      <section>
        <h2>Research Runs</h2>
        <button type="button" onClick={() => void refreshRuns()}>重新整理</button>
        <ul>{runs.map((run) => (
          <li key={run.id}>
            <button type="button" onClick={() => void loadRun(run.id)}>
              {run.question} — {run.status}
            </button>
          </li>
        ))}</ul>
      </section>
      {selected ? (
        <article>
          <h2>{selected.note?.title ?? selected.question}</h2>
          <p>狀態：{selected.status}</p>
          {selected.errorCode ? <p role="alert">{safeResearchError(selected.errorCode)}</p> : null}
          {selected.plan ? <details><summary>Research Plan</summary><pre>{JSON.stringify(selected.plan, null, 2)}</pre></details> : null}
          {selected.note ? <pre style={{ whiteSpace: "pre-wrap" }}>{selected.note.body}</pre> : null}
          <h3>可驗證的原始引用</h3>
          <ol>{selected.citations.map((citation) => {
            const page = researchPdfPage(citation.locator);
            return (
              <li key={citation.id}>
                <blockquote>{citation.quote}</blockquote>
                <p>{citation.relationType} · Evidence {citation.evidenceId} · Revision {citation.revisionId}</p>
                {citation.sourceUrl && page && citation.validityStatus === "valid" ? (
                  <a href={`${citation.sourceUrl}#page=${page}`} target="_blank" rel="noreferrer">
                    開啟原始 PDF 第 {page} 頁
                  </a>
                ) : <span>{citation.validityStatus}</span>}
              </li>
            );
          })}</ol>
        </article>
      ) : null}
    </main>
  );
}
