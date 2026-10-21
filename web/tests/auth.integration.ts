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

let server: ChildProcess;
let baseUrl: string;
let supabase: LocalSupabase;
let email: string;
let password: string;
let userId: string;
const jar = new CookieJar();

function localConfiguration(): LocalSupabase {
  const result = spawnSync("supabase", ["status", "-o", "json"], {
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout) as LocalSupabase;
}

async function unusedPort() {
  const socket = net.createServer();
  socket.listen(0, "127.0.0.1");
  await once(socket, "listening");
  const address = socket.address();
  assert(address && typeof address === "object");
  const port = address.port;
  await new Promise<void>((resolve) => socket.close(() => resolve()));
  return port;
}

async function request(path: string, init: RequestInit = {}, cookies = jar) {
  const headers = new Headers(init.headers);
  if (cookies.header()) headers.set("cookie", cookies.header());
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers,
    redirect: "manual",
  });
  cookies.capture(response);
  return response;
}

async function waitForServer() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(baseUrl);
      if (response.ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Next.js test server did not start");
}

before(async () => {
  supabase = localConfiguration();
  const port = await unusedPort();
  baseUrl = `http://127.0.0.1:${port}`;
  email = `auth-it-${crypto.randomUUID()}@example.test`;
  password = `T3a-${crypto.randomUUID()}!`;
  server = spawn("npm", ["start", "--", "--hostname", "127.0.0.1", "--port", String(port)], {
    env: {
      ...process.env,
      SUPABASE_SERVER_URL: supabase.API_URL,
      SUPABASE_SERVER_PUBLISHABLE_KEY: supabase.PUBLISHABLE_KEY,
      NEXT_PUBLIC_SUPABASE_URL: supabase.API_URL,
      NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: supabase.PUBLISHABLE_KEY,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  await waitForServer();
});

after(async () => {
  if (userId) {
    await fetch(`${supabase.API_URL}/auth/v1/admin/users/${userId}`, {
      method: "DELETE",
      headers: { apikey: supabase.SECRET_KEY, authorization: `Bearer ${supabase.SECRET_KEY}` },
    });
  }
  server?.kill("SIGTERM");
  if (server && server.exitCode === null) await once(server, "exit");
});

test("anonymous users cannot access the protected page", async () => {
  const response = await request("/protected", {}, new CookieJar());
  assert.equal(response.status, 307);
  assert.equal(new URL(response.headers.get("location")!, baseUrl).pathname, "/auth/login");
});

test("registration creates a real session", async () => {
  const body = new URLSearchParams({ email, password });
  const response = await request("/auth/actions/sign-up", { method: "POST", body });
  assert.equal(response.status, 303);
  assert.equal(new URL(response.headers.get("location")!).pathname, "/protected");
  const protectedResponse = await request("/protected");
  assert.equal(protectedResponse.status, 200);
  const html = await protectedResponse.text();
  const match = html.match(/Authenticated user: <!-- -->([0-9a-f-]{36})/);
  assert(match, "protected page did not render the server-verified user id");
  userId = match[1];
  assert.match(html, new RegExp(email));
});

test("the server ignores a user supplied user_id", async () => {
  const forged = crypto.randomUUID();
  const response = await request(`/protected?user_id=${forged}`);
  const html = await response.text();
  assert.equal(response.status, 200);
  const match = html.match(/Authenticated user: <!-- -->([0-9a-f-]{36})/);
  assert.equal(match?.[1], userId);
  assert.notEqual(match?.[1], forged);
});

test("sign out removes access to the protected page", async () => {
  const response = await request("/auth/actions/sign-out", { method: "POST" });
  assert.equal(response.status, 303);
  const protectedResponse = await request("/protected");
  assert.equal(protectedResponse.status, 307);
});

test("password login restores the session", async () => {
  const body = new URLSearchParams({ email, password });
  const response = await request("/auth/actions/sign-in", { method: "POST", body });
  assert.equal(response.status, 303);
  assert.equal((await request("/protected")).status, 200);
});

test("a corrupted session cookie is rejected", async () => {
  const invalid = new CookieJar();
  for (const [name] of jar.values) invalid.values.set(name, "invalid-session");
  const response = await request("/protected", {}, invalid);
  assert.equal(response.status, 307);
  assert.equal(new URL(response.headers.get("location")!, baseUrl).pathname, "/auth/login");
});

test("the temporary Auth user is removed through the local Auth API", async () => {
  const headers = {
    apikey: supabase.SECRET_KEY,
    authorization: `Bearer ${supabase.SECRET_KEY}`,
  };
  const deleted = await fetch(`${supabase.API_URL}/auth/v1/admin/users/${userId}`, {
    method: "DELETE",
    headers,
  });
  assert.equal(deleted.ok, true);
  const missing = await fetch(`${supabase.API_URL}/auth/v1/admin/users/${userId}`, { headers });
  assert.equal(missing.status, 404);
  userId = "";
});
