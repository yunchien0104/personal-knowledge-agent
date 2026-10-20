import { redirect } from "next/navigation";

import { createServerSupabaseClient } from "@/lib/supabase";

export const instant = false;

export default async function ProtectedPage() {
  const supabase = await createServerSupabaseClient();
  const { data: { user }, error } = await supabase.auth.getUser();

  if (error || !user) redirect("/auth/login");

  return (
    <main style={{ padding: "3rem" }}>
      <h1>Protected</h1>
      <p>Authenticated user: {user.id}</p>
      <p>{user.email}</p>
      <form method="post" action="/auth/actions/sign-out">
        <button type="submit">Sign out</button>
      </form>
    </main>
  );
}
