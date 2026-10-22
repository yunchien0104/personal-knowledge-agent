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
      <section>
        <button type="button" onClick={generate} disabled={busy || !assetReady || processing}>
          {processing ? "產生中…" : "產生 Source Note"}
        </button>
        {!assetReady ? <p>來源解析完成後才能產生 Source Note。</p> : null}
        <p role="status" aria-live="polite">{message}</p>
      </section>
    );
  }

  return (
    <form onSubmit={save} style={{ display: "grid", gap: "1rem" }}>
      <label>標題<input name="title" defaultValue={note.title} maxLength={300} required /></label>
      <label>內容<textarea name="body" defaultValue={note.body} rows={24} maxLength={100000} /></label>
      <button type="submit" disabled={busy}>{busy ? "儲存中…" : "儲存修改"}</button>
      <p role="status" aria-live="polite">{message}</p>
    </form>
  );
}
