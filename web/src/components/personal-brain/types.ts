export type Asset = {
  id: string;
  source_type: "pdf" | "instagram_reel" | "threads";
  source_url: string | null;
  source_external_id: string | null;
  status: string;
  active_revision_id: string | null;
  created_at: string;
  updated_at: string;
};

export type Note = {
  id: string;
  note_type: string;
  title: string;
  body: string;
  body_revision: number;
  publication_state: string;
  created_at: string;
  updated_at: string;
};

export type Opportunity = {
  id: string;
  question: string;
  rationale: string;
  score: number;
  status: string;
  signal_types: string[];
  topic_ids: string[];
  evidence_ids: string[];
  recommended_at: string;
  cooldown_until: string | null;
  created_at: string;
};

export type SourceKind = "PDF" | "IG" | "TH" | "NOTE";
export type KnowledgeStatus =
  | "已完成"
  | "處理中"
  | "待檢視"
  | "失敗"
  | "刪除中"
  | "刪除失敗"
  | "原檔已移除";

export type KnowledgeItem = {
  id: string;
  kind: SourceKind;
  category: "note" | "source";
  title: string;
  meta: string;
  status: KnowledgeStatus;
  excerpt: string;
  updatedAt: string;
  sourceStatus?: string;
};

export type AssetDeletion = {
  id: string;
  asset_id: string | null;
  asset_id_snapshot: string;
  mode: "remove_original" | "purge_all";
  status: "pending" | "deleting" | "completed" | "failed";
  error_code: string | null;
  error_message: string | null;
  requested_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
};

export type RetrievalCitation = {
  assetId: string;
  revisionId: string;
  evidenceId: string;
  locator: Record<string, unknown>;
  validityStatus: string;
};

export type RetrievalResult = {
  resultType: "source_note" | "claim" | "evidence";
  contentOrigin: "generated_note" | "generated_claim" | "original_source";
  resultId: string;
  title: string;
  snippet: string;
  relevance: number;
  assetId: string;
  revisionId: string;
  noteId: string | null;
  claimId: string | null;
  citations: RetrievalCitation[];
  createdAt: string;
};

export type ResearchCitation = {
  id: string;
  relationType: string;
  evidenceId: string;
  assetId: string;
  revisionId: string;
  locator: Record<string, unknown>;
  quote: string;
  validityStatus: string;
  sourceUrl?: string;
};

export type ResearchRun = {
  id: string;
  question: string;
  plan: Record<string, unknown> | null;
  status: string;
  createdAt: string;
  updatedAt: string;
  errorCode: string | null;
  note: { id: string; title: string; body: string; publicationState: string } | null;
  citations: ResearchCitation[];
};

export type ResearchRunSummary = {
  id: string;
  question: string;
  status: string;
  created_at: string;
  updated_at: string;
};
