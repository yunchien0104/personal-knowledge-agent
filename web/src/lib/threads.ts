import { z } from "zod";

const THREADS_HOSTS = new Set([
  "threads.net",
  "www.threads.net",
  "threads.com",
  "www.threads.com",
]);
const POST_PATH = /^\/(?:@[^/]+\/post|t)\/([A-Za-z0-9_-]{5,64})\/?$/;

export type ThreadsTarget = { canonicalUrl: string; postId: string };

export function normalizeThreadsUrl(value: string): ThreadsTarget | null {
  try {
    const url = new URL(value.trim());
    const match = url.pathname.match(POST_PATH);
    if (
      url.protocol !== "https:"
      || !THREADS_HOSTS.has(url.hostname.toLowerCase())
      || url.username
      || url.password
      || url.port
      || !match
    ) return null;
    const canonicalPath = url.pathname.startsWith("/t/")
      ? `/t/${match[1]}`
      : url.pathname.replace(/\/$/, "");
    return {
      // Preserve the account-qualified path because /t/:id can return a generic shell.
      canonicalUrl: `https://www.threads.net${canonicalPath}`,
      postId: match[1],
    };
  } catch {
    return null;
  }
}

export function threadsCitationUrl(locator: unknown): string | null {
  if (!locator || typeof locator !== "object") return null;
  const value = locator as Record<string, unknown>;
  if (
    value.kind !== "threads_post"
    || typeof value.url !== "string"
    || typeof value.post_id !== "string"
  ) return null;
  const target = normalizeThreadsUrl(value.url);
  return target?.postId === value.post_id ? target.canonicalUrl : null;
}

export const threadsEnqueueResultSchema = z.object({
  duplicate: z.boolean(),
  asset: z.object({
    id: z.uuid(),
    source_type: z.literal("threads"),
    source_url: z.string(),
    source_external_id: z.string(),
    status: z.string(),
    active_revision_id: z.uuid().nullable(),
    created_at: z.string(),
    updated_at: z.string(),
  }),
  job: z.object({
    id: z.uuid(),
    status: z.string(),
    idempotencyKey: z.string(),
  }).nullable(),
});

export async function submitThreadsUrl(url: string, sourceText?: string) {
  const fallback = sourceText?.trim();
  const response = await fetch("/api/assets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source_type: "threads",
      source_url: url,
      ...(fallback ? { source_text: fallback } : {}),
    }),
  });
  const payload = await response.json() as {
    data: unknown;
    error?: { code?: string; message?: string };
  };
  if (!response.ok) {
    throw new Error(payload.error?.message ?? "Threads 貼文匯入失敗。");
  }
  return threadsEnqueueResultSchema.parse(payload.data);
}
