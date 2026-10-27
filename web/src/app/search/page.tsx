import Link from "next/link";
import { redirect } from "next/navigation";
import { connection } from "next/server";

import { PDF_BUCKET, pdfObjectPath } from "@/lib/pdf";
import { instagramCitation } from "@/lib/instagram";
import { instagramReelCitation } from "@/lib/instagram-reel";
import {
  citationPdfPage,
  originLabel,
  parseRetrievalInput,
  retrieveKnowledge,
} from "@/lib/retrieval";
import { createServerSupabaseClient } from "@/lib/supabase";
import { threadsCitationUrl } from "@/lib/threads";


export const instant = false;

export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  await connection();
  const supabase = await createServerSupabaseClient();
  const { data: { user }, error } = await supabase.auth.getUser();
  if (error || !user) redirect("/auth/login");
  const params = await searchParams;
  const rawQuery = typeof params.q === "string" ? params.q : "";
  const parsed = rawQuery ? parseRetrievalInput({ q: rawQuery }) : null;
  const retrieval = parsed?.success
    ? await retrieveKnowledge(supabase, parsed.data)
    : { results: [], total: 0 };

  const assetIds = [...new Set(retrieval.results.flatMap((result) =>
    result.citations
      .filter((citation) => citation.validityStatus === "valid" && citationPdfPage(citation))
      .map((citation) => citation.assetId)
  ))];
  const signedUrls = new Map<string, string>();
  const citationAssetIds = [...new Set(retrieval.results.flatMap((result) =>
    result.citations.map((citation) => citation.assetId)
  ))];
  const { data: citationAssets } = citationAssetIds.length
    ? await supabase.from("assets").select("id,source_url").eq("user_id", user.id).in("id", citationAssetIds)
    : { data: [] };
  const sourceUrls = new Map((citationAssets ?? []).map((asset) => [asset.id, asset.source_url]));
  await Promise.all(assetIds.map(async (assetId) => {
    const signed = await supabase.storage
      .from(PDF_BUCKET)
      .createSignedUrl(pdfObjectPath(user.id, assetId), 300);
    if (!signed.error) signedUrls.set(assetId, signed.data.signedUrl);
  }));

  return (
    <main style={{ padding: "3rem", maxWidth: 900, margin: "0 auto" }}>
      <nav style={{ display: "flex", gap: "1rem", marginBottom: "2rem" }}>
        <Link href="/inbox">Inbox</Link>
        <Link href="/protected">帳號</Link>
      </nav>
      <h1>Knowledge Search</h1>
      <form action="/search" method="get" style={{ display: "flex", gap: "0.5rem" }}>
        <label htmlFor="knowledge-query">搜尋 Source Notes、Claims 與原始來源</label>
        <input
          id="knowledge-query"
          name="q"
          type="search"
          defaultValue={rawQuery}
          minLength={2}
          maxLength={200}
          required
        />
        <button type="submit">搜尋</button>
      </form>
      {rawQuery && parsed && !parsed.success ? <p role="alert">搜尋詞需為 2–200 個字元。</p> : null}
      {parsed?.success ? <p>找到 {retrieval.total} 筆結果。</p> : null}
      <ol style={{ display: "grid", gap: "1rem", paddingLeft: "1.5rem" }}>
        {retrieval.results.map((result) => (
          <li key={`${result.resultType}:${result.resultId}`}>
            <h2>{result.title}</h2>
            <p><strong>{originLabel(result.contentOrigin)}</strong> · {result.resultType}</p>
            <p>{result.snippet}</p>
            {result.noteId ? (
              <p><Link href={`/assets/${result.assetId}/source-note`}>查看 Source Note</Link></p>
            ) : null}
            <ul>
              {result.citations.map((citation) => {
                const page = citationPdfPage(citation);
                const pdfUrl = signedUrls.get(citation.assetId);
                const sourceUrl = threadsCitationUrl(citation.locator);
                const instagram = instagramCitation(citation.locator);
                const reel = instagramReelCitation(citation.locator, sourceUrls.get(citation.assetId));
                return (
                  <li key={citation.evidenceId}>
                    {pdfUrl && page && citation.validityStatus === "valid" ? (
                      <a href={`${pdfUrl}#page=${page}`} target="_blank" rel="noreferrer">
                        PDF 第 {page} 頁
                      </a>
                    ) : sourceUrl ? (
                      <a href={sourceUrl} target="_blank" rel="noreferrer">
                        Threads 原始貼文
                      </a>
                    ) : instagram ? (
                      <>
                        <a href={instagram.sourceUrl} target="_blank" rel="noreferrer">
                          {instagram.imageIndex ? `Instagram 第 ${instagram.imageIndex} 張圖片` : "Instagram 原始貼文"}
                        </a>
                        {instagram.mediaId ? <> · <a href={`/api/assets/${citation.assetId}/evidence/${citation.evidenceId}/media`} target="_blank" rel="noreferrer">私人圖片預覽</a></> : null}
                      </>
                    ) : reel ? (
                      <>
                        <a href={reel.sourceUrl} target="_blank" rel="noreferrer">
                          Reel {reel.startSeconds === null ? "說明文字" : `${reel.startSeconds.toFixed(1)}–${reel.endSeconds?.toFixed(1)} 秒`}
                        </a>
                        {reel.mediaId ? <> · <a href={`/api/assets/${citation.assetId}/evidence/${citation.evidenceId}/media`} target="_blank" rel="noreferrer">Keyframe 預覽</a></> : null}
                      </>
                    ) : `Evidence ${citation.evidenceId}`}
                    {citation.validityStatus !== "valid" ? ` (${citation.validityStatus})` : null}
                  </li>
                );
              })}
            </ul>
          </li>
        ))}
      </ol>
    </main>
  );
}
