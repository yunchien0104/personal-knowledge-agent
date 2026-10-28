import { NextResponse, type NextRequest } from "next/server";

import { createRouteHandlerSupabaseClient } from "@/lib/supabase";

export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  const requestedNext = request.nextUrl.searchParams.get("next") ?? "/";
  const next = requestedNext.startsWith("/") && !requestedNext.startsWith("//") ? requestedNext : "/";
  if (!code) return NextResponse.redirect(new URL("/auth/login?error=驗證連結無效。", request.url));
  const { applyAuthState, supabase } = createRouteHandlerSupabaseClient(request);
  const { error } = await supabase.auth.exchangeCodeForSession(code);
  if (error) return applyAuthState(NextResponse.redirect(new URL("/auth/login?error=驗證連結已失效，請重新操作。", request.url)));
  return applyAuthState(NextResponse.redirect(new URL(next, request.url)));
}
