"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

type EditableNote = { id: string; title: string; body: string; bodyRevision: number };

export function SourceNoteControls({
  assetId,
  assetReady,
  jobStatus,
  note,
}: {
  assetId: string;
  assetReady: boolean;
  jobStatus: string | null;
  note: EditableNote | null;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [revision, setRevision] = useState(note?.bodyRevision ?? 1);

  async function generate() {
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch(`/api/assets/${assetId}/source-note`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) {
        setMessage(payload?.error?.message ?? "無法建立 Source Note 工作。");
        return;
      }
      setMessage("Source Note 已排入背景處理，可稍後重新整理查看。");
      router.refresh();
    } catch {
      setMessage("無法連線，請稍後再試。");
    } finally {
      setBusy(false);
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!note) return;
    setBusy(true);
    setMessage(null);
    const form = new FormData(event.currentTarget);
    try {
      const response = await fetch(`/api/notes/${note.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: form.get("title"),
          body: form.get("body"),
          body_revision: revision,
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        setMessage(payload?.error?.message ?? "筆記儲存失敗。");
        return;
      }
      setRevision(payload.data.body_revision);
      setMessage("筆記已儲存；來源 Evidence 未被修改。");
      router.refresh();
    } catch {
      setMessage("無法連線，請稍後再試。");
    } finally {
      setBusy(false);
    }
  }

  if (!note) {
    const processing = ["queued", "running", "retry_wait"].includes(jobStatus ?? "");
    return (
      <section className="rounded-xl border border-line bg-surface p-5">
        <h2 className="text-sm font-semibold text-ink">建立來源筆記</h2>
        <p className="mt-1 text-xs leading-5 text-muted">來源解析完成後，系統會根據 Evidence 建立可引用的筆記。</p>
        <button type="button" onClick={generate} disabled={busy || !assetReady || processing} className="mt-4 rounded-md bg-indigo-500 px-4 py-2.5 text-xs font-semibold text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40">
          {processing ? "產生中…" : "產生 Source Note"}
        </button>
        {!assetReady ? <p className="mt-3 text-xs text-muted">來源解析完成後才能產生 Source Note。</p> : null}
        {message ? <p role="status" aria-live="polite" className="mt-3 text-xs text-muted">{message}</p> : null}
      </section>
    );
  }

  return (
    <form onSubmit={save} className="rounded-xl border border-line bg-surface p-4 sm:p-6">
      <div className="flex flex-col gap-1.5">
        <label htmlFor="source-note-title" className="text-xs font-semibold text-ink">標題</label>
        <input id="source-note-title" name="title" defaultValue={note.title} maxLength={300} required className="w-full rounded-md border border-line bg-canvas px-3 py-2.5 text-sm text-ink outline-none transition placeholder:text-faint focus:border-indigo-300 focus:ring-2 focus:ring-indigo-100" />
      </div>
      <div className="mt-5 flex flex-col gap-1.5">
        <label htmlFor="source-note-body" className="text-xs font-semibold text-ink">內容</label>
        <textarea id="source-note-body" name="body" defaultValue={note.body} rows={24} maxLength={100000} className="reading-copy min-h-[32rem] w-full resize-y rounded-md border border-line bg-canvas px-4 py-3 text-ink outline-none transition placeholder:text-faint focus:border-indigo-300 focus:ring-2 focus:ring-indigo-100" />
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        {message ? <p role="status" aria-live="polite" className="text-xs text-muted">{message}</p> : <span />}
        <button type="submit" disabled={busy} className="rounded-md bg-indigo-500 px-4 py-2.5 text-xs font-semibold text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40">{busy ? "儲存中…" : "儲存修改"}</button>
      </div>
    </form>
  );
}
