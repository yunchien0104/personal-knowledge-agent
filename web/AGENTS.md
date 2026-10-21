## Personal Brain repository rules

Root `../AGENTS.md` applies to all work under `web/`. The canonical product spec is
root `../PERSONAL_BRAIN_CODEX_SPEC.md`. The generated Next.js rules below supplement
those repository rules; the user's request and root rules take precedence on conflict.

<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

## Development Environment Policy

- Default to cloud-first development using Supabase Cloud Staging.
- Do not require OrbStack, Docker, or local Supabase for ordinary feature development.
- Run TypeScript, ESLint, build, Python unit tests, and fixture tests without containers.
- Do not repeatedly attempt Docker-dependent tests when the environment cannot access the Docker socket.
- Mark container-dependent integration tests as BLOCKED when unavailable; do not claim PASS.
- Cloud Staging integration tests involving writes or deletes require explicit authorization, dedicated test identities, and scoped cleanup.
- Keep Supabase migrations and existing local tests for future regression testing.
- Do not bypass authentication or RLS to make tests pass.

Personal Brain 後續採用 Supabase Cloud Staging-first 開發。

- 本機只保存版本控制中的 Migration SQL，不再要求 OrbStack、本機 Supabase 或 Docker。
- 每次實作需要 Schema 變更時，產生 Migration 並預覽待部署差異；確認目標是指定 Staging、變更非破壞性且不影響既有資料後，在同一次 Task 部署並執行必要測試。
- 使用隔離測試資料並精確清理。
- 已部署 Migration 不得任意修改，後續修正新增 Migration。
- 不操作 Production，不執行 db reset、破壞性 Migration 或全庫清理。
- 有重大風險才停止詢問，不為例行驗證重複建立 Task。
- Migration、測試、程式實作一次完成，保持 Token Efficiency。

<!-- END:nextjs-agent-rules -->
