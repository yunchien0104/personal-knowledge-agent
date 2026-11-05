import assert from "node:assert/strict";
import test from "node:test";

import {
  defaultResearchTemplates,
  researchTemplateSchema,
  templateAnalysisSpec,
} from "../src/lib/research-template-ui.ts";

test("ships four valid research templates with distinct ids", () => {
  assert.equal(defaultResearchTemplates.length, 4);
  assert.equal(researchTemplateSchema.array().safeParse(defaultResearchTemplates).success, true);
  assert.equal(new Set(defaultResearchTemplates.map((template) => template.id)).size, 4);
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
