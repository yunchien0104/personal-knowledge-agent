"use client";

import { ArrowLeft, Bell, BookOpen, FolderKanban, Home, LogOut, Menu, MessageCircleQuestion, Moon, Newspaper, Plus, Search, Settings2, Sun, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type PropsWithChildren } from "react";

import { AddDataModal } from "./AddDataModal";

const nav = [
  { href: "/", label: "首頁", icon: Home },
  { href: "/library", label: "知識庫", icon: BookOpen },
  { href: "/explore", label: "探索精選", icon: Newspaper },
  { href: "/ask", label: "問知識庫", icon: MessageCircleQuestion },
  { href: "/research", label: "研究專案", icon: FolderKanban },
];

type Theme = "dark" | "light";
type ProfileView = "account" | "preferences";

export function AppShell({ children, userEmail }: PropsWithChildren<{ userEmail: string | null }>) {
  const pathname = usePathname();
  const [captureOpen, setCaptureOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [mobileMenu, setMobileMenu] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [profileView, setProfileView] = useState<ProfileView>("account");
  const [theme, setTheme] = useState<Theme>("light");

  useEffect(() => {
    const stored = window.localStorage.getItem("personal-brain-theme") === "dark" ? "dark" : "light";
    setTheme(stored);
    document.documentElement.classList.toggle("dark", stored === "dark");
  }, []);

  function updateTheme(next: Theme) {
    setTheme(next);
    document.documentElement.classList.toggle("dark", next === "dark");
    window.localStorage.setItem("personal-brain-theme", next);
  }

  function closeProfile() {
    setProfileOpen(false);
    setProfileView("account");
  }

  function navClass(href: string) {
    const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
    return `group flex items-center gap-3 rounded-md px-3 py-2.5 text-[13px] font-medium transition ${active ? "bg-indigo-50 text-indigo-700" : "text-muted hover:bg-elevated hover:text-ink"}`;
  }

  const initials = userEmail?.slice(0, 2).toUpperCase() ?? "PB";

  return (
    <div className="min-h-screen bg-canvas text-ink">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[212px] flex-col border-r border-line bg-sidebar px-3 py-4 lg:flex">
        <div className="flex h-10 items-center px-2"><p className="text-[14px] font-semibold tracking-[-0.02em] text-ink">Personal Brain</p></div>
        <nav aria-label="主要導覽" className="mt-7 space-y-1">
          {nav.map(({ href, label, icon: Icon }) => <Link key={href} href={href} className={navClass(href)}><Icon className="h-4 w-4" strokeWidth={1.7} />{label}</Link>)}
        </nav>
      </aside>

      <header className="fixed inset-x-0 top-0 z-30 flex h-14 items-center border-b border-line bg-canvas/95 px-4 lg:left-[212px] lg:px-6">
        <button type="button" aria-label={mobileMenu ? "關閉選單" : "開啟選單"} onClick={() => setMobileMenu((open) => !open)} className="mr-3 rounded-md p-2 text-muted transition hover:bg-elevated hover:text-ink lg:hidden">{mobileMenu ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}</button>
        <div className="flex items-center gap-2 lg:hidden"><span className="text-sm font-semibold tracking-[-0.02em]">Personal Brain</span></div>
        <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
          <form action="/ask" method="get" className="hidden min-w-[310px] items-center gap-2 rounded-md border border-line bg-surface px-3 py-2 transition focus-within:border-indigo-200 md:flex">
            <Search className="h-3.5 w-3.5 text-muted" />
            <input name="q" aria-label="搜尋知識" className="w-full bg-transparent text-xs text-ink outline-none placeholder:text-faint" placeholder="搜尋文件、筆記與研究…" />
            <kbd className="text-[9px] text-faint">⌘ K</kbd>
          </form>
          <button type="button" onClick={() => setCaptureOpen(true)} aria-label="新增資料" className="flex h-9 items-center gap-2 rounded-md bg-indigo-500 px-2.5 text-xs font-semibold text-white transition hover:bg-indigo-700 sm:px-3"><Plus className="h-4 w-4" /><span className="hidden sm:inline">新增資料</span></button>
          <div className="relative">
            <button type="button" aria-label="通知中心" onClick={() => setNotificationsOpen((open) => !open)} className="grid h-9 w-9 place-items-center rounded-md text-muted transition hover:bg-elevated hover:text-ink"><Bell className="h-4 w-4" /></button>
            {notificationsOpen ? <div className="absolute right-0 top-11 w-72 rounded-[10px] border border-line bg-surface p-4 shadow-soft"><p className="text-xs font-semibold">通知中心</p><p className="mt-3 text-xs leading-5 text-muted">尚未串接通知資料，不會顯示模擬通知。</p></div> : null}
          </div>
          <div className="relative">
            <button type="button" aria-expanded={profileOpen} aria-haspopup="menu" aria-label="使用者選單" onClick={() => { setProfileOpen((open) => !open); setProfileView("account"); }} className="grid h-9 w-9 place-items-center rounded-full border border-line bg-surface text-[10px] font-semibold text-indigo-700 transition hover:border-indigo-200 hover:bg-indigo-50">{initials}</button>
            {profileOpen ? (
              <div role="menu" aria-label="使用者選單" className="absolute right-0 top-11 w-64 rounded-[10px] border border-line bg-surface p-2 shadow-soft">
                {profileView === "account" ? <>
                  <div className="px-2 pb-2 pt-1"><p className="truncate text-xs font-semibold text-ink">{userEmail ?? "已登入使用者"}</p><p className="mt-0.5 text-[11px] text-muted">Supabase Auth 帳號</p></div>
                  <div className="border-t border-line pt-1">
                    <button type="button" role="menuitem" onClick={() => setProfileView("preferences")} className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-xs text-muted transition hover:bg-elevated hover:text-ink"><Settings2 className="h-4 w-4" />設定與偏好</button>
                    <form method="post" action="/auth/actions/sign-out"><button role="menuitem" type="submit" className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-xs text-muted transition hover:bg-elevated hover:text-ink"><LogOut className="h-4 w-4" />登出</button></form>
                  </div>
                </> : <>
                  <button type="button" onClick={() => setProfileView("account")} className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-xs font-semibold text-ink transition hover:bg-elevated"><ArrowLeft className="h-4 w-4" />設定與偏好</button>
                  <div className="mt-1 border-t border-line px-2 py-3"><p className="text-[11px] font-medium text-muted">外觀</p><div className="mt-2 grid grid-cols-2 gap-2"><button type="button" onClick={() => updateTheme("light")} aria-pressed={theme === "light"} className={`flex items-center justify-center gap-1.5 rounded-md border px-2 py-2 text-xs transition ${theme === "light" ? "border-indigo-200 bg-indigo-50 text-indigo-700" : "border-line text-muted hover:bg-elevated"}`}><Sun className="h-3.5 w-3.5" />淺色</button><button type="button" onClick={() => updateTheme("dark")} aria-pressed={theme === "dark"} className={`flex items-center justify-center gap-1.5 rounded-md border px-2 py-2 text-xs transition ${theme === "dark" ? "border-indigo-200 bg-indigo-50 text-indigo-700" : "border-line text-muted hover:bg-elevated"}`}><Moon className="h-3.5 w-3.5" />深色</button></div></div>
                </>}
              </div>
            ) : null}
          </div>
        </div>
      </header>

      {mobileMenu ? <div className="fixed inset-x-3 top-16 z-40 rounded-[10px] border border-line bg-surface p-2 shadow-soft lg:hidden"><nav aria-label="行動版主要導覽" className="grid grid-cols-2 gap-1">{nav.map(({ href, label, icon: Icon }) => <Link key={href} href={href} onClick={() => setMobileMenu(false)} className={navClass(href)}><Icon className="h-4 w-4" />{label}</Link>)}</nav></div> : null}

      <main className="min-h-screen px-4 pb-24 pt-20 sm:px-6 lg:ml-[212px] lg:px-8 lg:pb-12 xl:px-10"><div className="mx-auto max-w-[1240px]">{children}</div></main>
      <nav aria-label="手機導覽" className="fixed inset-x-3 bottom-3 z-30 grid grid-cols-5 rounded-[10px] border border-line bg-surface/95 p-1 shadow-soft backdrop-blur-lg lg:hidden">
        {nav.map(({ href, label, icon: Icon }) => { const active = href === "/" ? pathname === "/" : pathname.startsWith(href); return <Link key={href} href={href} className={`flex flex-col items-center gap-1 rounded-md py-2 text-[9px] font-medium transition ${active ? "bg-indigo-50 text-indigo-700" : "text-muted"}`}><Icon className="h-4 w-4" /><span className="max-w-full truncate px-0.5">{label}</span></Link>; })}
      </nav>
      <AddDataModal open={captureOpen} onClose={() => setCaptureOpen(false)} />
    </div>
  );
}
