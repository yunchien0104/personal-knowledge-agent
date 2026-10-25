import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { basename, resolve } from "node:path";
import { spawnSync } from "node:child_process";

type Envelope<T> = { data: T | null; error?: { code: string; message: string } };

const REQUIRED_ACK = "YES_I_HAVE_VERIFIED_ISOLATION_AND_BACKUP";
const REQUIRED_LOCAL_ACK = "YES_I_HAVE_VERIFIED_LOCAL_ISOLATION";
const root = resolve(import.meta.dirname, "../..");
const worker = resolve(root, "worker/.venv/bin/python");

function required(name: string) {
  const value = process.env[name]?.trim();
  assert(value, `${name} is required`);
  return value;
}

const stagingRef = required("STAGING_REF");
const productionRef = required("PRODUCTION_REF");
const stagingMode = process.env.STAGING_MODE?.trim() || "cloud";
const supabaseUrl = required("STAGING_SUPABASE_URL");
const serviceRoleKey = required("STAGING_SERVICE_ROLE_KEY");
const webUrl = required("STAGING_WEB_URL").replace(/\/$/, "");
const email = required("STAGING_TEST_EMAIL");
const password = required("STAGING_TEST_PASSWORD");
const pdfPath = resolve(required("STAGING_TEST_PDF"));

assert.notEqual(stagingRef, productionRef, "Staging and Production refs must differ");
if (stagingMode === "cloud") {
  assert.equal(process.env.ALLOW_STAGING_E2E, REQUIRED_ACK, "explicit Staging E2E acknowledgement is required");
  assert.equal(new URL(supabaseUrl).origin, `https://${stagingRef}.supabase.co`);
  assert.equal(new URL(webUrl).protocol, "https:");
  assert(required("STAGING_BACKUP_EVIDENCE"), "restorable backup/PITR evidence is required");
  assert(required("STAGING_ISOLATION_EVIDENCE"), "environment isolation evidence is required");
} else {
  assert.equal(stagingMode, "local", "STAGING_MODE must be cloud or local");
  assert.equal(process.env.ALLOW_STAGING_E2E, REQUIRED_LOCAL_ACK, "explicit Local Staging acknowledgement is required");
  for (const [label, value] of [["Supabase", supabaseUrl], ["Web", webUrl]] as const) {
    const url = new URL(value);
    assert.equal(url.protocol, "http:", `${label} Local Staging must use HTTP`);
    assert(["127.0.0.1", "localhost", "::1"].includes(url.hostname), `${label} must be loopback-only`);
  }
  assert.equal(
    required("STAGING_ISOLATION_EVIDENCE"),
    "disposable-local-supabase",
    "Local Staging must be explicitly identified as disposable",
  );
}

class CookieJar {
  private readonly values = new Map<string, string>();

  capture(response: Response) {
    for (const cookie of response.headers.getSetCookie()) {
      const [pair] = cookie.split(";", 1);
      const separator = pair.indexOf("=");
      this.values.set(pair.slice(0, separator), pair.slice(separator + 1));
    }
  }

  header() {
    return [...this.values].map(([name, value]) => `${name}=${value}`).join("; ");
  }
}

const jar = new CookieJar();

async function app<T>(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  if (jar.header()) headers.set("cookie", jar.header());
  const response = await fetch(`${webUrl}${path}`, { ...init, headers, redirect: "manual" });
  jar.capture(response);
  const payload = await response.json() as Envelope<T>;
  assert(response.ok, `${init.method ?? "GET"} ${path} failed: ${JSON.stringify(payload.error)}`);
  assert(payload.data, `${path} returned no data`);
  return payload.data;
}

function serviceHeaders(extra?: HeadersInit) {
  const headers = new Headers(extra);
  headers.set("apikey", serviceRoleKey);
  if (serviceRoleKey.startsWith("eyJ")) headers.set("authorization", `Bearer ${serviceRoleKey}`);
  return headers;
}

async function service<T>(path: string, init: RequestInit = {}) {
  const response = await fetch(`${supabaseUrl}/rest/v1/${path}`, {
    ...init,
    headers: serviceHeaders(init.headers),
  });
  const text = await response.text();
  assert(response.ok, `service request ${path} failed: ${text}`);
  return text ? JSON.parse(text) as T : null as T;
}

