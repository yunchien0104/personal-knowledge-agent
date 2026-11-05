import { z } from "zod";

import { authenticateApi, jsonError, jsonSuccess, uuidSchema, validationError } from "@/lib/api";

const importSchema = z.object({
  note_id: uuidSchema.optional(),
  source_kind: z.enum([
    "knowledge", "personal", "threads", "reels", "research", "markdown", "spreadsheet",
  ]),
  source_id: uuidSchema,
  operation: z.enum(["reference", "partial", "full"]).default("full"),
  selection_text: z.string().max(20_000).optional(),
  append_mode: z.boolean().optional(),
}).strict().superRefine((value, context) => {
  if (value.operation === "partial" && !value.selection_text?.trim()) {
    context.addIssue({
      code: "custom",
      path: ["selection_text"],
      message: "Partial imports require selected source text.",
    });
  }
});

type NoteSourceKind = "knowledge" | "research" | "personal";

function plainTextDocument(text: string) {
  return {
    type: "doc",
    content: text ? [{ type: "paragraph", content: [{ type: "text", text }] }] : [],
  };
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

  const parsed = importSchema.safeParse(body);
  if (!parsed.success) return validationError(parsed.error);

  const { note_id, source_kind, source_id, operation, selection_text } = parsed.data;
  const userId = auth.user.id;
  let snapshotTitle = "";
  let snapshotText = "";
  let snapshotContentJson: Record<string, unknown> = { type: "doc", content: [] };
  let sourceAssetId: string | null = null;
  let sourceRevisionId: string | null = null;
  let sourceUrl: string | null = null;
  let researchRunId: string | null = null;
  let sourceType: string | null = null;

  if (["knowledge", "research", "personal"].includes(source_kind)) {
    const typedKind = source_kind as NoteSourceKind;
    const requiredNoteType = {
      knowledge: "source_note",
      research: "research_note",
      personal: "manual_note",
    }[typedKind];

    const { data: sourceNote, error: sourceError } = await auth.supabase
      .from("notes")
      .select("id,title,body,note_type,publication_state")
      .eq("id", source_id)
      .eq("user_id", userId)
      .eq("note_type", requiredNoteType)
      .maybeSingle();
    if (sourceError || !sourceNote || sourceNote.publication_state === "archived") {
      return jsonError(404, "SOURCE_NOT_FOUND", "找不到可存取的指定來源");
    }
    if (typedKind === "personal" && note_id === source_id) {
      return jsonError(400, "INVALID_SOURCE", "目前筆記不能引用自己");
    }

    if (typedKind === "research") {
      if (sourceNote.publication_state !== "published") {
        return jsonError(404, "SOURCE_NOT_FOUND", "研究報告尚未發布");
      }
      const { data: run, error: runError } = await auth.supabase
        .from("research_runs")
        .select("id,status")
        .eq("user_id", userId)
        .eq("output_note_id", source_id)
        .in("status", ["draft_ready", "completed"])
        .maybeSingle();
      if (runError || !run) {
        return jsonError(404, "SOURCE_NOT_FOUND", "找不到已完成的研究報告");
      }
      researchRunId = run.id;
    }

    if (typedKind === "knowledge") {
      const { data: generation, error: generationError } = await auth.supabase
        .from("source_note_generations")
        .select("asset_id_snapshot,source_revision_id_snapshot")
        .eq("user_id", userId)
        .eq("note_id", source_id)
        .maybeSingle();
      if (generationError || !generation) {
        return jsonError(404, "SOURCE_NOT_FOUND", "知識缺少有效的來源關聯");
      }
      sourceAssetId = generation.asset_id_snapshot;
      sourceRevisionId = generation.source_revision_id_snapshot;
      const { data: asset } = await auth.supabase
        .from("assets")
        .select("source_type,source_url")
        .eq("user_id", userId)
        .eq("id", sourceAssetId)
        .maybeSingle();
      sourceType = asset?.source_type ?? null;
      sourceUrl = asset?.source_url ?? null;
    }

    if (typedKind === "personal") {
      const { data: document } = await auth.supabase
        .from("personal_note_documents")
        .select("content_json,plain_text")
        .eq("user_id", userId)
        .eq("note_id", source_id)
        .maybeSingle();
      snapshotText = document?.plain_text ?? sourceNote.body ?? "";
      snapshotContentJson = document?.content_json ?? plainTextDocument(snapshotText);
    } else {
      snapshotText = sourceNote.body ?? "";
      snapshotContentJson = plainTextDocument(snapshotText);
    }
    snapshotTitle = sourceNote.title;
  } else {
    const { data: asset, error: assetError } = await auth.supabase
      .from("assets")
      .select("id,source_type,source_url,active_revision_id")
      .eq("id", source_id)
      .eq("user_id", userId)
      .maybeSingle();
    if (assetError || !asset) {
      return jsonError(404, "SOURCE_NOT_FOUND", "找不到指定的媒體資產");
    }
    sourceAssetId = asset.id;
    sourceRevisionId = asset.active_revision_id;
    sourceType = asset.source_type;
    sourceUrl = asset.source_url;
    snapshotTitle = `匯入自 ${source_kind} 資產`;
    snapshotText = asset.source_url ?? "";
    snapshotContentJson = plainTextDocument(snapshotText);
  }

  const selectedText = selection_text?.trim();
  if (operation === "partial" && selectedText && !snapshotText.includes(selectedText)) {
    return jsonError(400, "INVALID_SELECTION", "選取內容不屬於指定來源");
  }

  let targetNoteId = note_id;
  if (targetNoteId) {
    const { data: target } = await auth.supabase
      .from("notes")
      .select("id")
      .eq("id", targetNoteId)
      .eq("user_id", userId)
      .eq("note_type", "manual_note")
      .maybeSingle();
    if (!target) return jsonError(404, "NOTE_NOT_FOUND", "找不到可寫入的個人筆記");
  } else {
    const { data: newNote, error: newNoteError } = await auth.supabase
      .from("notes")
      .insert({
        user_id: userId,
        note_type: "manual_note",
        title: snapshotTitle,
        body: snapshotText.slice(0, 5000),
        body_revision: 1,
      })
      .select("id")
      .single();
    if (newNoteError || !newNote) {
      return jsonError(500, "DATABASE_ERROR", "建立匯入筆記失敗", newNoteError?.message);
    }
    targetNoteId = newNote.id;
    const { error: documentError } = await auth.supabase.from("personal_note_documents").insert({
      note_id: targetNoteId,
      user_id: userId,
      schema_version: 1,
      content_json: snapshotContentJson,
      plain_text: snapshotText,
    });
    if (documentError) {
      return jsonError(500, "DATABASE_ERROR", "建立匯入文件失敗", documentError.message);
    }
  }

  const isNoteSource = ["knowledge", "research", "personal"].includes(source_kind);
  const { data: importRecord, error: importError } = await auth.supabase
    .from("personal_note_imports")
    .insert({
      note_id: targetNoteId,
      user_id: userId,
      source_kind,
      source_note_id: isNoteSource ? source_id : null,
      source_asset_id: sourceAssetId,
      source_revision_id: sourceRevisionId,
      snapshot_title: snapshotTitle,
      snapshot_text: snapshotText,
      snapshot_content_json: snapshotContentJson,
    })
    .select("id")
    .single();
  if (importError || !importRecord) {
    return jsonError(500, "DATABASE_ERROR", "建立匯入快照失敗", importError?.message);
  }

  const supportingQuote = operation === "partial"
    ? selectedText ?? ""
    : snapshotText.slice(0, operation === "reference" ? 500 : 20_000);
  const { error: citationError } = await auth.supabase.from("personal_note_citations").insert({
    note_id: targetNoteId,
    user_id: userId,
    import_id: importRecord.id,
    asset_id_snapshot: sourceAssetId,
    revision_id_snapshot: sourceRevisionId,
    locator_json: {
      category: source_kind,
      operation,
      sourceNoteId: isNoteSource ? source_id : null,
      researchRunId,
      sourceType,
    },
    source_label: snapshotTitle,
    source_url: sourceUrl,
    supporting_quote: supportingQuote,
    validity_status: "valid",
  });
  if (citationError) {
    await auth.supabase
      .from("personal_note_imports")
      .delete()
      .eq("id", importRecord.id)
      .eq("user_id", userId);
    return jsonError(500, "DATABASE_ERROR", "建立來源引用失敗", citationError.message);
  }

  return jsonSuccess({
    note_id: targetNoteId,
    import_id: importRecord.id,
    source_kind,
    operation,
    snapshot_title: snapshotTitle,
    snapshot_text: snapshotText,
    snapshot_content_json: snapshotContentJson,
    source_asset_id: sourceAssetId,
    source_revision_id: sourceRevisionId,
  }, 201);
}
