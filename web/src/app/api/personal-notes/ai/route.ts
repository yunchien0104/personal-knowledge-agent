import { authenticateApi, jsonError, validationError } from "@/lib/api";
import { getNotesAiProvider } from "@/lib/notes-ai/provider";
import { checkNotesAiRateLimit } from "@/lib/notes-ai/rate-limit";
import { NotesAiProviderError } from "@/lib/notes-ai/types";
import { NOTES_AI_MAX_REQUEST_BYTES, notesAiRequestSchema } from "@/lib/notes-ai/validation";

export async function POST(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  const rateLimit = checkNotesAiRateLimit(auth.user.id);
  if (!rateLimit.allowed) {
    return Response.json(
      { data: null, error: { code: "RATE_LIMITED", message: "AI 請求過於頻繁，請稍後再試。" } },
      { status: 429, headers: { "Retry-After": String(rateLimit.retryAfterSeconds), "Cache-Control": "no-store" } },
    );
  }

  const contentLength = Number(request.headers.get("content-length") ?? 0);
  if (contentLength > NOTES_AI_MAX_REQUEST_BYTES) {
    return jsonError(413, "REQUEST_TOO_LARGE", "AI 請求內容超過大小限制。");
  }

  let rawBody = "";
  try {
    rawBody = await request.text();
  } catch {
    return jsonError(400, "INVALID_REQUEST", "無法讀取 AI 請求內容。");
  }
  if (new TextEncoder().encode(rawBody).byteLength > NOTES_AI_MAX_REQUEST_BYTES) {
    return jsonError(413, "REQUEST_TOO_LARGE", "AI 請求內容超過大小限制。");
  }

  let parsedJson: unknown;
  try {
    parsedJson = JSON.parse(rawBody);
  } catch {
    return jsonError(400, "INVALID_JSON", "AI 請求必須是有效 JSON。");
  }
  const parsed = notesAiRequestSchema.safeParse(parsedJson);
  if (!parsed.success) return validationError(parsed.error);

  try {
    const provider = getNotesAiProvider();
    const result = await provider.generate(parsed.data);
    return Response.json(
      {
        generatedText: result.generatedText,
        operation: parsed.data.operation,
        provider: `${result.provider} (${result.model})`,
      },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    if (error instanceof NotesAiProviderError) {
      return jsonError(error.status, `NOTES_AI_${error.code}`, error.message);
    }
    return jsonError(500, "INTERNAL_ERROR", "AI 請求無法完成。");
  }
}