function runWorker(capability: "pdf" | "retrieval" | "research") {
  const result = spawnSync(worker, ["-m", "personal_brain.runner", "--once"], {
    cwd: resolve(root, "worker"),
    encoding: "utf8",
    timeout: 10 * 60 * 1000,
    env: {
      ...process.env,
      SUPABASE_SERVER_URL: supabaseUrl,
      SUPABASE_SERVICE_ROLE_KEY: serviceRoleKey,
      WORKER_ID: `staging-e2e-${capability}`,
      WORKER_CAPABILITY: capability,
    },
  });
  assert.equal(result.status, 0, `${capability} worker failed: ${result.stderr}`);
  return JSON.parse(result.stdout.trim()) as { status: string; job_id?: string };
}

async function waitFor<T>(label: string, probe: () => Promise<T | null>, drive: () => void) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const value = await probe();
    if (value) return value;
    drive();
  }
  assert.fail(`${label} did not complete within 50 bounded worker attempts`);
}

const signIn = await fetch(`${webUrl}/auth/actions/sign-in`, {
  method: "POST",
  body: new URLSearchParams({ email, password }),
  redirect: "manual",
});
jar.capture(signIn);
assert([302, 303].includes(signIn.status), `Staging sign-in failed (${signIn.status})`);

const pdf = readFileSync(pdfPath);
assert(pdf.subarray(0, 5).toString() === "%PDF-", "STAGING_TEST_PDF must be a PDF");
const prepared = await app<{ asset: { id: string }; upload: { signed_url: string } }>(
  "/api/assets/upload",
  {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      action: "prepare",
      file_name: basename(pdfPath),
      file_size: pdf.byteLength,
      mime_type: "application/pdf",
    }),
  },
);

const uploadBody = new FormData();
uploadBody.append("cacheControl", "3600");
uploadBody.append("", new File([pdf], "source.pdf", { type: "application/pdf" }));
const uploaded = await fetch(prepared.upload.signed_url, {
  method: "PUT",
  headers: { "x-upsert": "false" },
  body: uploadBody,
  redirect: "error",
});
assert(uploaded.ok, `signed PDF upload failed (${uploaded.status})`);

await app("/api/assets/upload", {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ action: "complete", asset_id: prepared.asset.id }),
});

const asset = await waitFor(
  "PDF ingestion",
  async () => {
    const rows = await service<Array<{ active_revision_id: string | null; status: string }>>(
      `assets?id=eq.${prepared.asset.id}&select=status,active_revision_id`,
    );
    return rows[0]?.status === "ready" && rows[0].active_revision_id ? rows[0] : null;
  },
  () => { runWorker("pdf"); },
);

const enqueued = await service<number>("rpc/enqueue_retrieval_index_jobs", {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({
    p_embedding_model: required("EMBEDDING_MODEL"),
    p_embedding_version: required("EMBEDDING_MODEL_VERSION"),
    p_limit: 100,
  }),
});
assert(enqueued >= 1, "retrieval indexing did not enqueue a job");

await waitFor(
  "retrieval indexing",
  async () => {
    const rows = await service<Array<{ id: string }>>(
      `retrieval_chunks?asset_id=eq.${prepared.asset.id}&revision_id=eq.${asset.active_revision_id}&select=id&limit=1`,
    );
    return rows[0] ?? null;
  },
  () => { runWorker("retrieval"); },
);

const requestKey = crypto.randomUUID();
const started = await app<{ id: string; jobId: string; status: string }>("/api/research", {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({
    question: required("STAGING_RESEARCH_QUESTION"),
    request_key: requestKey,
    scope_asset_ids: [prepared.asset.id],
  }),
});

const completed = await waitFor(
  "R2 research",
  async () => {
    const run = await app<{
      id: string;
      status: string;
      retrievalStatus: string | null;
      note: { body: string; publicationState: string } | null;
      citations: Array<{ quote: string; validityStatus: string; sourceUrl?: string }>;
      errorCode: string | null;
    }>(`/api/research?id=${started.id}`);
    assert.notEqual(run.status, "failed", `research failed: ${run.errorCode}`);
    return run.status === "draft_ready" ? run : null;
  },
  () => { runWorker("research"); },
);

assert.equal(completed.retrievalStatus, "ready");
assert(completed.note, "Research Draft is missing");
assert(completed.note.body.trim(), "Research Draft body is empty");
assert.equal(completed.note.publicationState, "draft");
assert(completed.citations.length > 0, "Research Draft has no citations");
for (const citation of completed.citations) {
  assert(citation.quote.trim(), "citation quote is empty");
  assert.equal(citation.validityStatus, "valid");
  assert(citation.sourceUrl, "citation has no resolvable source URL");
}

console.log(JSON.stringify({
  status: "PASS",
  assetId: prepared.asset.id,
  revisionId: asset.active_revision_id,
  researchRunId: started.id,
  citationCount: completed.citations.length,
}, null, 2));
