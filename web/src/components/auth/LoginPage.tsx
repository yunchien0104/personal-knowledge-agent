"use client";

import { ArrowLeft, Check, Eye, EyeOff, KeyRound, Mail, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";

export type AuthView = "login" | "register" | "verify" | "forgot" | "reset";

const inputClass = "mt-2 w-full rounded-lg border border-white/15 bg-white/[0.06] px-4 py-3 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-white/45 focus:bg-white/[0.09]";
const primaryClass = "mt-6 flex w-full items-center justify-center rounded-lg bg-white px-4 py-3 text-sm font-semibold text-black transition hover:bg-white/[0.88] disabled:cursor-not-allowed disabled:opacity-50";

export function LoginPage({ initialView = "login", error, status, initialEmail = "" }: { initialView?: AuthView; error?: string; status?: string; initialEmail?: string }) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [localError, setLocalError] = useState("");

  function validateMatchingPasswords(event: FormEvent<HTMLFormElement>) {
    setLocalError("");
    if (password.length < 8 || confirmation !== password) {
      event.preventDefault();
      setLocalError(password.length < 8 ? "密碼至少需要 8 個字元。" : "兩次輸入的密碼不一致。");
    }
  }

  const heading = initialView === "register" ? "建立帳號" : initialView === "forgot" ? "忘記密碼" : initialView === "reset" ? "重設密碼" : null;
  const description = initialView === "register" ? "建立一個只屬於你的知識工作區。" : initialView === "forgot" ? "輸入帳號 Email，我們會寄送重設連結。" : initialView === "reset" ? "設定一組至少 8 個字元的新密碼。" : null;

  return (
    <main className="flex min-h-screen items-center justify-center bg-[#050505] px-5 py-10 text-white sm:px-10">
      <div className="w-full max-w-md">
        {initialView === "verify" ? (
          <div className="animate-page">
            <div className="grid h-12 w-12 place-items-center rounded-full border border-white/15 bg-white/[0.06]"><Mail className="h-5 w-5" /></div>
            <p className="mt-8 text-xs font-medium uppercase tracking-[0.2em] text-white/40">Email 驗證</p>
            <h1 className="mt-3 text-3xl font-semibold tracking-[-0.035em]">請驗證你的 Email</h1>
            <p className="mt-4 text-sm leading-7 text-white/50">驗證連結已寄到 <strong className="text-white">{initialEmail || "你的信箱"}</strong>。完成驗證後即可登入。</p>
            {status ? <p role="status" className="mt-5 rounded-lg border border-emerald-400/25 bg-emerald-400/10 px-4 py-3 text-sm text-emerald-200">{status}</p> : null}
            {error ? <p role="alert" className="mt-5 rounded-lg border border-red-400/30 bg-red-400/10 px-4 py-3 text-sm text-red-200">{error}</p> : null}
            <form method="post" action="/auth/actions/resend-verification"><input type="hidden" name="email" value={initialEmail} /><button type="submit" disabled={!initialEmail} className={primaryClass}>重新寄送驗證信</button></form>
            <Link href="/auth/login" className="mt-4 flex w-full items-center justify-center gap-2 py-2 text-sm text-white/55 hover:text-white"><ArrowLeft className="h-4 w-4" />返回登入</Link>
          </div>
        ) : (
          <div className="animate-page">
            {initialView === "login" ? <h1 className="whitespace-nowrap text-center text-3xl font-semibold tracking-[-0.035em]">“Your personal knowledge agent”</h1> : <><p className="text-xs font-medium uppercase tracking-[0.2em] text-white/40">Personal Brain</p><h1 className="mt-3 text-3xl font-semibold tracking-[-0.035em]">{heading}</h1><p className="mt-3 text-sm leading-6 text-white/45">{description}</p></>}
            {(localError || error) ? <p role="alert" className="mt-6 rounded-lg border border-red-400/30 bg-red-400/10 px-4 py-3 text-sm leading-6 text-red-200">{localError || error}</p> : null}
            {status ? <p role="status" className="mt-6 flex items-start gap-3 rounded-lg border border-emerald-400/25 bg-emerald-400/10 px-4 py-3 text-sm text-emerald-200"><Check className="mt-0.5 h-4 w-4 shrink-0" />{status}</p> : null}

            {initialView === "login" ? <form method="post" action="/auth/actions/sign-in" className="mt-7">
              <label className="block text-xs font-medium text-white/65">Email<input name="email" required type="email" autoComplete="email" defaultValue={initialEmail} className={inputClass} placeholder="you@example.com" /></label>
              <label className="mt-5 block text-xs font-medium text-white/65">密碼<span className="relative block"><input name="password" required type={showPassword ? "text" : "password"} autoComplete="current-password" minLength={6} value={password} onChange={(event) => setPassword(event.target.value)} className={`${inputClass} pr-12`} placeholder="輸入密碼" /><button type="button" aria-label={showPassword ? "隱藏密碼" : "顯示密碼"} onClick={() => setShowPassword((value) => !value)} className="absolute right-3 top-[18px] p-2 text-white/35 hover:text-white">{showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</button></span></label>
              <div className="mt-4 flex justify-end"><Link href="/auth/forgot" className="text-xs text-white/55 hover:text-white">忘記密碼？</Link></div>
              <button type="submit" className={primaryClass}>登入</button>
              <p className="mt-5 text-center text-xs text-white/40">還沒有帳號？ <Link href="/auth/register" className="font-semibold text-white hover:underline">立即註冊</Link></p>
            </form> : null}

            {initialView === "register" ? <form method="post" action="/auth/actions/sign-up" onSubmit={validateMatchingPasswords} className="mt-7">
              <label className="block text-xs font-medium text-white/65">Email<input name="email" required type="email" autoComplete="email" defaultValue={initialEmail} className={inputClass} placeholder="you@example.com" /></label>
              <label className="mt-5 block text-xs font-medium text-white/65">密碼<input name="password" required type="password" autoComplete="new-password" minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} className={inputClass} placeholder="至少 8 個字元" /></label>
              <label className="mt-5 block text-xs font-medium text-white/65">確認密碼<input required type="password" autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} className={inputClass} placeholder="再次輸入密碼" /></label>
              <button type="submit" className={primaryClass}>建立帳號</button><Link href="/auth/login" className="mt-4 flex w-full items-center justify-center gap-2 py-2 text-sm text-white/55 hover:text-white"><ArrowLeft className="h-4 w-4" />返回登入</Link>
            </form> : null}

            {initialView === "forgot" ? <form method="post" action="/auth/actions/forgot-password" className="mt-7">
              <label className="block text-xs font-medium text-white/65">帳號 Email<input name="email" required type="email" autoComplete="email" defaultValue={initialEmail} className={inputClass} placeholder="you@example.com" /></label>
              <button type="submit" className={primaryClass}>寄送重設連結</button><Link href="/auth/login" className="mt-4 flex w-full items-center justify-center gap-2 py-2 text-sm text-white/55 hover:text-white"><ArrowLeft className="h-4 w-4" />返回登入</Link>
            </form> : null}

            {initialView === "reset" ? <form method="post" action="/auth/actions/reset-password" onSubmit={validateMatchingPasswords} className="mt-7">
              <div className="mb-6 flex items-center gap-3 rounded-lg border border-white/10 bg-white/[0.04] px-4 py-3 text-xs text-white/45"><KeyRound className="h-4 w-4" />設定新的登入密碼</div>
              <label className="block text-xs font-medium text-white/65">新密碼<input name="password" required type="password" autoComplete="new-password" minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} className={inputClass} placeholder="至少 8 個字元" /></label>
              <label className="mt-5 block text-xs font-medium text-white/65">確認新密碼<input required type="password" autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} className={inputClass} placeholder="再次輸入新密碼" /></label>
              <button type="submit" className={primaryClass}>更新密碼</button>
            </form> : null}

            <div className="mt-10 flex items-center justify-center gap-2 text-[11px] text-white/30"><ShieldCheck className="h-3.5 w-3.5" />由 Supabase 安全驗證帳號</div>
          </div>
        )}
      </div>
    </main>
  );
}
