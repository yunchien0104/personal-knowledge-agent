import { redirect } from "next/navigation";
import { connection } from "next/server";
import type { ReactNode } from "react";

import { AppShell } from "@/components/personal-brain/AppShell";
import { createServerSupabaseClient } from "@/lib/supabase";

export const instant = false;

export default async function WorkspaceLayout({ children }: { children: ReactNode }) {
  await connection();
  const supabase = await createServerSupabaseClient();
  const { data: { user }, error } = await supabase.auth.getUser();
  if (error || !user) redirect("/auth/login");
  return <AppShell userEmail={user.email ?? null}>{children}</AppShell>;
}
