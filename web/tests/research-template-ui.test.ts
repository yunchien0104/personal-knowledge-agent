import assert from "node:assert/strict";
import test from "node:test";

import {
  defaultResearchTemplates,
  researchStartPayload,
  researchTemplateSchema,
  templateAnalysisSpec,
} from "../src/lib/research-template-ui.ts";

test("ships four valid research templates with distinct ids", () => {
  assert.equal(defaultResearchTemplates.length, 4);
  assert.equal(researchTemplateSchema.array().safeParse(defaultResearchTemplates).success, true);
  assert.equal(new Set(defaultResearchTemplates.map((template) => template.id)).size, 4);
});

test("legacy stays unchanged while deep receives the selected template", () => {
  const template = defaultResearchTemplates[2];
  const legacy = researchStartPayload("legacy", "Question", "request", template);
  assert.deepEqual(legacy, { question: "Question", request_key: "request" });

  const deep = researchStartPayload("deep", "Question", "request", template);
  assert.equal(deep.mode, "deep");
  assert.equal(deep.web_policy, "disabled");
  assert.equal(deep.analysis_spec.framework, "counterargument");
  assert.equal(deep.analysis_spec.parameters.templateId, template.id);
  assert.deepEqual(deep.analysis_spec.parameters.sections, template.sections.map(
    ({ title, purpose, required }) => ({ title, purpose, required }),
  ));
});

test("template analysis adapter stays inside the existing analysis-spec-v1 contract", () => {
  const spec = templateAnalysisSpec(defaultResearchTemplates[1]);
  assert.equal(spec.schemaVersion, "analysis-spec-v1");
  assert.equal(spec.framework, "comparative");
  assert.equal(Array.isArray(spec.parameters.sections), true);
  assert.equal("prompt" in spec.parameters, false);
});

test("rejects duplicate section ids and fewer than two sections", () => {
  const template = defaultResearchTemplates[0];
  assert.equal(researchTemplateSchema.safeParse({ ...template, sections: [template.sections[0]] }).success, false);
  assert.equal(researchTemplateSchema.safeParse({ ...template, sections: [template.sections[0], template.sections[0]] }).success, false);
});
