"use client";

import { ArrowRight, Bookmark, BookmarkCheck, Check, EyeOff, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { getApi } from "./api";
import type { Opportunity } from "./types";
import { PageTitle, PendingState, SoftButton } from "./ui";

export function ExplorePage() {
  const [items, setItems] = useState<Opportunity[]>([]);
  const [saved, setSaved] = useState<string[]>([]);
  const [hidden, setHidden] = useState<string[]>([]);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const visible = items.filter((item) => !hidden.includes(item.id));
  const featured = visible[0];

  useEffect(() => {
    let active = true;
    void getApi<Opportunity[]>("/api/research/opportunities?limit=20")
      .then((data) => { if (active) setItems(data); })
      .catch((reason: unknown) => { if (active) setMessage(reason instanceof Error ? reason.message : "研究提案讀取失敗。"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function feedback(id: string, value: "saved" | "skip") {
    try {
      await getApi<unknown>("/api/research/opportunities", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ opportunity_id: id, feedback: value }) });
      if (value === "saved") setSaved((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
      else setHidden((current) => [...current, id]);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "無法儲存偏好。");
    }
  }

  return (
    <div className="animate-page">
      <PageTitle title="探索精選" action={<span className="text-xs font-medium text-muted">推薦偏好設定尚未串接</span>} />
      {message ? <p role="status" className="mb-5 rounded-lg border border-line bg-subtle px-4 py-3 text-xs text-muted">{message}</p> : null}
      {loading ? <p className="py-8 text-sm text-muted">正在讀取研究提案…</p> : null}
      {!loading && !featured ? <PendingState title="目前沒有研究提案" description="後端尚未產生 Research Opportunities；此頁不會用 Mock Data 代替。" /> : null}

      {featured ? <>
        <section className="grid gap-8 border-y border-line py-7 lg:grid-cols-[1.25fr_.75fr]">
          <div><div className="flex flex-wrap items-center gap-2"><span className="text-xs font-medium text-ink">研究機會</span><span className="text-[11px] text-muted">· {new Date(featured.recommended_at).toLocaleDateString("zh-TW")}</span></div><h2 className="mt-4 max-w-3xl text-[24px] font-semibold leading-[1.45] tracking-[-0.03em]">{featured.question}</h2><p className="mt-4 max-w-2xl text-sm leading-7 text-muted">{featured.rationale}</p><div className="mt-5 flex flex-wrap gap-x-4 gap-y-2">{featured.signal_types.map((signal) => <span key={signal} className="text-xs text-muted">#{signal}</span>)}</div><div className="mt-7 flex flex-wrap gap-2"><button type="button" onClick={() => setPreviewOpen(true)} className="inline-flex items-center gap-2 rounded-md bg-indigo-500 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-700">預覽研究計畫 <ArrowRight className="h-4 w-4" /></button><SoftButton type="button" onClick={() => void feedback(featured.id, "saved")}>{saved.includes(featured.id) ? <BookmarkCheck className="h-4 w-4" /> : <Bookmark className="h-4 w-4" />}{saved.includes(featured.id) ? "已收藏方向" : "收藏方向"}</SoftButton><SoftButton type="button" onClick={() => void feedback(featured.id, "skip")}><EyeOff className="h-4 w-4" />略過</SoftButton></div><p className="mt-3 text-[11px] text-faint">預覽不會啟動研究；只有你前往研究頁並送出問題後才會建立 Research Run。</p></div>
          <aside className="border-l-0 border-line lg:border-l lg:pl-7"><p className="text-xs font-medium text-muted">可追溯訊號</p><dl className="mt-4 space-y-4 text-xs"><div><dt className="text-muted">Evidence</dt><dd className="mt-1 font-semibold text-ink">{featured.evidence_ids.length} 筆</dd></div><div><dt className="text-muted">Topic IDs</dt><dd className="mt-1 break-all text-ink">{featured.topic_ids.length ? featured.topic_ids.join(", ") : "尚未連結"}</dd></div><div><dt className="text-muted">推薦分數</dt><dd className="mt-1 font-semibold text-ink">{featured.score}</dd></div></dl></aside>
        </section>

        <div className="mt-8 grid gap-px overflow-hidden rounded-lg border border-line bg-line lg:grid-cols-2">{visible.slice(1).map((item) => <article key={item.id} className="bg-surface p-5 sm:p-6"><p className="text-[11px] font-medium text-indigo-600">{item.signal_types.join(" · ") || "研究機會"}</p><h2 className="mt-3 text-lg font-semibold leading-7">{item.question}</h2><p className="mt-3 line-clamp-3 text-sm leading-6 text-muted">{item.rationale}</p><div className="mt-5 flex items-center justify-between gap-4"><p className="text-xs text-muted">{item.evidence_ids.length} 個相關 Evidence</p><div className="flex items-center gap-2"><Link href={`/research?q=${encodeURIComponent(item.question)}`} className="rounded-md px-3 py-2 text-xs font-semibold text-indigo-700 hover:bg-indigo-50">查看研究方向</Link><button type="button" aria-label={saved.includes(item.id) ? "取消儲存" : "儲存"} onClick={() => void feedback(item.id, "saved")} className={`rounded-md p-2.5 ${saved.includes(item.id) ? "bg-indigo-50 text-indigo-700" : "text-muted hover:bg-subtle"}`}>{saved.includes(item.id) ? <BookmarkCheck className="h-4 w-4" /> : <Bookmark className="h-4 w-4" />}</button></div></div></article>)}</div>
      </> : null}

      {previewOpen && featured ? <div className="fixed inset-0 z-50 grid place-items-end bg-black/25 p-0 sm:place-items-center sm:p-6" onMouseDown={(event) => event.target === event.currentTarget && setPreviewOpen(false)}><section role="dialog" aria-modal="true" aria-label="研究計畫預覽" className="max-h-[90vh] w-full overflow-y-auto rounded-t-xl border border-line bg-surface p-5 shadow-soft sm:max-w-xl sm:rounded-lg sm:p-7"><div className="flex items-start justify-between gap-4"><div><p className="text-xs text-muted">研究計畫預覽</p><h2 className="mt-2 text-xl font-semibold leading-8">{featured.question}</h2></div><button type="button" aria-label="關閉研究計畫預覽" onClick={() => setPreviewOpen(false)} className="rounded-md p-2 text-muted hover:bg-subtle"><X className="h-4 w-4" /></button></div><div className="mt-6"><PendingState title="完整計畫尚未建立" description="Research Plan 只會在你明確送出研究問題後由既有 Research API 建立。" /></div><div className="mt-6 flex items-start gap-3 text-xs leading-5 text-muted"><Check className="mt-0.5 h-4 w-4 shrink-0" /><p>這個預覽使用真實 Opportunity 資料，不會自動執行研究。</p></div><div className="mt-6 flex justify-end gap-2"><button type="button" onClick={() => setPreviewOpen(false)} className="rounded-md border border-line px-4 py-2.5 text-sm font-medium hover:bg-subtle">稍後再看</button><Link href={`/research?q=${encodeURIComponent(featured.question)}`} className="rounded-md bg-indigo-500 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-700">前往研究頁</Link></div></section></div> : null}
    </div>
  );
}
