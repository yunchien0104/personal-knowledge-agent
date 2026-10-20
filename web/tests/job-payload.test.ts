import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { parseJobPayloadV1 } from "../src/lib/job-payload.ts";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = path.resolve(webRoot, "..");
const fixturePath = path.join(
  repositoryRoot,
  "packages/contracts/fixtures/job-payload.v1.json",
);

test("TypeScript accepts the v1 job payload fixture", () => {
  const fixture: unknown = JSON.parse(readFileSync(fixturePath, "utf8"));
  assert.deepEqual(parseJobPayloadV1(fixture), fixture);
});

test("TypeScript and Python normalize the v1 fixture identically", () => {
  const fixture: unknown = JSON.parse(readFileSync(fixturePath, "utf8"));
  const expected = parseJobPayloadV1(fixture);
  const virtualenvPython = path.join(repositoryRoot, "worker/.venv/bin/python");
  const python = process.env.PYTHON ??
    (existsSync(virtualenvPython) ? virtualenvPython : "python3");
  const result = spawnSync(
    python,
    ["-m", "personal_brain.runner", "--validate-payload", fixturePath],
    {
      cwd: repositoryRoot,
      encoding: "utf8",
      env: {
        ...process.env,
        PYTHONPATH: path.join(repositoryRoot, "worker/src"),
      },
    },
  );

  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), expected);
});
