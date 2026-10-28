"use client";

import { FileText, Link2, LoaderCircle, Type, Upload, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { uploadPdf } from "@/lib/pdf-upload-client";
import { uploadInstagramPost } from "@/lib/instagram-upload-client";
import { submitInstagramReelUrl } from "@/lib/instagram-reel-upload-client";
import { submitThreadsUrl } from "@/lib/threads";
import { PrimaryButton } from "./ui";

type CaptureMode = "url" | "instagram" | "pdf" | "text";

export function AddDataModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const [mode, setMode] = useState<CaptureMode>("url");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [instagramKind, setInstagramKind] = useState<"post" | "reel">("reel");

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

  async function submitUrl(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const input = event.currentTarget.elements.namedItem("url");
    const fallback = event.currentTarget.elements.namedItem("source_text");
    if (!(input instanceof HTMLInputElement) || !input.value.trim()) {
      setMessage("請先貼上 Threads 貼文網址。");
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      await submitThreadsUrl(
        input.value,
        fallback instanceof HTMLTextAreaElement ? fallback.value : undefined,
      );
      onClose();
      router.push("/library");
      router.refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Threads 貼文匯入失敗。");
    } finally {
      setBusy(false);
    }
  }

  async function submitInstagram(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const url = form.elements.namedItem("instagram_url");
    const contract = form.elements.namedItem("instagram_contract");
    const images = form.elements.namedItem("instagram_media");
    if (
      !(url instanceof HTMLInputElement) || !url.value.trim()
      || (instagramKind === "post" && (
        !(contract instanceof HTMLInputElement) || !contract.files?.[0]
        || !(images instanceof HTMLInputElement) || !images.files?.length
      ))
    ) {
      setMessage(instagramKind === "reel"
        ? "請提供公開 Reel 網址。"
        : "請提供 Instagram 貼文網址、Contract JSON 與全部圖片。");
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      if (instagramKind === "reel") {
        await submitInstagramReelUrl(url.value);
      } else if (contract instanceof HTMLInputElement && contract.files?.[0]
        && images instanceof HTMLInputElement) {
        await uploadInstagramPost(url.value, contract.files[0], Array.from(images.files ?? []));
      }
      onClose();
      router.push("/library");
      router.refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Instagram 匯入失敗。");
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
          <div role="tablist" aria-label="新增資料方式" className="grid grid-cols-4 border-b border-line">
            {([[
              "url", "貼網址", Link2,
            ], ["instagram", "Instagram", Upload], ["pdf", "上傳 PDF", FileText], ["text", "貼上文字", Type]] as const).map(([id, label, Icon]) => (
              <button key={id} type="button" role="tab" aria-selected={mode === id} onClick={() => { setMode(id); setMessage(null); }} className={`flex items-center justify-center gap-2 border-b-2 px-2 py-3 text-xs font-medium transition sm:text-sm ${mode === id ? "border-indigo-500 text-indigo-700" : "border-transparent text-muted hover:text-ink"}`}>
                <Icon className="h-4 w-4" />{label}
              </button>
            ))}
          </div>

          {mode === "instagram" ? (
            <form onSubmit={submitInstagram} className="mt-6 space-y-4">
              <fieldset className="flex gap-2" aria-label="Instagram 來源類型">
                <button type="button" onClick={() => setInstagramKind("reel")} className={`rounded-md border px-3 py-2 text-xs ${instagramKind === "reel" ? "border-indigo-400 bg-indigo-50 text-indigo-700" : "border-line text-muted"}`}>Reel 影片</button>
                <button type="button" onClick={() => setInstagramKind("post")} className={`rounded-md border px-3 py-2 text-xs ${instagramKind === "post" ? "border-indigo-400 bg-indigo-50 text-indigo-700" : "border-line text-muted"}`}>圖片貼文</button>
              </fieldset>
              <label htmlFor="instagram-url" className="block text-sm font-semibold">{instagramKind === "reel" ? "Instagram Reel 網址" : "Instagram 圖片貼文網址"}</label>
              <input id="instagram-url" name="instagram_url" type="url" required disabled={busy} placeholder={instagramKind === "reel" ? "https://www.instagram.com/reel/…/" : "https://www.instagram.com/p/…/"} className="w-full rounded-md border border-line bg-surface px-3 py-3 text-sm outline-none focus:border-indigo-300" />
              {instagramKind === "post" ? <>
                <label className="block text-sm font-semibold">Mac App 產生的 Contract JSON
                  <input name="instagram_contract" type="file" accept="application/json,.json" required disabled={busy} className="mt-2 block w-full text-xs text-muted" />
                </label>
                <label className="block text-sm font-semibold">貼文圖片（依貼文順序選取）
                  <input name="instagram_media" type="file" accept="image/jpeg,image/png" multiple required disabled={busy} className="mt-2 block w-full text-xs text-muted" />
                </label>
              </> : <p className="text-xs leading-5 text-muted">系統會在背景取得公開 Reel、轉錄人聲、分析關鍵畫面並自動建立筆記。</p>}
              {message ? <p role="alert" className="text-xs text-rose-700">{message}</p> : null}
              <div className="flex justify-end border-t border-line pt-5"><PrimaryButton type="submit" disabled={busy}>{busy ? <><LoaderCircle className="h-4 w-4 animate-spin" />上傳中…</> : "開始匯入"}</PrimaryButton></div>
            </form>
          ) : mode === "pdf" ? (
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
          ) : mode === "url" ? (
            <form onSubmit={submitUrl} className="mt-6">
              <label htmlFor="capture-url" className="block text-sm font-semibold">Threads 貼文網址</label>
              <input
                id="capture-url"
                name="url"
                type="url"
                inputMode="url"
                autoComplete="url"
                placeholder="https://www.threads.net/@account/post/…"
                disabled={busy}
                required
                className="mt-3 w-full rounded-md border border-line bg-surface px-3 py-3 text-sm outline-none transition placeholder:text-faint focus:border-indigo-300"
              />
              <p className="mt-2 text-xs leading-5 text-muted">只處理你明確送出的這一則公開貼文；不會追蹤帳號、串文或排程抓取。</p>
              <label htmlFor="capture-threads-text" className="mt-5 block text-sm font-semibold">
                貼文全文（選填）
              </label>
              <textarea
                id="capture-threads-text"
                name="source_text"
                rows={5}
                maxLength={100000}
                disabled={busy}
                placeholder="若公開貼文無法自動讀取，可將貼文全文貼在這裡。"
                className="mt-3 w-full resize-y rounded-md border border-line bg-surface px-3 py-3 text-sm outline-none transition placeholder:text-faint focus:border-indigo-300"
              />
              <p className="mt-2 text-xs leading-5 text-muted">有提供全文時會直接建立可引用的來源內容，不會再向 Threads 抓取。</p>
              {message ? <p role="alert" className="mt-3 text-xs text-rose-700">{message}</p> : null}
              <div className="mt-7 flex justify-end border-t border-line pt-5">
                <PrimaryButton type="submit" disabled={busy}>{busy ? <><LoaderCircle className="h-4 w-4 animate-spin" />建立匯入工作…</> : "開始匯入"}</PrimaryButton>
              </div>
            </form>
          ) : (
            <div className="mt-6">
              <div className="rounded-lg border border-dashed border-line bg-subtle p-7 text-center">
                <p className="text-sm font-semibold">此新增方式尚未串接</p>
                <p className="mt-2 text-xs leading-5 text-muted">文字收藏尚未連接既有處理佇列，不會送出或建立假資料。</p>
              </div>
              <div className="mt-7 flex justify-end border-t border-line pt-5"><PrimaryButton type="button" disabled>尚未串接</PrimaryButton></div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
