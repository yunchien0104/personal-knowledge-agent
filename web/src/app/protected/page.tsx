import Link from "next/link";
import { redirect } from "next/navigation";
import { connection } from "next/server";

import { createServerSupabaseClient } from "@/lib/supabase";

export const instant = false;

export default async function ProtectedPage() {
  await connection();
  const supabase = await createServerSupabaseClient();
  const { data: { user }, error } = await supabase.auth.getUser();

  if (error || !user) redirect("/auth/login");

  return (
    <main style={{ padding: "3rem" }}>
      <h1>Protected</h1>
      <p>
        <Link
          href="/upload"
          style={{
            display: "inline-block",
            padding: "0.75rem 1rem",
            borderRadius: "0.5rem",
            background: "#111827",
            color: "#ffffff",
            fontWeight: 600,
            textDecoration: "none",
          }}
        >
          Upload PDF
        </Link>
      </p>
      <p>Authenticated user: {user.id}</p>
      <p>{user.email}</p>
      <form method="post" action="/auth/actions/sign-out">
        <button type="submit">Sign out</button>
      </form>
    </main>
  );
}
