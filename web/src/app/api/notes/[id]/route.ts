import { z } from "zod";

import {
  authenticateApi,
  jsonError,
  jsonSuccess,
  uuidSchema,
  validationError,
} from "@/lib/api";

const NOTE_COLUMNS =
  "id,user_id,note_type,title,body,body_revision,publication_state,created_at,updated_at";

export async function GET(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  const { id } = await context.params;
  if (!uuidSchema.safeParse(id).success) {
    return jsonError(400, "INVALID_ID", "Note id must be a UUID.");
  }

  const { data, error } = await auth.supabase
    .from("notes")
    .select(NOTE_COLUMNS)
    .eq("id", id)
    .maybeSingle();

  if (error) return jsonError(500, "DATABASE_ERROR", "The note could not be loaded.");
  if (!data) return jsonError(404, "NOT_FOUND", "Note not found.");
  return jsonSuccess(data);
}

const updateNoteSchema = z.object({
  title: z.string().trim().min(1).max(300),
  body: z.string().max(100000),
  body_revision: z.number().int().positive(),
}).strict();

export async function PATCH(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;
  const { id } = await context.params;
  if (!uuidSchema.safeParse(id).success) {
    return jsonError(400, "INVALID_ID", "Note id must be a UUID.");
  }
  let input: unknown;
  try {
    input = await request.json();
  } catch {
    return jsonError(400, "INVALID_JSON", "Request body must be valid JSON.");
  }
  const parsed = updateNoteSchema.safeParse(input);
  if (!parsed.success) return validationError(parsed.error);
  const { data, error } = await auth.supabase.rpc("update_note_content", {
    p_note_id: id,
    p_expected_revision: parsed.data.body_revision,
    p_title: parsed.data.title,
    p_body: parsed.data.body,
  });
  if (error) return jsonError(500, "DATABASE_ERROR", "The note could not be saved.");
  if (!data) {
    return jsonError(409, "EDIT_CONFLICT", "The note changed; reload before saving again.");
  }
  return jsonSuccess(data);
}
