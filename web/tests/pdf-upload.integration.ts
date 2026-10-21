import assert from "node:assert/strict";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import net from "node:net";
import test, { after, before } from "node:test";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

type LocalSupabase = { API_URL: string; PUBLISHABLE_KEY: string; SECRET_KEY: string };
type TestUser = { id: string; jar: CookieJar; token: string; storage: SupabaseClient };
type Prepared = { asset: { id: string }; upload: { signed_url: string } };
type Envelope<T> = { data: T | null; error?: { code: string } };

class CookieJar {
  readonly values = new Map<string, string>();

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

let server: ChildProcess | undefined;
let baseUrl = "";
let local: LocalSupabase;
let userA: TestUser;
let userB: TestUser;
let savedAsset: Prepared | undefined;
const authUserIds: string[] = [];

function localConfiguration() {
  const result = spawnSync("supabase", ["status", "-o", "json"], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  const configuration = JSON.parse(result.stdout) as LocalSupabase;
  const url = new URL(configuration.API_URL);
  assert.equal(url.protocol, "http:");
  assert(["127.0.0.1", "localhost", "::1"].includes(url.hostname), "Supabase must be local");
  return configuration;
}

async function unusedPort() {
  const socket = net.createServer();
  socket.listen(0, "127.0.0.1");
  await once(socket, "listening");
  const address = socket.address();
  assert(address && typeof address === "object");
  await new Promise<void>((resolve) => socket.close(() => resolve()));
  return address.port;
}

async function appRequest(path: string, init: RequestInit = {}, jar = new CookieJar()) {
  const headers = new Headers(init.headers);
  if (jar.header()) headers.set("cookie", jar.header());
  const response = await fetch(`${baseUrl}${path}`, { ...init, headers, redirect: "manual" });
  jar.capture(response);
  return response;
}

async function waitForServer() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      if ((await fetch(baseUrl)).ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Next.js test server did not start");
}

async function createUser(label: string): Promise<TestUser> {
  const email = `pdf-upload-${label}-${crypto.randomUUID()}@example.test`;
  const password = `Pdf-${crypto.randomUUID()}!`;
  const admin = await fetch(`${local.API_URL}/auth/v1/admin/users`, {
    method: "POST",
    headers: {
      apikey: local.SECRET_KEY,
      authorization: `Bearer ${local.SECRET_KEY}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({ email, password, email_confirm: true }),
  });
  assert(admin.ok, "local Auth fixture creation failed");
  const id = (await admin.json() as { id?: string }).id;
  assert(id);
  authUserIds.push(id);

  const jar = new CookieJar();
  const signIn = await appRequest("/auth/actions/sign-in", {
    method: "POST",
    body: new URLSearchParams({ email, password }),
  }, jar);
  assert.equal(new URL(signIn.headers.get("location")!).pathname, "/protected");

  const login = await fetch(`${local.API_URL}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { apikey: local.PUBLISHABLE_KEY, "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  assert(login.ok);
  const token = (await login.json() as { access_token?: string }).access_token;
  assert(token);
  const storage = createClient(local.API_URL, local.PUBLISHABLE_KEY, {
    auth: { autoRefreshToken: false, persistSession: false },
    global: { headers: { authorization: `Bearer ${token}` } },
  });
  return { id, jar, token, storage };
}

async function uploadRequest<T>(user: TestUser, body: unknown) {
  const response = await appRequest("/api/assets/upload", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  }, user.jar);
  return { response, payload: await response.json() as Envelope<T> };
}

async function prepare(user: TestUser, fileName: string, bytes: Uint8Array) {
  const result = await uploadRequest<Prepared>(user, {
    action: "prepare",
    file_name: fileName,
    file_size: bytes.byteLength,
    mime_type: "application/pdf",
  });
  assert.equal(result.response.status, 201);
  assert(result.payload.data);
  return result.payload.data;
}

async function signedUpload(prepared: Prepared, bytes: Uint8Array) {
  const file = new File([new Uint8Array(bytes).buffer], "source.pdf", {
    type: "application/pdf",
  });
  const body = new FormData();
  body.append("cacheControl", "3600");
  body.append("", file);
  const response = await fetch(prepared.upload.signed_url, {
    method: "POST",
    headers: { "x-upsert": "false" },
    body,
    redirect: "error",
  });
  assert(response.ok);
}

function storagePath(user: TestUser, prepared: Prepared) {
  return `${user.id}/${prepared.asset.id}/original/source.pdf`;
}

async function deleteAuthUser(id: string) {
  const response = await fetch(`${local.API_URL}/auth/v1/admin/users/${id}`, {
    method: "DELETE",
    headers: { apikey: local.SECRET_KEY, authorization: `Bearer ${local.SECRET_KEY}` },
  });
  assert(response.ok, `Auth cleanup failed for ${id}`);
}

before(async () => {
  local = localConfiguration();
  const port = await unusedPort();
  baseUrl = `http://127.0.0.1:${port}`;
  server = spawn("npm", ["start", "--", "--hostname", "127.0.0.1", "--port", String(port)], {
    env: {
      ...process.env,
      SUPABASE_SERVER_URL: local.API_URL,
      SUPABASE_SERVER_PUBLISHABLE_KEY: local.PUBLISHABLE_KEY,
      NEXT_PUBLIC_SUPABASE_URL: local.API_URL,
      NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: local.PUBLISHABLE_KEY,
    },
    stdio: "ignore",
  });
  await waitForServer();
  userA = await createUser("a");
  userB = await createUser("b");
});

after(async () => {
  const cleanupErrors: unknown[] = [];
  try {
    if (savedAsset) {
      const removed = await userA.storage.storage
        .from("asset-private")
        .remove([storagePath(userA, savedAsset)]);
      if (removed.error) cleanupErrors.push(removed.error);
      const deleted = await fetch(`${local.API_URL}/rest/v1/assets?id=eq.${savedAsset.asset.id}`, {
        method: "DELETE",
        headers: { apikey: local.PUBLISHABLE_KEY, authorization: `Bearer ${userA.token}` },
      });
      if (!deleted.ok) cleanupErrors.push(new Error("Asset cleanup failed"));
    }
    for (const id of authUserIds) {
      try {
        await deleteAuthUser(id);
      } catch (error) {
        cleanupErrors.push(error);
      }
    }
  } finally {
    server?.kill("SIGTERM");
    if (server && server.exitCode === null) await once(server, "exit");
  }
  if (cleanupErrors.length) throw new AggregateError(cleanupErrors, "PDF test cleanup failed");
});

test("anonymous users cannot access upload UI, Inbox, or upload API", async () => {
  for (const path of ["/upload", "/inbox"]) {
    const response = await appRequest(path);
    assert.equal(response.status, 307);
    assert.equal(new URL(response.headers.get("location")!, baseUrl).pathname, "/auth/login");
  }
  const api = await appRequest("/api/assets/upload", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ action: "prepare", file_name: "a.pdf", file_size: 10, mime_type: "application/pdf" }),
  });
  assert.equal(api.status, 401);
});

test("server validation rejects unsafe PDF metadata before creating an Asset", async () => {
  for (const body of [
    { action: "prepare", file_name: "a.txt", file_size: 10, mime_type: "application/pdf" },
    { action: "prepare", file_name: "a.pdf", file_size: 10, mime_type: "text/plain" },
    { action: "prepare", file_name: "a.pdf", file_size: 10 * 1024 * 1024 + 1, mime_type: "application/pdf" },
  ]) {
    assert.equal((await uploadRequest(userA, body)).response.status, 400);
  }
  const assets = await appRequest("/api/assets", {}, userA.jar);
  assert.equal((await assets.json() as { meta: { total: number } }).meta.total, 0);
});

test("a valid PDF is stored, queued once, and rendered in the owner's Inbox", async () => {
  const bytes = new TextEncoder().encode("%PDF-1.4\n1 0 obj\n%%EOF\n");
  savedAsset = await prepare(userA, "my-private-file.pdf", bytes);
  await signedUpload(savedAsset, bytes);
  const completed = await uploadRequest<{
    stored: { size: number };
    job: { id: string; status: string; idempotencyKey: string };
  }>(userA, {
    action: "complete",
    asset_id: savedAsset.asset.id,
  });
  assert.equal(completed.response.status, 200);
  assert.equal(completed.payload.data?.stored.size, bytes.byteLength);
  assert.equal(completed.payload.data?.job.status, "queued");
  assert.match(completed.payload.data?.job.idempotencyKey ?? "", /^ingest_pdf:/);

  const replayed = await uploadRequest<{ job: { id: string } }>(userA, {
    action: "complete",
    asset_id: savedAsset.asset.id,
  });
  assert.equal(replayed.response.status, 200);
  assert.equal(replayed.payload.data?.job.id, completed.payload.data?.job.id);

  const jobs = await fetch(
    `${local.API_URL}/rest/v1/jobs?asset_id=eq.${savedAsset.asset.id}&select=id,status,payload_json`,
    { headers: { apikey: local.PUBLISHABLE_KEY, authorization: `Bearer ${userA.token}` } },
  );
  assert.equal(jobs.status, 200);
  const rows = await jobs.json() as Array<{ id: string; status: string; payload_json: unknown }>;
  assert.equal(rows.length, 1);
  assert.equal(rows[0].id, completed.payload.data?.job.id);

  const inbox = await appRequest("/inbox", {}, userA.jar);
  const html = await inbox.text();
  assert.equal(inbox.status, 200);
  assert.match(html, /my-private-file\.pdf/);
  assert.match(html, /檔案已保存/);
  assert.match(html, /下載確認/);

  const downloaded = await userA.storage.storage
    .from("asset-private")
    .download(storagePath(userA, savedAsset));
  assert.equal(downloaded.error, null);
  assert.deepEqual(new Uint8Array(await downloaded.data!.arrayBuffer()), bytes);

  const anonymous = createClient(local.API_URL, local.PUBLISHABLE_KEY, {
    auth: { autoRefreshToken: false, persistSession: false },
  });
  assert((await anonymous.storage.from("asset-private").download(storagePath(userA, savedAsset))).error);
});

test("another user cannot read, complete, clean, or see the PDF", async () => {
  assert(savedAsset);
  assert((await userB.storage.storage.from("asset-private").download(storagePath(userA, savedAsset))).error);
  assert.equal((await uploadRequest(userB, {
    action: "complete",
    asset_id: savedAsset.asset.id,
  })).response.status, 404);
  assert.equal((await appRequest(
    `/api/assets/upload?asset_id=${savedAsset.asset.id}`,
    { method: "DELETE" },
    userB.jar,
  )).status, 404);
  const inbox = await appRequest("/inbox", {}, userB.jar);
  assert.doesNotMatch(await inbox.text(), /my-private-file\.pdf/);
  const jobs = await fetch(
    `${local.API_URL}/rest/v1/jobs?asset_id=eq.${savedAsset.asset.id}&select=id`,
    { headers: { apikey: local.PUBLISHABLE_KEY, authorization: `Bearer ${userB.token}` } },
  );
  assert.equal(jobs.status, 200);
  assert.deepEqual(await jobs.json(), []);
});

test("invalid and abandoned uploads are compensated without orphan Assets", async () => {
  const invalidBytes = new TextEncoder().encode("not a pdf");
  const invalid = await prepare(userA, "invalid.pdf", invalidBytes);
  await signedUpload(invalid, invalidBytes);
  const rejected = await uploadRequest(userA, { action: "complete", asset_id: invalid.asset.id });
  assert.equal(rejected.response.status, 400);
  assert.equal(rejected.payload.error?.code, "INVALID_PDF");
  assert.equal((await appRequest(`/api/assets/${invalid.asset.id}`, {}, userA.jar)).status, 404);
  assert((await userA.storage.storage.from("asset-private").download(storagePath(userA, invalid))).error);

  const abandonedBytes = new TextEncoder().encode("%PDF-abandoned");
  const abandoned = await prepare(userA, "abandoned.pdf", abandonedBytes);
  const cleanup = await appRequest(
    `/api/assets/upload?asset_id=${abandoned.asset.id}`,
    { method: "DELETE" },
    userA.jar,
  );
  assert.equal(cleanup.status, 200);
  assert.equal((await appRequest(`/api/assets/${abandoned.asset.id}`, {}, userA.jar)).status, 404);
});
