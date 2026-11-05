"use client";

import { ArrowDown, ArrowUp, Check, Copy, FolderCog, Pencil, Plus, Save, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";

import {
  cloneResearchTemplate,
  defaultResearchTemplates,
  researchTemplateSchema,
  type ResearchTemplate,
} from "@/lib/research-template-ui";

const STORAGE_KEY = "personal-brain-research-templates-v1";

type Props = {
  selected: ResearchTemplate;
  onSelect: (template: ResearchTemplate) => void;
};

function readSavedTemplates() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) as unknown : [];
    const result = researchTemplateSchema.array().safeParse(parsed);
    return result.success ? result.data : [];
  } catch {
    return [];
  }
}

function writeSavedTemplates(templates: ResearchTemplate[]) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(templates));
}

function ordered(template: ResearchTemplate, sections: ResearchTemplate["sections"]): ResearchTemplate {
  return { ...template, sections };
}

export function ResearchTemplateStudio({ selected, onSelect }: Props) {
  const [editorOpen, setEditorOpen] = useState(false);
  const [managerOpen, setManagerOpen] = useState(false);
  const [saved, setSaved] = useState<ResearchTemplate[]>([]);
  const [draft, setDraft] = useState(() => cloneResearchTemplate(selected));
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setSaved(readSavedTemplates()));
    return () => window.cancelAnimationFrame(frame);
  }, []);

  function choose(template: ResearchTemplate) {
    const next = cloneResearchTemplate(template);
    onSelect(next);
    setDraft(next);
    setEditorOpen(false);
    setMessage(`已選擇「${next.name}」。`);
  }

  function applyDraft() {
    const parsed = researchTemplateSchema.safeParse(draft);
    if (!parsed.success) {
      setMessage("模板至少需要兩個有效章節，且名稱與章節內容不可留白。");
      return;
    }
    onSelect(parsed.data);
    setMessage("本次研究的章節設定已更新。");
  }

  function saveAsMine(template: ResearchTemplate) {
    const copy = { ...cloneResearchTemplate(template), id: crypto.randomUUID() };
    const next = [copy, ...saved].slice(0, 20);
    setSaved(next);
    writeSavedTemplates(next);
    setMessage("已存到這個瀏覽器的「我的模板」。");
  }

  function removeSaved(id: string) {
    const next = saved.filter((template) => template.id !== id);
    setSaved(next);
    writeSavedTemplates(next);
    setMessage("自「我的模板」移除；不影響任何既有研究。");
  }

  return <section className="mb-6 space-y-3" aria-labelledby="research-template-title">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-indigo-600">Research Template</p>
        <h2 id="research-template-title" className="mt-1 text-lg font-semibold">選擇研究架構</h2>
        <p className="mt-1 text-xs leading-5 text-muted">模板控制本次畫面中的分析架構；研究送出與驗證仍沿用既有流程。</p>
      </div>
      <button type="button" onClick={() => setManagerOpen((value) => !value)} aria-expanded={managerOpen} className="inline-flex items-center gap-2 rounded-md border border-line bg-surface px-3 py-2 text-xs font-semibold hover:bg-subtle">
        <FolderCog className="h-4 w-4 text-indigo-600" />我的模板<span className="rounded-full bg-elevated px-1.5 py-0.5 text-[10px] text-muted">{saved.length}</span>
      </button>
    </div>

    {message ? <p role="status" aria-live="polite" className="rounded-md bg-subtle px-3 py-2 text-[11px] text-muted">{message}</p> : null}

    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
      {defaultResearchTemplates.map((template) => {
        const active = selected.id === template.id;
        return <button key={template.id} type="button" onClick={() => choose(template)} aria-pressed={active} className={`relative rounded-lg border p-4 text-left transition ${active ? "border-indigo-500 bg-indigo-50 ring-2 ring-indigo-100" : "border-line bg-surface hover:border-indigo-200 hover:bg-subtle"}`}>
          {active ? <span className="absolute right-3 top-3 grid h-5 w-5 place-items-center rounded-full bg-indigo-600 text-white"><Check className="h-3 w-3" /></span> : null}
          <span className="rounded bg-elevated px-2 py-1 text-[10px] font-semibold text-indigo-700">{template.framework}</span>
          <h3 className="mt-3 pr-7 text-sm font-semibold">{template.name}</h3>
          <p className="mt-1.5 text-xs leading-5 text-muted">{template.description}</p>
          <div className="mt-3 flex flex-wrap gap-1">{template.sections.map((section) => <span key={section.id} className="rounded-full bg-white px-2 py-1 text-[10px] text-muted ring-1 ring-line">{section.title}</span>)}</div>
        </button>;
      })}
    </div>

    <div className="rounded-lg border border-line bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div><p className="text-xs font-semibold">目前模板：{selected.name}</p><p className="mt-1 text-[11px] text-muted">{selected.sections.length} 個章節 · {selected.depth} · {selected.tone}</p></div>
        <div className="flex gap-2">
          <button type="button" onClick={() => saveAsMine(selected)} className="inline-flex items-center gap-1 rounded border border-line px-2.5 py-1.5 text-[11px]"><Copy className="h-3 w-3" />存到我的模板</button>
          <button type="button" onClick={() => setEditorOpen((value) => !value)} aria-expanded={editorOpen} className="inline-flex items-center gap-1 rounded border border-line px-2.5 py-1.5 text-[11px] font-semibold"><Pencil className="h-3 w-3" />編輯章節</button>
        </div>
      </div>
      {editorOpen ? <div className="border-t border-line bg-subtle p-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="text-[11px] text-muted">模板名稱<input value={draft.name} maxLength={100} onChange={(event) => setDraft({ ...draft, name: event.target.value })} className="mt-1 w-full rounded border border-line bg-white px-3 py-2 text-xs text-ink" /></label>
          <label className="text-[11px] text-muted">分析深度<select value={draft.depth} onChange={(event) => setDraft({ ...draft, depth: event.target.value as ResearchTemplate["depth"] })} className="mt-1 w-full rounded border border-line bg-white px-3 py-2 text-xs text-ink"><option value="concise">精簡</option><option value="standard">標準</option><option value="deep">深入</option></select></label>
          <label className="text-[11px] text-muted">語氣<select value={draft.tone} onChange={(event) => setDraft({ ...draft, tone: event.target.value as ResearchTemplate["tone"] })} className="mt-1 w-full rounded border border-line bg-white px-3 py-2 text-xs text-ink"><option value="neutral">中性</option><option value="executive">決策導向</option><option value="academic">學術</option></select></label>
        </div>
        <div className="mt-4 flex items-center justify-between"><h3 className="text-xs font-semibold">報告章節</h3><button type="button" disabled={draft.sections.length >= 12} onClick={() => { const suffix = draft.sections.length + 1; setDraft(ordered(draft, [...draft.sections, { id: `custom_section_${suffix}`, title: "新章節", purpose: "說明本章節要回答的問題。", required: false }])); }} className="inline-flex items-center gap-1 rounded border border-line bg-white px-2.5 py-1.5 text-[11px] disabled:opacity-40"><Plus className="h-3 w-3" />新增章節</button></div>
        <div className="mt-2 space-y-2">{draft.sections.map((section, index) => <div key={section.id} className="grid gap-2 rounded-md border border-line bg-white p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)_auto]">
          <input aria-label={`第 ${index + 1} 章標題`} value={section.title} maxLength={120} onChange={(event) => setDraft(ordered(draft, draft.sections.map((item) => item.id === section.id ? { ...item, title: event.target.value } : item)))} className="rounded border border-line px-2.5 py-2 text-xs font-semibold" />
          <textarea aria-label={`第 ${index + 1} 章目的`} value={section.purpose} maxLength={600} rows={2} onChange={(event) => setDraft(ordered(draft, draft.sections.map((item) => item.id === section.id ? { ...item, purpose: event.target.value } : item)))} className="resize-none rounded border border-line px-2.5 py-2 text-xs" />
          <div className="flex items-center justify-end gap-1"><label className="mr-1 flex items-center gap-1 text-[10px] text-muted"><input type="checkbox" checked={section.required} onChange={(event) => setDraft(ordered(draft, draft.sections.map((item) => item.id === section.id ? { ...item, required: event.target.checked } : item)))} />必填</label><button type="button" aria-label="上移章節" disabled={index === 0} onClick={() => { const next = [...draft.sections]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; setDraft(ordered(draft, next)); }} className="rounded border border-line p-1.5 disabled:opacity-30"><ArrowUp className="h-3 w-3" /></button><button type="button" aria-label="下移章節" disabled={index === draft.sections.length - 1} onClick={() => { const next = [...draft.sections]; [next[index], next[index + 1]] = [next[index + 1], next[index]]; setDraft(ordered(draft, next)); }} className="rounded border border-line p-1.5 disabled:opacity-30"><ArrowDown className="h-3 w-3" /></button><button type="button" aria-label="刪除章節" disabled={draft.sections.length <= 2} onClick={() => setDraft(ordered(draft, draft.sections.filter((item) => item.id !== section.id)))} className="rounded border border-line p-1.5 text-rose-600 disabled:opacity-30"><Trash2 className="h-3 w-3" /></button></div>
        </div>)}</div>
        <button type="button" onClick={applyDraft} className="mt-4 inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-2 text-xs font-semibold text-white"><Save className="h-3.5 w-3.5" />套用本次修改</button>
      </div> : null}
    </div>

    {managerOpen ? <div className="rounded-lg border border-line bg-surface p-4">
      <div className="flex items-center justify-between"><div><h3 className="text-sm font-semibold">我的模板</h3><p className="mt-1 text-[11px] text-muted">保存在這個瀏覽器；不會改動帳號資料、Worker 或其他頁面。</p></div><button type="button" onClick={() => setManagerOpen(false)} aria-label="關閉我的模板"><X className="h-4 w-4" /></button></div>
      {saved.length ? <div className="mt-4 grid gap-3 md:grid-cols-2">{saved.map((template) => <article key={template.id} className="rounded-lg border border-line p-4"><h4 className="text-sm font-semibold">{template.name}</h4><p className="mt-1 text-[11px] text-muted">{template.sections.length} 個章節 · {template.description}</p><div className="mt-3 flex gap-2"><button type="button" onClick={() => choose(template)} className="rounded border border-line px-2.5 py-1.5 text-[11px] font-semibold">使用</button><button type="button" onClick={() => removeSaved(template.id)} className="rounded border border-rose-100 px-2.5 py-1.5 text-[11px] text-rose-700">移除</button></div></article>)}</div> : <p className="mt-4 rounded-md border border-dashed border-line p-4 text-xs text-muted">尚未儲存個人模板。</p>}
    </div> : null}
  </section>;
}
