import { ArrowLeft, ExternalLink } from "lucide-react";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { connection } from "next/server";

import { SourceNoteControls } from "@/app/assets/[id]/source-note/source-note-controls";
import { uuidSchema } from "@/lib/api";
import { loadSourceNoteWorkspace, safeSourceNoteError } from "@/lib/source-notes";
import { createServerSupabaseClient } from "@/lib/supabase";

export const instant = false;

export default async function SourceNotePage({ params }: { params: Promise<{ id: string }> }) {
  await connection();
  const { id } = await params;
  if (!uuidSchema.safeParse(id).success) notFound();

  const supabase = await createServerSupabaseClient();
  const { data: { user }, error } = await supabase.auth.getUser();
  if (error || !user) redirect("/auth/login");

  const workspace = await loadSourceNoteWorkspace(supabase, user.id, id);
  if (!workspace) notFound();
  const note = workspace.note;

  return (
    <div className="mx-auto max-w-5xl animate-page">
      <Link
        href="/library"
        className="inline-flex items-center gap-2 rounded-md px-2 py-2 text-xs font-semibold text-muted transition hover:bg-subtle hover:text-ink"
      >
        <ArrowLeft className="h-4 w-4" />
        返回 Library
      </Link>

      <header className="mt-4 border-b border-line pb-5">
        <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-faint">Knowledge source</p>
        <h1 className="mt-2 text-2xl font-semibold tracking-[-0.025em] text-ink sm:text-3xl">Source Note</h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-muted">閱讀、編輯來源摘要，並檢查每項 Claim 對應的原始引用。</p>
      </header>

      {workspace.job?.status === "failed" ? (
        <p role="alert" className="mt-5 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {safeSourceNoteError(workspace.job.errorCode)}
        </p>
      ) : null}

      <div className="mt-6">
        <SourceNoteControls
          assetId={workspace.asset.id}
          assetReady={workspace.asset.status === "ready"}
          jobStatus={workspace.job?.status ?? null}
          note={note ? {
            id: note.id,
            title: note.title,
            body: note.body,
            bodyRevision: note.bodyRevision,
          } : null}
        />
      </div>

      {note ? (
        <div className="mt-8 space-y-8">
          <section className="grid gap-4 rounded-xl border border-line bg-surface p-5 sm:grid-cols-2">
            <div>
              <h2 className="text-sm font-semibold text-ink">主題分類</h2>
              <p className="mt-2 text-sm leading-6 text-muted">
                {note.topics.length ? note.topics.map((topic) => topic.name).join("、") : "未分類"}
              </p>
            </div>
            <dl className="space-y-2 text-xs text-muted">
              <div>
                <dt className="font-medium text-ink">來源 Revision</dt>
                <dd className="mt-1 break-all font-mono text-[11px]">{note.revisionId}</dd>
              </div>
              <div>
                <dt className="font-medium text-ink">生成模型</dt>
                <dd className="mt-1 break-words">{note.modelProvider} / {note.modelName} · {note.promptVersion}</dd>
              </div>
            </dl>
            {workspace.asset.status === "source_removed" ? (
              <p role="status" className="sm:col-span-2 rounded-lg bg-subtle px-3 py-2 text-xs leading-5 text-muted">
                原始來源已移除；筆記與既有引用文字仍保留，但原檔連結已停用。
              </p>
            ) : null}
          </section>

          <section>
            <div className="border-b border-line pb-4">
              <h2 className="text-lg font-semibold tracking-[-0.015em] text-ink">Claims 與引用</h2>
              <p className="mt-1 text-xs leading-5 text-muted">所有 Claims 目前均為「未審核」，不代表已獨立證實。</p>
            </div>
            <ul className="mt-4 grid gap-4">
              {note.claims.map((claim) => (
                <li key={claim.id} className="min-w-0 rounded-xl border border-line bg-surface p-4 sm:p-5">
                  <p className="break-words text-sm font-semibold leading-6 text-ink">{claim.text}</p>
                  <p className="mt-2 text-[11px] font-medium text-muted">
                    {claim.claimType} · {claim.verificationStatus}{claim.needsReview ? " · 需人工審核" : ""}
                  </p>
                  <ul className="mt-4 grid gap-3">
                    {claim.citations.map((citation) => (
                      <li key={citation.evidenceId} className="min-w-0 rounded-lg bg-subtle p-3 text-xs leading-6 text-muted">
                        <div className="mb-1 flex flex-wrap items-center gap-2">
                          {note.pdfUrl && citation.page ? (
                            <a href={`${note.pdfUrl}#page=${citation.page}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold text-indigo-700 hover:underline">
                              PDF 第 {citation.page} 頁 <ExternalLink className="h-3 w-3" />
                            </a>
                          ) : citation.sourceUrl ? (
                            <>
                              <a href={citation.sourceUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold text-indigo-700 hover:underline">
                                {citation.imageIndex ? `Instagram 第 ${citation.imageIndex} 張圖片` : "原始貼文"} <ExternalLink className="h-3 w-3" />
                              </a>
                              {citation.imageUrl ? (
                                <a href={citation.imageUrl} target="_blank" rel="noreferrer" className="font-semibold text-indigo-700 hover:underline">私人圖片預覽</a>
                              ) : null}
                            </>
                          ) : (
                            <span className="font-mono text-[11px]">Evidence {citation.evidenceId}</span>
                          )}
                          <span className="rounded-full border border-line bg-surface px-2 py-0.5 text-[10px]">{citation.validityStatus}</span>
                        </div>
                        <blockquote className="break-words text-ink">「{citation.quote}」</blockquote>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </section>
        </div>
      ) : null}
    </div>
  );
}
