"use client";

import { ArrowRight, BookOpen, ChevronRight, Clock3, FileText } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { getApi, toKnowledgeItems } from "./api";
import type { Asset, KnowledgeItem, Note, Opportunity } from "./types";
import { Card, PendingState, SourceIcon, StatusBadge } from "./ui";

export function HomePage() {
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void Promise.all([
      getApi<Asset[]>("/api/assets?limit=12"),
      getApi<Note[]>("/api/notes?limit=12"),
      getApi<Opportunity[]>("/api/research/opportunities?limit=4"),
    ]).then(([assets, notes, suggestions]) => {
      if (!active) return;
      setItems(toKnowledgeItems(assets, notes));
      setOpportunities(suggestions);
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "資料讀取失敗。");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, []);

  const notes = items.filter((item) => item.category === "note").slice(0, 3);
  const recommendation = opportunities[0];

  return (
    <div className="animate-page">
      <header className="mb-8"><h1 className="text-[28px] font-semibold tracking-[-0.03em] text-ink sm:text-[32px]">知識摘要</h1></header>
      {error ? <p role="alert" className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</p> : null}

      <div className="grid gap-8 xl:grid-cols-[minmax(0,1.45fr)_minmax(300px,.72fr)]">
        <section>
          <div className="flex items-end justify-between border-b border-line pb-3"><div><h2 className="text-lg font-semibold">今日重點</h2><p className="mt-1 text-xs text-muted">依你的真實筆記更新</p></div></div>
          {loading ? <p className="py-8 text-sm text-muted">正在讀取知識摘要…</p> : notes.length ? (
            <div className="divide-y divide-line">{notes.map((item, index) => <article key={item.id} className="grid gap-3 py-6 sm:grid-cols-[36px_minmax(0,1fr)]"><span className="pt-0.5 font-serif text-sm italic text-faint">0{index + 1}</span><div><p className="text-xs font-medium text-indigo-600">{item.meta}</p><h3 className="mt-2 text-[17px] font-semibold leading-7 text-ink">{item.title}</h3><p className="mt-2 line-clamp-3 max-w-2xl text-sm leading-6 text-muted">{item.excerpt}</p></div></article>)}</div>
          ) : <PendingState title="尚無可摘要的筆記" description="上傳並完成處理來源後，真實筆記會出現在這裡。" />}
        </section>

        {recommendation ? (
          <Card className="self-start overflow-hidden bg-subtle"><div className="border-b border-indigo-200 px-5 py-4"><p className="text-xs font-medium text-indigo-700">延伸研究</p></div><div className="p-5"><h2 className="text-xl font-semibold leading-8 tracking-[-0.02em]">{recommendation.question}</h2><p className="mt-3 text-sm leading-6 text-muted">{recommendation.rationale}</p><div className="mt-5 border-t border-indigo-200 pt-4"><p className="text-[11px] font-medium text-muted">研究訊號</p><p className="mt-2 text-xs leading-5 text-muted">{recommendation.signal_types.length ? recommendation.signal_types.join(" · ") : "尚未標註訊號"}</p></div><Link href="/explore" className="mt-6 inline-flex items-center gap-2 text-sm font-semibold text-indigo-700">查看研究提案 <ArrowRight className="h-4 w-4" /></Link></div></Card>
        ) : <PendingState title="目前沒有研究提案" description="系統尚未產生可驗證的研究機會，不會以 Mock Data 填補。" />}
      </div>

      <div className="mt-10 grid gap-8 xl:grid-cols-[minmax(0,1.4fr)_minmax(300px,.74fr)]">
        <section>
          <div className="flex items-end justify-between border-b border-line pb-3"><div><h2 className="text-lg font-semibold">最近加入</h2><p className="mt-1 text-xs text-muted">文件與個人筆記</p></div><Link href="/library" className="flex items-center gap-1 text-xs font-medium text-indigo-600">前往知識庫 <ChevronRight className="h-3.5 w-3.5" /></Link></div>
          {items.length ? <div className="grid gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-2">{items.slice(0, 4).map((item) => <Link href="/library" key={item.id} className="group bg-surface p-4 transition hover:bg-subtle"><div className="flex h-20 items-center justify-center rounded-md bg-subtle"><SourceIcon kind={item.kind} size="sm" /></div><p className="mt-4 text-[10px] text-muted">{item.category === "note" ? "個人筆記" : "來源"} · {item.kind}</p><h3 className="mt-2 line-clamp-2 text-sm font-semibold leading-5 group-hover:underline">{item.title}</h3><p className="mt-2 line-clamp-2 text-xs leading-5 text-muted">{item.excerpt}</p><div className="mt-4 flex items-center justify-between gap-2"><span className="truncate text-[10px] text-faint">{item.meta}</span><StatusBadge status={item.status} /></div></Link>)}</div> : <div className="pt-5"><PendingState title="知識庫目前是空的" description="新增 PDF 後，真實處理狀態會顯示在這裡。" /></div>}
        </section>

        <div className="space-y-7">
          <section><div className="flex items-center justify-between border-b border-line pb-3"><h2 className="text-base font-semibold">我的主題</h2><BookOpen className="h-4 w-4 text-muted" /></div><div className="pt-4"><PendingState title="主題分類尚未串接" description="目前後端尚未提供 Topics API，因此不顯示模擬產業分類。" /></div></section>
          <Card className="p-5"><p className="text-xs font-medium text-indigo-600">回顧建議</p><p className="mt-3 text-sm font-medium leading-6">回顧排程尚未串接</p><p className="mt-2 flex items-center gap-1.5 text-[11px] text-faint"><Clock3 className="h-3.5 w-3.5" />不會以假日期或假閱讀時間填補</p><span className="mt-4 flex items-center gap-2 text-xs text-muted"><FileText className="h-3.5 w-3.5" />等待真實回顧資料</span></Card>
        </div>
      </div>
    </div>
  );
}
