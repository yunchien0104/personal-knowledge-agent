import { z } from "zod";

import { authenticateApi, jsonError, jsonSuccess, uuidSchema, validationError } from "@/lib/api";

const renameSchema = z.object({ name: z.string().trim().min(1).max(80) }).strict();

export async function PATCH(request: Request, context: { params: Promise<{ id: string }> }) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  const { id } = await context.params;
  if (!uuidSchema.safeParse(id).success) return jsonError(400, "INVALID_ID", "Topic id must be a UUID.");
  let input: unknown;
  try { input = await request.json(); } catch {
    return jsonError(400, "INVALID_JSON", "Request body must be valid JSON.");
  }
  const parsed = renameSchema.safeParse(input);
  if (!parsed.success) return validationError(parsed.error);
  const { data, error } = await auth.supabase.from("topics").update({ name: parsed.data.name })
    .eq("id", id).eq("user_id", auth.user.id).is("merged_into_id", null)
    .select("id,name,description,is_seed,created_at,updated_at").maybeSingle();
  if (error?.code === "23505") return jsonError(409, "TOPIC_EXISTS", "同名主題已存在。");
  if (error) return jsonError(500, "DATABASE_ERROR", "Topic could not be renamed.");
  if (!data) return jsonError(404, "NOT_FOUND", "Topic not found.");
  return jsonSuccess(data);
}
