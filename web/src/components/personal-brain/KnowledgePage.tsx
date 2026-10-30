"use client";

import { ArrowLeft, BookOpen, ChevronLeft, ChevronRight, Filter, Plus, Save, Tags } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { getApi, getApiEnvelope } from "./api";
import { knowledgeNoteLabel } from "@/lib/knowledge-labels";
import type { KnowledgeNote, Note, Topic } from "./types";
import { PageTitle, PendingState } from "./ui";

const PAGE_SIZE = 12;

function TopicPill({ name }: { name: string }) {
  return <span className="rounded-full bg-indigo-50 px-2.5 py-1 text-[11px] font-medium text-indigo-700">{name}</span>;
}

type NoteSection = { title: string; content: string };

function noteSections(body: string): NoteSection[] {
  const normalized = body.replace(/\r\n/g, "\n").trim();
  if (!normalized) return [{ title: "筆記內容", content: "這則筆記目前沒有內容。" }];
  const matches = [...normalized.matchAll(/^##\s+(.+)$/gm)];
  if (!matches.length) return [{ title: "筆記內容", content: normalized }];
  return matches.map((match, index) => ({
    title: match[1].trim(),
    content: normalized.slice((match.index ?? 0) + match[0].length, matches[index + 1]?.index ?? normalized.length).trim(),
  }));
}

function NoteSectionContent({ content }: { content: string }) {
  const lines = content.split("\n").map((line) => line.trim()).filter(Boolean);
  const bullets = lines.filter((line) => /^[-*]\s+/.test(line));
  if (bullets.length === lines.length && bullets.length > 0) {
    return <ul className="space-y-3">{bullets.map((line, index) => <li key={`${index}-${line}`} className="flex gap-3"><span aria-hidden="true" className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-400" /><span>{line.replace(/^[-*]\s+/, "")}</span></li>)}</ul>;
  }
  return <div className="space-y-4">{lines.map((line, index) => <p key={`${index}-${line}`}>{line.replace(/^[-*]\s+/, "")}</p>)}</div>;
}

export function KnowledgePage() {
  const [topics, setTopics] = useState<Topic[]>([]);
  const [notes, setNotes] = useState<KnowledgeNote[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [topicId, setTopicId] = useState<string | null>(null);
  const [unclassified, setUnclassified] = useState(false);
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailBody, setDetailBody] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [topicEditorOpen, setTopicEditorOpen] = useState(false);
  const [draftTopicIds, setDraftTopicIds] = useState<string[]>([]);
  const [draftNoteId, setDraftNoteId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newTopic, setNewTopic] = useState("");
  const [managerOpen, setManagerOpen] = useState(false);
  const [mergeSource, setMergeSource] = useState("");
  const [mergeTarget, setMergeTarget] = useState("");

  const loadTopics = useCallback(async () => {
    const rows = await getApi<Topic[]>("/api/topics");
    setTopics(rows);
    return rows;
  }, []);

  const loadNotes = useCallback(async () => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (query.trim()) params.set("q", query.trim());
    if (topicId) params.set("topicId", topicId);
    if (unclassified) params.set("unclassified", "true");
    const payload = await getApiEnvelope<KnowledgeNote[]>(`/api/knowledge?${params}`);
    setNotes(payload.data ?? []);
    setTotal(payload.meta?.total ?? 0);
    setError(null);
  }, [offset, query, topicId, unclassified]);

  const knowledgeUrl = useMemo(() => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (query.trim()) params.set("q", query.trim());
    if (topicId) params.set("topicId", topicId);
    if (unclassified) params.set("unclassified", "true");
    return `/api/knowledge?${params}`;
  }, [offset, query, topicId, unclassified]);

  useEffect(() => {
    let active = true;
    Promise.all([getApi<Topic[]>("/api/topics"), getApiEnvelope<KnowledgeNote[]>(knowledgeUrl)])
      .then(([topicRows, payload]) => {
        if (!active) return;
        setTopics(topicRows); setNotes(payload.data ?? []); setTotal(payload.meta?.total ?? 0); setError(null);
      })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "全部知識讀取失敗。"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [knowledgeUrl]);

  const selected = notes.find((note) => note.id === selectedId) ?? null;
  const effectiveDraftTopicIds = draftNoteId === selected?.id
    ? draftTopicIds
    : selected?.topics.map((topic) => topic.id) ?? [];

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const filterDescription = useMemo(() => {
    if (unclassified) return "未分類";
    return topics.find((topic) => topic.id === topicId)?.name ?? "全部主題";
  }, [topicId, topics, unclassified]);

  async function saveTopics() {
    if (!selected) return;
    setSaving(true);
    try {
      await getApi(`/api/notes/${selected.id}/topics`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topicIds: effectiveDraftTopicIds }),
      });
      await loadNotes();
      setTopicEditorOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "筆記主題無法儲存。");
    } finally { setSaving(false); }
  }

  async function createTopic(event: React.FormEvent) {
    event.preventDefault();
    if (!newTopic.trim()) return;
    try {
      await getApi("/api/topics", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: newTopic.trim(), description: "使用者自訂主題" }) });
      setNewTopic("");
      await loadTopics();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "主題無法新增。"); }
  }

  async function renameTopic(topic: Topic) {
    const name = window.prompt("主題新名稱", topic.name)?.trim();
    if (!name || name === topic.name) return;
    try {
      await getApi(`/api/topics/${topic.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
      await Promise.all([loadTopics(), loadNotes()]);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "主題無法改名。"); }
  }

  async function mergeTopics() {
    if (!mergeSource || !mergeTarget || mergeSource === mergeTarget) return;
    try {
      await getApi("/api/topics/merge", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sourceTopicId: mergeSource, targetTopicId: mergeTarget }) });
      setMergeSource(""); setMergeTarget("");
      if (topicId === mergeSource) setTopicId(mergeTarget);
      await Promise.all([loadTopics(), loadNotes()]);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "主題無法合併。"); }
  }

  async function openNote(note: KnowledgeNote) {
    setSelectedId(note.id);
    setDraftNoteId(note.id);
    setDraftTopicIds(note.topics.map((topic) => topic.id));
    setTopicEditorOpen(false);
    setDetailBody(null);
    setDetailError(null);
    setDetailLoading(true);
    setDetailOpen(true);
    try {
      const detail = await getApi<Note>(`/api/notes/${note.id}`);
      setDetailBody(detail.body);
    } catch (reason) {
      setDetailError(reason instanceof Error ? reason.message : "完整筆記內容無法讀取。");
    } finally {
      setDetailLoading(false);
    }
  }

  function cancelTopicEdit() {
    if (selected) {
      setDraftNoteId(selected.id);
      setDraftTopicIds(selected.topics.map((topic) => topic.id));
    }
    setTopicEditorOpen(false);
  }

  function closeNote() {
    cancelTopicEdit();
    setDetailOpen(false);
  }

  if (detailOpen && selected) {
    const sections = noteSections(detailBody ?? selected.summary);
    const selectedLabel = knowledgeNoteLabel(selected.note_type, selected.source_type);
    return <article aria-labelledby="knowledge-note-title" className="animate-page pb-10">
      <div className="flex items-center justify-between gap-4 border-b border-line pb-4">
        <button type="button" onClick={closeNote} className="inline-flex items-center gap-2 rounded-md px-2 py-2 text-xs font-semibold text-muted transition hover:bg-subtle hover:text-ink"><ArrowLeft className="h-4 w-4" />返回全部知識</button>
        <p className="text-[11px] text-faint">{selectedLabel}{selected.note_type === "research_note" && selected.publication_state === "draft" ? "／待檢視" : ""} · {new Date(selected.updated_at).toLocaleDateString("zh-TW")}</p>
      </div>

      <header className="border-b border-line py-8 sm:py-10">
        <div className="flex items-center gap-2 text-indigo-600"><BookOpen className="h-5 w-5" /><span className="text-xs font-semibold">{selectedLabel}</span></div>
        <h1 id="knowledge-note-title" className="mt-4 max-w-4xl text-3xl font-semibold leading-tight tracking-[-0.025em] sm:text-4xl">{selected.title}</h1>
      </header>

      <div className="grid gap-10 py-8 xl:grid-cols-[minmax(0,1fr)_280px]">
        <section aria-label="筆記內容" className="min-w-0">
          {detailLoading ? <p className="rounded-lg bg-subtle px-4 py-3 text-xs text-muted">正在讀取完整筆記…</p> : null}
          {detailError ? <p role="alert" className="mb-5 rounded-lg border border-amber-200 bg-amber-50 p-4 text-xs leading-5 text-amber-800">{detailError} 目前顯示列表摘要。</p> : null}
          <div className="space-y-10">{sections.map((section, index) => <section key={`${index}-${section.title}`} aria-labelledby={`note-section-${index}`}><h2 id={`note-section-${index}`} className="text-xs font-semibold uppercase tracking-[0.12em] text-faint">{section.title}</h2><div className="mt-4 text-sm leading-8 text-muted sm:text-[15px]"><NoteSectionContent content={section.content} /></div></section>)}</div>
          {selected.source_status === "source_removed" ? <p className="mt-7 rounded-lg border border-amber-200 bg-amber-50 p-4 text-xs leading-5 text-amber-800">原始來源已移除；筆記與既有引用文字仍保留，但不提供失效連結。</p> : null}
        </section>

        <aside className="self-start border-t border-line pt-6 xl:sticky xl:top-20 xl:border-l xl:border-t-0 xl:pl-7 xl:pt-0">
          <div className="flex items-center justify-between gap-3"><p className="text-xs font-semibold">主題分類</p><button type="button" onClick={() => setTopicEditorOpen((value) => !value)} aria-expanded={topicEditorOpen} className="inline-flex items-center gap-1.5 rounded-md border border-line px-3 py-2 text-xs font-semibold hover:bg-subtle"><Tags className="h-3.5 w-3.5" />管理主題</button></div>
          <div className="mt-3 flex flex-wrap gap-1.5">{selected.topics.length ? selected.topics.map((topic) => <TopicPill key={topic.id} name={topic.name} />) : <span className="text-[11px] text-amber-700">未分類</span>}</div>

          {topicEditorOpen ? <fieldset className="mt-5 rounded-lg border border-line bg-surface p-4"><legend className="px-1 text-xs font-semibold">選擇主題</legend><div className="mt-2 space-y-3">{topics.map((topic) => <label key={topic.id} className="flex items-start gap-2 text-xs text-muted"><input type="checkbox" checked={effectiveDraftTopicIds.includes(topic.id)} onChange={(event) => { const current = effectiveDraftTopicIds; setDraftNoteId(selected.id); setDraftTopicIds(event.target.checked ? [...current, topic.id] : current.filter((id) => id !== topic.id)); }} className="mt-0.5" /><span><span className="font-medium text-ink">{topic.name}</span><span className="block text-[10px] leading-4 text-faint">{topic.description}</span></span></label>)}</div><div className="mt-5 flex flex-wrap gap-2"><button type="button" onClick={saveTopics} disabled={saving} className="inline-flex items-center gap-1.5 rounded-md bg-indigo-500 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"><Save className="h-3.5 w-3.5" />{saving ? "儲存中…" : "儲存分類"}</button><button type="button" onClick={() => { setDraftNoteId(selected.id); setDraftTopicIds([]); }} className="rounded-md border border-line px-3 py-2 text-xs">清空</button><button type="button" onClick={cancelTopicEdit} className="rounded-md border border-line px-3 py-2 text-xs">取消</button></div><p className="mt-2 text-[10px] leading-4 text-faint">只有按下儲存才會更新；人工設定（包含清空）會持續保留，AI 重試不會覆蓋。</p></fieldset> : null}

          {selected.source_asset_id ? <a href={`/assets/${selected.source_asset_id}/source-note`} className="mt-6 inline-flex text-xs font-semibold text-indigo-700 hover:underline">查看完整筆記與引用</a> : null}
        </aside>
      </div>
    </article>;
  }

  return <div className="animate-page">
    <PageTitle title="全部知識" />
    <div className="mt-3 flex justify-end">
      <button type="button" onClick={() => setManagerOpen((value) => !value)} className="inline-flex items-center gap-1.5 rounded-md border border-line px-3 py-2 text-xs font-semibold hover:bg-subtle"><Tags className="h-3.5 w-3.5" />管理主題</button>
    </div>

    {managerOpen ? <section aria-label="管理主題" className="mt-5 rounded-lg border border-line bg-surface p-4">
      <form onSubmit={createTopic} className="flex gap-2"><input value={newTopic} onChange={(event) => setNewTopic(event.target.value)} aria-label="新主題名稱" placeholder="新增主題" className="min-w-0 flex-1 rounded-md border border-line bg-canvas px-3 py-2 text-xs outline-none focus:border-indigo-300" /><button className="inline-flex items-center gap-1 rounded-md bg-indigo-500 px-3 py-2 text-xs font-semibold text-white"><Plus className="h-3.5 w-3.5" />新增</button></form>
      <div className="mt-3 flex flex-wrap gap-2">{topics.map((topic) => <button type="button" key={topic.id} onClick={() => renameTopic(topic)} title={topic.description || "點擊改名"} className="rounded-full border border-line px-2.5 py-1 text-[11px] hover:border-indigo-200 hover:bg-indigo-50">{topic.name}</button>)}</div>
      <div className="mt-4 grid gap-2 sm:grid-cols-[1fr_auto_1fr_auto]"><select aria-label="被合併主題" value={mergeSource} onChange={(event) => setMergeSource(event.target.value)} className="rounded-md border border-line bg-canvas px-2 py-2 text-xs"><option value="">選擇被合併主題</option>{topics.map((topic) => <option key={topic.id} value={topic.id}>{topic.name}</option>)}</select><span className="self-center text-center text-xs text-muted">合併至</span><select aria-label="目標主題" value={mergeTarget} onChange={(event) => setMergeTarget(event.target.value)} className="rounded-md border border-line bg-canvas px-2 py-2 text-xs"><option value="">選擇保留主題</option>{topics.map((topic) => <option key={topic.id} value={topic.id}>{topic.name}</option>)}</select><button type="button" onClick={mergeTopics} disabled={!mergeSource || !mergeTarget || mergeSource === mergeTarget} className="rounded-md border border-line px-3 py-2 text-xs font-semibold disabled:opacity-40">合併</button></div>
    </section> : null}

    <section className="mt-6">
      <div className="flex flex-col gap-3 border-b border-line pb-4 lg:flex-row lg:items-center">
        <input value={query} onChange={(event) => { setQuery(event.target.value); setOffset(0); }} aria-label="搜尋筆記" placeholder="搜尋筆記標題與內容…" className="min-w-0 flex-1 rounded-md bg-subtle px-3 py-2.5 text-xs outline-none focus:ring-1 focus:ring-ink" />
        <div className="flex items-center gap-2 overflow-x-auto"><Filter className="h-4 w-4 shrink-0 text-muted" /><button type="button" onClick={() => { setTopicId(null); setUnclassified(false); setOffset(0); }} className={`shrink-0 rounded-full px-3 py-1.5 text-xs ${!topicId && !unclassified ? "bg-ink text-canvas" : "bg-subtle text-muted"}`}>全部</button>{topics.map((topic) => <button type="button" key={topic.id} onClick={() => { setTopicId(topic.id); setUnclassified(false); setOffset(0); }} className={`shrink-0 rounded-full px-3 py-1.5 text-xs ${topicId === topic.id ? "bg-indigo-500 text-white" : "bg-subtle text-muted"}`}>{topic.name}</button>)}<button type="button" onClick={() => { setTopicId(null); setUnclassified(true); setOffset(0); }} className={`shrink-0 rounded-full px-3 py-1.5 text-xs ${unclassified ? "bg-amber-500 text-white" : "bg-subtle text-muted"}`}>未分類</button></div>
      </div>
      {error ? <p role="alert" className="mt-4 text-sm text-rose-700">{error}</p> : null}
      <div className="mt-5 flex items-center justify-between"><p className="text-xs text-muted">{filterDescription} · 共 {total} 則筆記</p><p className="text-[11px] text-faint">第 {page} / {pages} 頁</p></div>
      <div className="mt-4">
        {loading ? <p className="py-10 text-sm text-muted">正在讀取筆記…</p> : null}
        {!loading && notes.length ? <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {notes.map((note) => <button type="button" key={note.id} onClick={() => void openNote(note)} className="rounded-lg border border-line bg-surface p-4 text-left transition hover:border-indigo-200 hover:shadow-sm">
            <div className="flex items-center justify-between gap-3"><span className="text-[11px] font-medium text-muted">{knowledgeNoteLabel(note.note_type, note.source_type)}{note.note_type === "research_note" && note.publication_state === "draft" ? "／待檢視" : ""}</span><span className="text-[10px] text-faint">{new Date(note.updated_at).toLocaleDateString("zh-TW")}</span></div>
            <h2 className="mt-3 line-clamp-2 text-sm font-semibold leading-6">{note.title}</h2>
            <p className="mt-2 line-clamp-3 text-xs leading-5 text-muted">{note.summary || "這則筆記目前沒有摘要。"}</p>
            <div className="mt-4 flex flex-wrap gap-1.5">{note.topics.length ? note.topics.map((topic) => <TopicPill key={topic.id} name={topic.name} />) : <span className="text-[11px] text-amber-700">未分類</span>}</div>
          </button>)}
        </div> : null}
        {!loading && !notes.length ? <PendingState title="沒有符合條件的筆記" description="全部知識不會顯示原始檔、刪除紀錄或背景工作。" /> : null}
        <div className="mt-5 flex justify-end gap-2"><button type="button" aria-label="上一頁" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} className="rounded-md border border-line p-2 disabled:opacity-35"><ChevronLeft className="h-4 w-4" /></button><button type="button" aria-label="下一頁" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)} className="rounded-md border border-line p-2 disabled:opacity-35"><ChevronRight className="h-4 w-4" /></button></div>
      </div>
    </section>

  </div>;
}
