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
    <main style={{ padding: "3rem", maxWidth: 900, margin: "0 auto" }}>
      <nav style={{ display: "flex", gap: "1rem", marginBottom: "2rem" }}>
        <Link href="/inbox">Inbox</Link>
        <Link href="/protected">帳號</Link>
      </nav>
      <h1>Source Note</h1>
      {workspace.job?.status === "failed" ? (
        <p role="alert">{safeSourceNoteError(workspace.job.errorCode)}</p>
      ) : null}
      <SourceNoteControls
        assetId={workspace.asset.id}
        assetReady={workspace.asset.status === "ready"}
        jobStatus={workspace.job?.status ?? null}
        note={note ? {
          id: note.id, title: note.title, body: note.body, bodyRevision: note.bodyRevision,
        } : null}
      />
      {note ? (
        <>
          <p>
            來源 Revision：<code>{note.revisionId}</code><br />
            模型：{note.modelProvider} / {note.modelName} · Prompt {note.promptVersion}
          </p>
          <h2>Claims 與引用</h2>
          <p>所有 Claims 目前均為「未審核」，不代表已獨立證實。</p>
          <ul style={{ display: "grid", gap: "1rem", padding: 0, listStyle: "none" }}>
            {note.claims.map((claim) => (
              <li key={claim.id} style={{ border: "1px solid currentColor", padding: "1rem" }}>
                <strong>{claim.text}</strong>
                <p>{claim.claimType} · {claim.verificationStatus}{claim.needsReview ? " · 需人工審核" : ""}</p>
                <ul>
                  {claim.citations.map((citation) => (
                    <li key={citation.evidenceId}>
                      {note.pdfUrl && citation.page ? (
                        <a href={`${note.pdfUrl}#page=${citation.page}`} target="_blank" rel="noreferrer">
                          PDF 第 {citation.page} 頁
                        </a>
                      ) : `Evidence ${citation.evidenceId}`}
                      ：「{citation.quote}」 ({citation.validityStatus})
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </main>
  );
}
