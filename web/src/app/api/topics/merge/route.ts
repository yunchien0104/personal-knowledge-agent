import { z } from "zod";

import { authenticateApi, jsonError, jsonSuccess, validationError } from "@/lib/api";

const mergeSchema = z.object({ sourceTopicId: z.uuid(), targetTopicId: z.uuid() }).strict();

export async function POST(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  let input: unknown;
  try { input = await request.json(); } catch {
    return jsonError(400, "INVALID_JSON", "Request body must be valid JSON.");
  }
  const parsed = mergeSchema.safeParse(input);
  if (!parsed.success) return validationError(parsed.error);
  const { data, error } = await auth.supabase.rpc("merge_topics", {
    p_source_topic_id: parsed.data.sourceTopicId,
    p_target_topic_id: parsed.data.targetTopicId,
  });
  if (error) return jsonError(400, "MERGE_FAILED", "Topics could not be merged.");
  return jsonSuccess(data);
}
