import { z } from "zod";

export const researchTemplateSectionSchema = z.object({
  id: z.string().trim().regex(/^[a-z][a-z0-9_]{1,63}$/),
  title: z.string().trim().min(1).max(120),
  purpose: z.string().trim().min(1).max(600),
  required: z.boolean(),
}).strict();

export const researchTemplateSchema = z.object({
  id: z.string().trim().min(1).max(100),
  name: z.string().trim().min(1).max(100),
  description: z.string().trim().min(1).max(500),
  framework: z.enum(["general", "comparative", "counterargument", "scenario"]),
  tone: z.enum(["neutral", "executive", "academic"]),
  depth: z.enum(["concise", "standard", "deep"]),
  sections: z.array(researchTemplateSectionSchema).min(2).max(12),
}).strict().superRefine((template, context) => {
  const ids = template.sections.map((section) => section.id);
  if (new Set(ids).size !== ids.length) {
    context.addIssue({ code: "custom", path: ["sections"], message: "Section IDs must be unique." });
  }
});

export type ResearchTemplate = z.infer<typeof researchTemplateSchema>;

export const defaultResearchTemplates: ResearchTemplate[] = [
  {
    id: "evidence-brief",
    name: "證據導向研究",
    description: "先整理已知證據，再區分結論、限制與待確認事項。",
    framework: "general",
    tone: "neutral",
    depth: "standard",
    sections: [
      { id: "executive_summary", title: "研究摘要", purpose: "回答核心問題並標示結論強度。", required: true },
      { id: "key_findings", title: "主要發現", purpose: "逐項呈現可由 Evidence 支持的發現。", required: true },
      { id: "evidence_gaps", title: "證據缺口", purpose: "列出尚未獲得充分支持的問題。", required: true },
      { id: "next_steps", title: "後續行動", purpose: "提出可驗證且具體的下一步。", required: false },
    ],
  },
  {
    id: "comparative-analysis",
    name: "比較分析",
    description: "以一致維度比較多個方案，避免只列優缺點而缺少判準。",
    framework: "comparative",
    tone: "executive",
    depth: "standard",
    sections: [
      { id: "comparison_scope", title: "比較範圍", purpose: "定義比較對象、界線與共同假設。", required: true },
      { id: "comparison_dimensions", title: "比較維度", purpose: "使用相同判準逐項比較。", required: true },
      { id: "tradeoffs", title: "取捨分析", purpose: "說明各方案的成本、風險與限制。", required: true },
      { id: "recommendation", title: "建議", purpose: "依 Evidence 說明適用情境與建議選擇。", required: false },
    ],
  },
  {
    id: "claim-challenge",
    name: "主張與反證",
    description: "同時整理支持與反對證據，檢查主張是否仍然成立。",
    framework: "counterargument",
    tone: "academic",
    depth: "deep",
    sections: [
      { id: "claim_definition", title: "核心主張", purpose: "精確定義要檢驗的主張與範圍。", required: true },
      { id: "supporting_arguments", title: "支持證據", purpose: "整理支持主張的 Evidence 與推理。", required: true },
      { id: "counterarguments", title: "反方與反證", purpose: "呈現最強反方觀點與矛盾證據。", required: true },
      { id: "judgement", title: "綜合判斷", purpose: "說明主張成立的條件、程度與不確定性。", required: true },
    ],
  },
  {
    id: "scenario-planning",
    name: "情境規劃",
    description: "以關鍵變因建立多種情境，辨識共同風險與決策訊號。",
    framework: "scenario",
    tone: "executive",
    depth: "deep",
    sections: [
      { id: "drivers", title: "關鍵驅動因子", purpose: "找出可能改變結果的主要變因。", required: true },
      { id: "scenario_baseline", title: "基準情境", purpose: "描述在現有趨勢延續下的可能結果。", required: true },
      { id: "scenario_alternatives", title: "替代情境", purpose: "比較樂觀、壓力或轉折情境。", required: true },
      { id: "signals_actions", title: "監測訊號與行動", purpose: "列出可觀察訊號與對應決策。", required: false },
    ],
  },
];

export function cloneResearchTemplate(template: ResearchTemplate): ResearchTemplate {
  return structuredClone(template);
}

export function templateAnalysisSpec(template: ResearchTemplate) {
  return {
    schemaVersion: "analysis-spec-v1" as const,
    framework: template.framework,
    parameters: {
      templateId: template.id,
      templateName: template.name,
      templateDescription: template.description,
      tone: template.tone,
      depth: template.depth,
      sections: template.sections.map(({ title, purpose, required }) => ({ title, purpose, required })),
    },
  };
}

export function researchStartPayload(
  mode: "legacy" | "deep",
  question: string,
  requestKey: string,
  template: ResearchTemplate,
) {
  if (mode === "legacy") {
    return { question, request_key: requestKey };
  }
  return {
    question,
    request_key: requestKey,
    mode: "deep" as const,
    web_policy: "disabled" as const,
    analysis_spec: templateAnalysisSpec(template),
  };
}
