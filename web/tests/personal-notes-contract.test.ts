import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const fixtureRoot = new URL("../../fixtures/", import.meta.url);

async function fixture(path: string) {
  return JSON.parse(await readFile(new URL(path, fixtureRoot), "utf8")) as Record<string, unknown>;
}

test("all import fixtures are synthetic and structurally usable", async () => {
  const accounts = await fixture("accounts/accounts.json");
  assert.equal(accounts.syntheticOnly, true);
  assert.equal((accounts.accounts as unknown[]).length, 2);

  const knowledge = await fixture("knowledge/knowledge.json");
  assert.equal(knowledge.noteType, "source_note");
  assert.match(String(knowledge.body), /Synthetic/);

  const threads = await fixture("threads/threads.json");
  assert.equal(threads.sourceKind, "threads");
  assert.match(String(threads.canonicalUrl), /synthetic|fixture/);

  const reels = await fixture("reels/reels.json");
  assert.equal(reels.sourceKind, "reels");
  assert.match(String(reels.caption), /Synthetic/);

  const research = await fixture("research/research.json");
  assert.equal(research.status, "draft_ready");

  const citations = await fixture("citations/citations.json");
  const rows = citations.citations as Array<Record<string, unknown>>;
  assert.ok(rows.some((row) => row.validityStatus === "source_deleted"));
});

test("spreadsheet fixture preserves formulas as explicit fixture data", async () => {
  const spreadsheet = await fixture("spreadsheet/spreadsheet.json");
  assert.equal(spreadsheet.schemaVersion, 1);
  assert.match(JSON.stringify(spreadsheet), /=SUM\(/);
});
