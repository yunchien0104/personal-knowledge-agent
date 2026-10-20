import { NextResponse, type NextRequest } from "next/server";

import { createServerSupabaseClient } from "@/lib/supabase";

const actions = new Set(["sign-in", "sign-up", "sign-out"]);

function authRedirect(request: NextRequest, mode: "login" | "register", error: string) {
  const url = new URL(`/auth/${mode}`, request.url);
  url.searchParams.set("error", error);
  return NextResponse.redirect(url, 303);
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ action: string }> },
) {
  const { action } = await params;
  if (!actions.has(action)) {
    return NextResponse.json({ error: "Unknown auth action" }, { status: 404 });
  }

  const supabase = await createServerSupabaseClient();
  if (action === "sign-out") {
    await supabase.auth.signOut();
    return NextResponse.redirect(new URL("/auth/login", request.url), 303);
  }

  const form = await request.formData();
  const email = form.get("email");
  const password = form.get("password");
  const mode = action === "sign-in" ? "login" : "register";
  if (typeof email !== "string" || typeof password !== "string" || password.length < 6) {
    return authRedirect(request, mode, "Valid email and password are required");
  }

  const credentials = { email, password };
  const result = action === "sign-in"
    ? await supabase.auth.signInWithPassword(credentials)
    : await supabase.auth.signUp(credentials);

  if (result.error) return authRedirect(request, mode, result.error.message);
  if (!result.data.session) {
    return authRedirect(request, "login", "Check your email before signing in");
  }

  return NextResponse.redirect(new URL("/protected", request.url), 303);
}
