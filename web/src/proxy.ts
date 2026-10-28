import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

export async function proxy(request: NextRequest) {
  let response = NextResponse.next({ request });
  const url =
    process.env.SUPABASE_SERVER_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL;
  const publishableKey = process.env.SUPABASE_SERVER_PUBLISHABLE_KEY
    ?? process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

  if (!url || !publishableKey) {
    return NextResponse.json(
      { error: "Missing local Supabase public configuration" },
      { status: 500 },
    );
  }

  const supabase = createServerClient(url, publishableKey, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (cookiesToSet, headersToSet) => {
        cookiesToSet.forEach(({ name, value }) =>
          request.cookies.set(name, value),
        );
        response = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) =>
          response.cookies.set(name, value, options),
        );
        Object.entries(headersToSet).forEach(([name, value]) =>
          response.headers.set(name, value),
        );
      },
    },
  });

  const { data, error } = await supabase.auth.getClaims();
  if (error || !data?.claims?.sub) {
    const loginUrl = new URL("/auth/login", request.url);
    const redirect = NextResponse.redirect(loginUrl);
    response.cookies.getAll().forEach((cookie) => redirect.cookies.set(cookie));
    ["cache-control", "expires", "pragma"].forEach((name) => {
      const value = response.headers.get(name);
      if (value) redirect.headers.set(name, value);
    });
    return redirect;
  }

  return response;
}

export const config = {
  matcher: ["/", "/library/:path*", "/explore/:path*", "/ask/:path*", "/protected/:path*", "/upload/:path*", "/inbox/:path*", "/research/:path*"],
};
