import { notFound } from "next/navigation";
import { LoginPage, type AuthView } from "@/components/auth/LoginPage";

const authViews = new Set<AuthView>(["login", "register", "verify", "forgot", "reset"]);

export const instant = false;

export default async function AuthPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { slug } = await params;
  const { error, status, email } = await searchParams;
  if (!authViews.has(slug as AuthView)) notFound();

  return <LoginPage initialView={slug as AuthView} error={typeof error === "string" ? error : undefined} status={typeof status === "string" ? status : undefined} initialEmail={typeof email === "string" ? email : ""} />;
}
