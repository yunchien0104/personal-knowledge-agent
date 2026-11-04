import { z } from "zod";
import {
  authenticateApi,
  jsonError,
  jsonSuccess,
  validationError,
} from "@/lib/api";

const createNoteSchema = z.object({
  title: z.string().trim().min(1).max(300).default("未命名筆記"),
  content_json: z.record(z.string(), z.any()).optional(),
  plain_text: z.string().max(1000000).optional(),
}).strict();

interface NoteDbRow {
  id: string;
  title: string;
  body_revision: number;
  created_at: string;
  updated_at: string;
  personal_note_documents?: Array<{ plain_text?: string }> | { plain_text?: string };
}

export async function GET(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  const url = new URL(request.url);
  const q = url.searchParams.get("q")?.trim();

  let query = auth.supabase
    .from("notes")
    .select(`
      id,
      title,
      body_revision,
      created_at,
      updated_at,
      personal_note_documents (
        plain_text
      )
    `)
    .eq("note_type", "manual_note")
    .order("updated_at", { ascending: false });

  if (q) {
    query = query.ilike("title", `%${q}%`);
  }

  const { data, error } = await query;
  if (error) {
    return jsonError(500, "DATABASE_ERROR", "無法取得筆記列表", error.message);
  }

  const rawRows = (data || []) as unknown as NoteDbRow[];
  const notes = rawRows.map((row) => {
    const doc = Array.isArray(row.personal_note_documents)
      ? row.personal_note_documents[0]
      : row.personal_note_documents;
    return {
      id: row.id,
      title: row.title,
      body_revision: row.body_revision,
      created_at: row.created_at,
      updated_at: row.updated_at,
      snippet: doc?.plain_text ? doc.plain_text.slice(0, 160) : "",
    };
  });

  return jsonSuccess({ notes });
}

export async function POST(request: Request) {
  const auth = await authenticateApi();
  if ("response" in auth) return auth.response;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return jsonError(400, "INVALID_JSON", "Request body 必須為合法 JSON");
  }

  const parsed = createNoteSchema.safeParse(body);
  if (!parsed.success) return validationError(parsed.error);

  const { title, content_json, plain_text } = parsed.data;
  const initialContent = content_json || {
    type: "doc",
    content: [{ type: "paragraph", content: [{ type: "text", text: "" }] }],
  };

  const { data: note, error: noteErr } = await auth.supabase
    .from("notes")
    .insert({
      user_id: auth.user.id,
      note_type: "manual_note",
      title,
      body: plain_text ? plain_text.slice(0, 5000) : "",
      body_revision: 1,
    })
    .select("id, title, body_revision, created_at, updated_at")
    .single();

  if (noteErr || !note) {
    return jsonError(500, "DATABASE_ERROR", "建立筆記失敗", noteErr?.message);
  }

  const { error: docErr } = await auth.supabase
    .from("personal_note_documents")
    .insert({
      note_id: note.id,
      user_id: auth.user.id,
      schema_version: 1,
      content_json: initialContent,
      plain_text: plain_text || "",
    });

  if (docErr) {
    await auth.supabase.from("notes").delete().eq("id", note.id);
    return jsonError(500, "DATABASE_ERROR", "建立筆記內容結構失敗", docErr.message);
  }

  return jsonSuccess(
    {
      id: note.id,
      title: note.title,
      body_revision: note.body_revision,
      created_at: note.created_at,
      updated_at: note.updated_at,
      content_json: initialContent,
      plain_text: plain_text || "",
    },
    201
  );
}
