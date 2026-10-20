import Link from "next/link";
import { notFound } from "next/navigation";

export const instant = false;

export default async function AuthPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { slug } = await params;
  const { error } = await searchParams;
  if (slug !== "login" && slug !== "register") notFound();

  const isLogin = slug === "login";
  return (
    <main style={{ margin: "4rem auto", maxWidth: 420, padding: "0 1rem" }}>
      <h1>{isLogin ? "Sign in" : "Create account"}</h1>
      {typeof error === "string" && <p role="alert">{error}</p>}
      <form method="post" action={`/auth/actions/${isLogin ? "sign-in" : "sign-up"}`}>
        <label>
          Email
          <input name="email" type="email" autoComplete="email" required />
        </label>
        <label>
          Password
          <input
            name="password"
            type="password"
            autoComplete={isLogin ? "current-password" : "new-password"}
            minLength={6}
            required
          />
        </label>
        <button type="submit">{isLogin ? "Sign in" : "Register"}</button>
      </form>
      <p>
        <Link href={isLogin ? "/auth/register" : "/auth/login"}>
          {isLogin ? "Create an account" : "Already have an account"}
        </Link>
      </p>
    </main>
  );
}
