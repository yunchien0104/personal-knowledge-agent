export type KnowledgeNoteType = "source_note" | "research_note" | "manual_note";
export type KnowledgeSourceType = "pdf" | "instagram_reel" | "instagram_post" | "threads";

const sourceNoteLabels: Record<KnowledgeSourceType, string> = {
  pdf: "PDF 來源筆記",
  threads: "Threads 來源筆記",
  instagram_reel: "Reels 來源筆記",
  instagram_post: "Instagram 貼文來源筆記",
};

export function knowledgeNoteLabel(
  noteType: KnowledgeNoteType,
  sourceType: KnowledgeSourceType | null,
) {
  if (noteType === "research_note") return "研究筆記";
  if (noteType === "manual_note") return "手寫筆記";
  return sourceType ? sourceNoteLabels[sourceType] : "來源筆記";
}
