import { z } from "zod";

export const referenceCategorySchema = z.enum(["research", "knowledge", "personal"]);
export const knowledgeSourceTypeSchema = z.enum([
  "all",
  "pdf",
  "instagram_reel",
  "instagram_post",
  "threads",
]);

export type ReferenceCategory = z.infer<typeof referenceCategorySchema>;
export type KnowledgeSourceType = z.infer<typeof knowledgeSourceTypeSchema>;

export const referenceSearchSchema = z
  .object({
    category: referenceCategorySchema,
    sourceType: knowledgeSourceTypeSchema.default("all"),
    query: z.string().trim().max(200).default(""),
    currentNoteId: z.uuid().optional(),
    limit: z.coerce.number().int().min(1).max(50).default(20),
    offset: z.coerce.number().int().min(0).default(0),
  })
  .superRefine((value, context) => {
    if (value.category !== "knowledge" && value.sourceType !== "all") {
      context.addIssue({
        code: "custom",
        path: ["sourceType"],
        message: "Source type filters are only valid for Knowledge references.",
      });
    }
  });

export const referenceSourceSchema = z.object({
  id: z.uuid(),
  category: referenceCategorySchema,
  title: z.string(),
  summary: z.string(),
  content: z.string(),
  source_type: z.enum(["pdf", "instagram_reel", "instagram_post", "threads"]).nullable(),
  source_url: z.string().nullable(),
  source_asset_id: z.uuid().nullable(),
  source_revision_id: z.uuid().nullable(),
  research_run_id: z.uuid().nullable(),
  created_at: z.string(),
  total_count: z.coerce.number().int().min(0),
});

export type ReferenceSource = z.infer<typeof referenceSourceSchema>;

export const REFERENCE_CATEGORY_LABELS: Record<ReferenceCategory, string> = {
  research: "研究報告",
  knowledge: "知識",
  personal: "筆記",
};

export const KNOWLEDGE_SOURCE_LABELS: Record<KnowledgeSourceType, string> = {
  all: "全部",
  pdf: "PDF",
  instagram_reel: "Instagram Reels",
  instagram_post: "Instagram Posts",
  threads: "Threads",
};

export function sourceKindForCategory(category: ReferenceCategory) {
  return category;
}
