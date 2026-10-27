import { z } from "zod";

import { authenticateApi, jsonError, jsonSuccess, validationError } from "@/lib/api";

const createTopicSchema = z.object({
  name: z.string().trim().min(1).max(80),
  description: z.string().trim().max(500).default(""),
}).strict();

export async function GET() {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  const { data, error } = await auth.supabase.rpc("ensure_default_topics");
  if (error) return jsonError(500, "DATABASE_ERROR", "Topics could not be loaded.");
  return jsonSuccess(data ?? []);
}

export async function POST(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  let input: unknown;
  try { input = await request.json(); } catch {
    return jsonError(400, "INVALID_JSON", "Request body must be valid JSON.");
  }
  const parsed = createTopicSchema.safeParse(input);
  if (!parsed.success) return validationError(parsed.error);
  const { data, error } = await auth.supabase.from("topics").insert({
    user_id: auth.user.id,
    name: parsed.data.name,
    description: parsed.data.description,
  }).select("id,name,description,is_seed,created_at,updated_at").single();
  if (error?.code === "23505") return jsonError(409, "TOPIC_EXISTS", "同名主題已存在。");
  if (error) return jsonError(500, "DATABASE_ERROR", "Topic could not be created.");
  return jsonSuccess(data, 201);
}
