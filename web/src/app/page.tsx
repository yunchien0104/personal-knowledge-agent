import Link from "next/link";

export default function Home() {
  return (
    <main style={{ padding: "48px", fontFamily: "Arial" }}>
      <h1>Personal Brain</h1>
      <p>Phase 1 — Authentication Foundation</p>
      <p><Link href="/auth/register">Register</Link></p>
      <p><Link href="/auth/login">Sign in</Link></p>
      <p><Link href="/protected">Protected page</Link></p>
    </main>
  );
}
