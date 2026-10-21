import { NextResponse, type NextRequest } from "next/server";

import { createServerSupabaseClient } from "@/lib/supabase";

const actions = new Set(["sign-in", "sign-up", "sign-out", "forgot-password", "reset-password", "resend-verification"]);

function authRedirect(request: NextRequest, mode: string, values: Record<string, string>) {
  const url = new URL(`/auth/${mode}`, request.url);
  Object.entries(values).forEach(([key, value]) => url.searchParams.set(key, value));
  return NextResponse.redirect(url, 303);
}

export async function POST(request: NextRequest, { params }: { params: Promise<{ action: string }> }) {
  const { action } = await params;
  if (!actions.has(action)) return NextResponse.json({ error: "Unknown auth action" }, { status: 404 });
  const supabase = await createServerSupabaseClient();

  if (action === "sign-out") {
    await supabase.auth.signOut();
    return NextResponse.redirect(new URL("/auth/login", request.url), 303);
  }

  const form = await request.formData();
  const email = form.get("email");
  const password = form.get("password");

  if (action === "reset-password") {
    if (typeof password !== "string" || password.length < 8) return authRedirect(request, "reset", { error: "新密碼至少需要 8 個字元。" });
    const { error } = await supabase.auth.updateUser({ password });
    if (error) return authRedirect(request, "reset", { error: "重設連結已失效，請重新申請。" });
    return authRedirect(request, "login", { status: "密碼已更新，請使用新密碼登入。" });
  }

  if (typeof email !== "string" || !email.includes("@")) return authRedirect(request, action === "sign-up" ? "register" : action === "forgot-password" ? "forgot" : "login", { error: "請輸入有效的 Email。" });
  const callback = `${request.nextUrl.origin}/auth/callback`;

  if (action === "forgot-password") {
    const { error } = await supabase.auth.resetPasswordForEmail(email, { redirectTo: `${callback}?next=/auth/reset` });
    if (error) return authRedirect(request, "forgot", { error: "目前無法寄送重設信，請稍後再試。", email });
    return authRedirect(request, "forgot", { status: "重設連結已寄出，請檢查信箱。", email });
  }

  if (action === "resend-verification") {
    const { error } = await supabase.auth.resend({ type: "signup", email, options: { emailRedirectTo: `${callback}?next=/` } });
    if (error) return authRedirect(request, "verify", { error: "目前無法重新寄送驗證信。", email });
    return authRedirect(request, "verify", { status: "驗證信已重新寄出。", email });
  }

  if (typeof password !== "string" || password.length < 6) return authRedirect(request, action === "sign-up" ? "register" : "login", { error: "請輸入有效的 Email 與密碼。", email });
  if (action === "sign-in") {
    const result = await supabase.auth.signInWithPassword({ email, password });
    if (result.error) return authRedirect(request, "login", { error: "Email 或密碼不正確。請檢查後再試一次。", email });
    return NextResponse.redirect(new URL("/", request.url), 303);
  }

  const result = await supabase.auth.signUp({ email, password, options: { emailRedirectTo: `${callback}?next=/` } });
  if (result.error) return authRedirect(request, "register", { error: result.error.message, email });
  if (!result.data.session) return authRedirect(request, "verify", { email });
  return NextResponse.redirect(new URL("/", request.url), 303);
}
