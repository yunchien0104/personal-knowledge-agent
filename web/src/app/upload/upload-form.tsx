"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { uploadPdf } from "@/lib/pdf-upload-client";

export function UploadForm() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const input = event.currentTarget.elements.namedItem("pdf");
    if (!(input instanceof HTMLInputElement) || !input.files?.[0]) {
      setMessage("請先選擇 PDF 檔案。");
      return;
    }

    setBusy(true);
    setMessage(null);
    try {
      await uploadPdf(input.files[0]);
      router.push("/inbox");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "PDF 上傳失敗。");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: "grid", gap: "1rem", maxWidth: 520 }}>
      <label htmlFor="pdf">PDF 檔案（最多 10 MB）</label>
      <input id="pdf" name="pdf" type="file" accept="application/pdf,.pdf" disabled={busy} />
      <button type="submit" disabled={busy}>{busy ? "上傳中…" : "上傳 PDF"}</button>
      <p aria-live="polite" role="status">{message}</p>
    </form>
  );
}
