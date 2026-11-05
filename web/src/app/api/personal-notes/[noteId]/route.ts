import { z } from "zod";
import {
  authenticateApi,
  jsonError,
  jsonSuccess,
  uuidSchema,
  validationError,
} from "@/lib/api";

const updateSchema = z.object({
  title: z.string().trim().min(1).max(300).optional(),
  content_json: z.record(z.string(), z.any()).optional(),
  plain_text: z.string().max(1000000).optional(),
  expected_revision: z.number().int().positive().optional(),
  reason: z.enum(["autosave", "manual", "import", "restore"]).default("autosave"),
}).strict();

export async function GET(
  _request: Request,
  context: { params: Promise<{ noteId: string }> }
) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  const { noteId } = await context.params;
  if (!uuidSchema.safeParse(noteId).success) {
    return jsonError(400, "INVALID_ID", "Note id 必須為有效的 UUID");
  }

  const { data: note, error: noteError } = await auth.supabase
    .from("notes")
    .select(`
      id,
      user_id,
      title,
      note_type,
      body_revision,
      created_at,
      updated_at,
      personal_note_documents (
        content_json,
        plain_text,
        schema_version,
        updated_at
      )
    `)
    .eq("id", noteId)
    .eq("note_type", "manual_note")
    .maybeSingle();

  if (noteError) {
    return jsonError(500, "DATABASE_ERROR", "讀取筆記失敗", noteError.message);
  }
  if (!note) {
    return jsonError(404, "NOT_FOUND", "找不到指定的筆記");
  }

  const doc = Array.isArray(note.personal_note_documents)
    ? note.personal_note_documents[0]
    : note.personal_note_documents;

  return jsonSuccess({
    id: note.id,
    title: note.title,
    body_revision: note.body_revision,
    created_at: note.created_at,
    updated_at: note.updated_at,
    content_json: doc?.content_json || { type: "doc", content: [] },
    plain_text: doc?.plain_text || "",
  });
}

export async function PATCH(
  request: Request,
  context: { params: Promise<{ noteId: string }> }
) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  const { noteId } = await context.params;
  if (!uuidSchema.safeParse(noteId).success) {
    return jsonError(400, "INVALID_ID", "Note id 必須為有效的 UUID");
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return jsonError(400, "INVALID_JSON", "Request body 必須為合法 JSON");
  }

  const parsed = updateSchema.safeParse(body);
  if (!parsed.success) return validationError(parsed.error);

  const { title, content_json, plain_text, expected_revision, reason } = parsed.data;

  // 1. 查詢當前版本與狀態
  const { data: currentNote, error: fetchErr } = await auth.supabase
    .from("notes")
    .select("id, title, body_revision")
    .eq("id", noteId)
    .eq("note_type", "manual_note")
    .maybeSingle();

  if (fetchErr) return jsonError(500, "DATABASE_ERROR", "查詢筆記失敗", fetchErr.message);
  if (!currentNote) return jsonError(404, "NOT_FOUND", "找不到指定的筆記");

  // 樂觀併發控制 (OCC) 檢查
  if (expected_revision !== undefined && currentNote.body_revision !== expected_revision) {
    return jsonError(
      409,
      "EDIT_CONFLICT",
      "筆記已在其他視窗或連線中被修改，請重新整理以取得最新版本",
      { current_revision: currentNote.body_revision, expected_revision }
    );
  }

  const nextRevision = (currentNote.body_revision || 1) + 1;
  const newTitle = title ?? currentNote.title;

  // 2. 更新 notes 主表
  const noteUpdates: Record<string, unknown> = {
    title: newTitle,
    body_revision: nextRevision,
    updated_at: new Date().toISOString(),
  };
  if (plain_text !== undefined) noteUpdates.body = plain_text.slice(0, 5000);

  const { data: updatedNote, error: updateNoteErr } = await auth.supabase
    .from("notes")
    .update(noteUpdates)
    .eq("id", noteId)
    .eq("body_revision", currentNote.body_revision)
    .select("body_revision")
    .maybeSingle();

  if (updateNoteErr) {
    return jsonError(500, "DATABASE_ERROR", "更新筆記失敗", updateNoteErr.message);
  }
  if (!updatedNote) {
    return jsonError(
      409,
      "EDIT_CONFLICT",
      "筆記已在其他視窗或連線中被修改，請重新整理以取得最新版本",
      { expected_revision: currentNote.body_revision }
    );
  }

  // 3. 更新 personal_note_documents
  const docUpdates: Record<string, unknown> = {
    updated_at: new Date().toISOString(),
  };
  if (content_json !== undefined) docUpdates.content_json = content_json;
  if (plain_text !== undefined) docUpdates.plain_text = plain_text;

  const { data: updatedDoc, error: docErr } = await auth.supabase
    .from("personal_note_documents")
    .update(docUpdates)
    .eq("note_id", noteId)
    .select("content_json, plain_text, updated_at")
    .single();

  if (docErr || !updatedDoc) {
    return jsonError(500, "DATABASE_ERROR", "更新筆記內容失敗", docErr?.message);
  }

  const finalDoc = updatedDoc as {
    content_json: Record<string, unknown>;
    plain_text: string;
    updated_at: string;
  };

  // 4. 寫入版本紀錄
  if (reason === "manual" || nextRevision % 5 === 0) {
    await auth.supabase.from("personal_note_versions").insert({
      note_id: noteId,
      user_id: auth.user.id,
      body_revision: nextRevision,
      title: newTitle,
      content_json: finalDoc.content_json,
      plain_text: finalDoc.plain_text,
      reason,
    });
  }

  return jsonSuccess({
    id: noteId,
    title: newTitle,
    body_revision: nextRevision,
    content_json: finalDoc.content_json,
    plain_text: finalDoc.plain_text,
    updated_at: finalDoc.updated_at,
  });
}

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ noteId: string }> }
) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  const { noteId } = await context.params;
  if (!uuidSchema.safeParse(noteId).success) {
    return jsonError(400, "INVALID_ID", "Note id 必須為有效的 UUID");
  }

  const { error } = await auth.supabase
    .from("notes")
    .delete()
    .eq("id", noteId)
    .eq("note_type", "manual_note");

  if (error) {
    return jsonError(500, "DATABASE_ERROR", "刪除筆記失敗", error.message);
  }

  return jsonSuccess({ success: true, id: noteId });
}
