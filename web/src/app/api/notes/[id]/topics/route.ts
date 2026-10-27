import { z } from "zod";

import { authenticateApi, jsonError, jsonSuccess, uuidSchema, validationError } from "@/lib/api";

const selectionSchema = z.object({ topicIds: z.array(z.uuid()).max(20) }).strict();

export async function PUT(request: Request, context: { params: Promise<{ id: string }> }) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  const { id } = await context.params;
  if (!uuidSchema.safeParse(id).success) return jsonError(400, "INVALID_ID", "Note id must be a UUID.");
  let input: unknown;
  try { input = await request.json(); } catch {
    return jsonError(400, "INVALID_JSON", "Request body must be valid JSON.");
  }
  const parsed = selectionSchema.safeParse(input);
  if (!parsed.success) return validationError(parsed.error);
  const { data, error } = await auth.supabase.rpc("set_note_topics", {
    p_note_id: id,
    p_topic_ids: parsed.data.topicIds,
  });
  if (error) return jsonError(400, "TOPIC_SAVE_FAILED", "筆記主題無法儲存。");
  return jsonSuccess(data);
}
