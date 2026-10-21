import assert from "node:assert/strict";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { once } from "node:events";
import net from "node:net";
import test, { after, before } from "node:test";

type LocalSupabase = {
  API_URL: string;
  PUBLISHABLE_KEY: string;
  SECRET_KEY: string;
};

type TestUser = {
  email: string;
  id: string;
  jar: CookieJar;
  password: string;
  token: string;
};

type ApiEnvelope<T> = {
  data: T;
  error?: { code: string; message: string };
  meta?: { limit: number; offset: number; total: number };
};

type Asset = { id: string; user_id: string; source_external_id: string | null };
type Note = { id: string; user_id: string; title: string };

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
let supabase: LocalSupabase;
let userA: TestUser;
let userB: TestUser;
let noteA: Note;
let noteB: Note;
const createdAssets: Array<{ id: string; owner: "a" | "b" }> = [];
const createdAuthUserIds: string[] = [];

function localConfiguration(): LocalSupabase {
  const result = spawnSync("supabase", ["status", "-o", "json"], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  const configuration = JSON.parse(result.stdout) as LocalSupabase;
  const url = new URL(configuration.API_URL);
  assert.equal(url.protocol, "http:", "integration tests only allow local HTTP Supabase");
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
  const email = `product-api-${label}-${crypto.randomUUID()}@example.test`;
  const password = `T3b-${crypto.randomUUID()}!`;
  const jar = new CookieJar();
  const admin = await fetch(`${supabase.API_URL}/auth/v1/admin/users`, {
    method: "POST",
    headers: {
      apikey: supabase.SECRET_KEY,
      authorization: `Bearer ${supabase.SECRET_KEY}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({ email, password, email_confirm: true }),
  });
  assert.equal(admin.ok, true, "local Auth fixture creation failed");
  const id = (await admin.json() as { id?: string }).id;
  assert(id, "local Auth fixture id missing");
  createdAuthUserIds.push(id);

  const body = new URLSearchParams({ email, password });
  const loginResponse = await appRequest(
    "/auth/actions/sign-in",
    { method: "POST", body },
    jar,
  );
  assert.equal(loginResponse.status, 303);
  assert.equal(new URL(loginResponse.headers.get("location")!).pathname, "/protected");

  const protectedPage = await appRequest("/protected", {}, jar);
  assert.equal(protectedPage.status, 200);
  const html = await protectedPage.text();
  const renderedId = html.match(/Authenticated user: <!-- -->([0-9a-f-]{36})/)?.[1];
  assert.equal(renderedId, id, "server session did not match the Auth fixture");

  const login = await fetch(`${supabase.API_URL}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { apikey: supabase.PUBLISHABLE_KEY, "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  assert.equal(login.ok, true, "ordinary user token login failed");
  const token = (await login.json() as { access_token?: string }).access_token;
  assert(token, "ordinary user token missing");
  return { email, id, jar, password, token };
}

async function userRest<T>(user: TestUser, path: string, init: RequestInit) {
  const headers = new Headers(init.headers);
  headers.set("apikey", supabase.PUBLISHABLE_KEY);
  headers.set("authorization", `Bearer ${user.token}`);
  const response = await fetch(`${supabase.API_URL}/rest/v1/${path}`, { ...init, headers });
  return { response, data: response.status === 204 ? null : await response.json() as T };
}

async function createNote(user: TestUser, title: string) {
  const { response, data } = await userRest<Note[]>(user, "notes", {
    method: "POST",
    headers: { "content-type": "application/json", prefer: "return=representation" },
    body: JSON.stringify({ user_id: user.id, note_type: "manual_note", title }),
  });
  assert.equal(response.status, 201, "note fixture creation through user RLS failed");
  assert(data, "note fixture response was empty");
  assert.equal(data.length, 1);
  return data[0];
}

async function deleteRow(user: TestUser, table: string, id: string) {
  const { response } = await userRest(user, `${table}?id=eq.${id}`, { method: "DELETE" });
  assert.equal(response.status, 204, `${table} fixture cleanup failed for ${id}`);
}

async function deleteAuthUser(id: string) {
  const response = await fetch(`${supabase.API_URL}/auth/v1/admin/users/${id}`, {
    method: "DELETE",
    headers: {
      apikey: supabase.SECRET_KEY,
      authorization: `Bearer ${supabase.SECRET_KEY}`,
    },
  });
  assert.equal(response.ok, true, `Auth fixture cleanup failed for ${id}`);
}

async function postAsset(user: TestUser, body: unknown) {
  return appRequest(
    "/api/assets",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
    user.jar,
  );
}

before(async () => {
  supabase = localConfiguration();
  const port = await unusedPort();
  baseUrl = `http://127.0.0.1:${port}`;
  server = spawn("npm", ["start", "--", "--hostname", "127.0.0.1", "--port", String(port)], {
    env: {
      ...process.env,
      SUPABASE_SERVER_URL: supabase.API_URL,
      SUPABASE_SERVER_PUBLISHABLE_KEY: supabase.PUBLISHABLE_KEY,
      NEXT_PUBLIC_SUPABASE_URL: supabase.API_URL,
      NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: supabase.PUBLISHABLE_KEY,
    },
    stdio: "ignore",
  });
  await waitForServer();
  userA = await createUser("a");
  userB = await createUser("b");
  noteA = await createNote(userA, "User A private note");
  noteB = await createNote(userB, "User B private note");
});

after(async () => {
  try {
    if (userA && noteA) await deleteRow(userA, "notes", noteA.id);
    if (userB && noteB) await deleteRow(userB, "notes", noteB.id);
    for (const asset of createdAssets) {
      await deleteRow(asset.owner === "a" ? userA : userB, "assets", asset.id);
    }
    for (const id of createdAuthUserIds) await deleteAuthUser(id);
  } finally {
    server?.kill("SIGTERM");
    if (server && server.exitCode === null) await once(server, "exit");
  }
});

test("all product endpoints reject anonymous requests", async () => {
  const id = crypto.randomUUID();
  for (const [path, init] of [
    ["/api/assets", {}],
    ["/api/assets", { method: "POST", body: "{}" }],
    [`/api/assets/${id}`, {}],
    ["/api/notes", {}],
    [`/api/notes/${id}`, {}],
  ] as const) {
    const response = await appRequest(path, init);
    assert.equal(response.status, 401, `${init.method ?? "GET"} ${path}`);
    assert.equal((await response.json() as ApiEnvelope<null>).error?.code, "UNAUTHENTICATED");
  }
});

test("forged user_id is rejected and creates no asset", async () => {
  const forged = await postAsset(userA, {
    source_type: "pdf",
    source_external_id: `forged-${crypto.randomUUID()}`,
    user_id: userB.id,
  });
  assert.equal(forged.status, 400);
  assert.equal((await forged.json() as ApiEnvelope<null>).error?.code, "INVALID_REQUEST");
  const list = await appRequest("/api/assets", {}, userA.jar);
  const payload = await list.json() as ApiEnvelope<Asset[]>;
  assert.equal(payload.meta?.total, 0);
});

test("invalid bodies, ids, and pagination return consistent client errors", async () => {
  const invalidJson = await appRequest("/api/assets", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "{",
  }, userA.jar);
  assert.equal(invalidJson.status, 400);
  assert.equal((await invalidJson.json() as ApiEnvelope<null>).error?.code, "INVALID_JSON");

  assert.equal((await postAsset(userA, { source_type: "unknown" })).status, 400);
  assert.equal((await appRequest("/api/assets?limit=101", {}, userA.jar)).status, 400);
  assert.equal((await appRequest("/api/notes?offset=-1", {}, userA.jar)).status, 400);
  assert.equal((await appRequest("/api/assets/not-a-uuid", {}, userA.jar)).status, 400);
  assert.equal((await appRequest("/api/notes/not-a-uuid", {}, userA.jar)).status, 400);
});

test("authenticated users create, read, and paginate only their assets", async () => {
  for (const [owner, user, externalId] of [
    ["a", userA, `a-1-${crypto.randomUUID()}`],
    ["a", userA, `a-2-${crypto.randomUUID()}`],
    ["b", userB, `b-1-${crypto.randomUUID()}`],
  ] as const) {
    const response = await postAsset(user, {
      source_type: "pdf",
      source_url: `https://example.test/${externalId}.pdf`,
      source_external_id: externalId,
    });
    assert.equal(response.status, 201);
    const asset = (await response.json() as ApiEnvelope<Asset>).data;
    assert.equal(asset.user_id, user.id);
    createdAssets.push({ id: asset.id, owner });
  }

  const page = await appRequest("/api/assets?limit=1&offset=0", {}, userA.jar);
  const payload = await page.json() as ApiEnvelope<Asset[]>;
  assert.equal(page.status, 200);
  assert.equal(payload.data.length, 1);
  assert.equal(payload.meta?.total, 2);
  assert(payload.data.every((asset) => asset.user_id === userA.id));

  const own = await appRequest(`/api/assets/${createdAssets[0].id}`, {}, userA.jar);
  assert.equal(own.status, 200);
  assert.equal((await own.json() as ApiEnvelope<Asset>).data.user_id, userA.id);
});

test("RLS hides assets and notes across accounts", async () => {
  const assetA = createdAssets.find((asset) => asset.owner === "a")!;
  const assetB = createdAssets.find((asset) => asset.owner === "b")!;
  assert.equal((await appRequest(`/api/assets/${assetB.id}`, {}, userA.jar)).status, 404);
  assert.equal((await appRequest(`/api/assets/${assetA.id}`, {}, userB.jar)).status, 404);
  assert.equal((await appRequest(`/api/notes/${noteB.id}`, {}, userA.jar)).status, 404);
  assert.equal((await appRequest(`/api/notes/${noteA.id}`, {}, userB.jar)).status, 404);

  const notes = await appRequest("/api/notes", {}, userA.jar);
  const payload = await notes.json() as ApiEnvelope<Note[]>;
  assert.equal(notes.status, 200);
  assert.deepEqual(payload.data.map((note) => note.id), [noteA.id]);
  assert(payload.data.every((note) => note.user_id === userA.id));
});
