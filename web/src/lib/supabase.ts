import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import type { NextRequest, NextResponse } from "next/server";

type SupabaseCookie = {
  name: string;
  value: string;
  options: Parameters<NextResponse["cookies"]["set"]>[2];
};

function serverConfiguration() {
  const url =
    process.env.SUPABASE_SERVER_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL;
  const publishableKey =
    process.env.SUPABASE_SERVER_PUBLISHABLE_KEY
    ?? process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !publishableKey) {
    throw new Error("Missing server Supabase public configuration");
  }
  return { publishableKey, url };
}

export async function createServerSupabaseClient() {
  const { publishableKey, url } = serverConfiguration();
  const cookieStore = await cookies();

  return createServerClient(url, publishableKey, {
    cookies: {
      getAll: () => cookieStore.getAll(),
      setAll: (cookiesToSet) => {
        try {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options),
          );
        } catch {
          // Server Components cannot write cookies; proxy.ts refreshes them.
        }
      },
    },
  });
}

export function createRouteHandlerSupabaseClient(request: NextRequest) {
  const { publishableKey, url } = serverConfiguration();
  const pendingCookies = new Map<string, SupabaseCookie>();
  const pendingHeaders = new Headers();

  const supabase = createServerClient(url, publishableKey, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (cookiesToSet, headersToSet) => {
        cookiesToSet.forEach((cookie) => {
          request.cookies.set(cookie.name, cookie.value);
          pendingCookies.set(cookie.name, cookie);
        });
        Object.entries(headersToSet).forEach(([name, value]) =>
          pendingHeaders.set(name, value),
        );
      },
    },
  });

  return {
    supabase,
    applyAuthState<T extends NextResponse>(response: T) {
      pendingCookies.forEach(({ name, value, options }) =>
        response.cookies.set(name, value, options),
      );
      pendingHeaders.forEach((value, name) => response.headers.set(name, value));
      return response;
    },
  };
}
