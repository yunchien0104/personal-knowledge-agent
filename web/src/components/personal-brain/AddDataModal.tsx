"use client";

import { FileText, Link2, LoaderCircle, Type, Upload, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { uploadPdf } from "@/lib/pdf-upload-client";
import { PrimaryButton } from "./ui";

type CaptureMode = "url" | "pdf" | "text";

export function AddDataModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const [mode, setMode] = useState<CaptureMode>("url");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  if (!open) return null;

  function close() {
    if (busy) return;
    setMode("url");
    setMessage(null);
    onClose();
  }

  async function submitPdf(event: FormEvent<HTMLFormElement>) {
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
      onClose();
      router.push("/library");
      router.refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "PDF 上傳失敗。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/25 p-0 sm:items-center sm:p-6" onMouseDown={(event) => event.target === event.currentTarget && close()}>
      <section role="dialog" aria-modal="true" aria-label="新增資料" className="max-h-[92vh] w-full overflow-y-auto rounded-t-[14px] border border-line bg-surface shadow-soft sm:max-w-xl sm:rounded-[10px]">
        <header className="flex justify-end border-b border-line px-3 py-3 sm:px-5">
          <button type="button" aria-label="關閉新增資料" onClick={close} className="rounded-md p-2 text-muted hover:bg-subtle"><X className="h-5 w-5" /></button>
        </header>
        <div className="p-5 sm:p-7">
          <div role="tablist" aria-label="新增資料方式" className="grid grid-cols-3 border-b border-line">
            {([[
              "url", "貼網址", Link2,
            ], ["pdf", "上傳 PDF", FileText], ["text", "貼上文字", Type]] as const).map(([id, label, Icon]) => (
              <button key={id} type="button" role="tab" aria-selected={mode === id} onClick={() => { setMode(id); setMessage(null); }} className={`flex items-center justify-center gap-2 border-b-2 px-2 py-3 text-xs font-medium transition sm:text-sm ${mode === id ? "border-indigo-500 text-indigo-700" : "border-transparent text-muted hover:text-ink"}`}>
                <Icon className="h-4 w-4" />{label}
              </button>
            ))}
          </div>

          {mode === "pdf" ? (
            <form onSubmit={submitPdf} className="mt-6">
              <label htmlFor="capture-pdf" className="grid min-h-44 cursor-pointer place-items-center rounded-lg border border-dashed border-line bg-subtle p-6 text-center">
                <span><Upload className="mx-auto h-6 w-6 text-indigo-600" /><span className="mt-3 block font-semibold">選擇 PDF 檔案</span><span className="mt-1 block text-xs text-muted">最多 10 MB，檔案會儲存在你的私人空間</span></span>
                <input id="capture-pdf" name="pdf" type="file" accept="application/pdf,.pdf" disabled={busy} className="mt-4 block w-full text-xs text-muted file:mr-3 file:rounded-md file:border-0 file:bg-elevated file:px-3 file:py-2 file:text-xs file:font-medium file:text-ink" />
              </label>
              {message ? <p role="alert" className="mt-3 text-xs text-rose-700">{message}</p> : null}
              <div className="mt-7 flex justify-end border-t border-line pt-5">
                <PrimaryButton type="submit" disabled={busy}>{busy ? <><LoaderCircle className="h-4 w-4 animate-spin" />上傳中…</> : "加入 Inbox"}</PrimaryButton>
              </div>
            </form>
          ) : (
            <div className="mt-6">
              <div className="rounded-lg border border-dashed border-line bg-subtle p-7 text-center">
                <p className="text-sm font-semibold">此新增方式尚未串接</p>
                <p className="mt-2 text-xs leading-5 text-muted">目前只有 PDF 上傳已連接既有 Storage 與處理佇列；{mode === "url" ? "網址收藏" : "文字收藏"}不會送出或建立假資料。</p>
              </div>
              <div className="mt-7 flex justify-end border-t border-line pt-5"><PrimaryButton type="button" disabled>尚未串接</PrimaryButton></div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
