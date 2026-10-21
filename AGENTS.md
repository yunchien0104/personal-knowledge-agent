# Codex Working Rules — Personal Brain
- Source of truth: root `PERSONAL_BRAIN_CODEX_SPEC.md`; preserve locked ADR-001..005.
- MVP first; no unsolicited architecture expansion or unrelated refactor.
- Read only task-relevant spec sections and files; use `rg` before opening files.
- One vertical slice per task; state impacted paths and test plan in <=8 lines.
- Use Supabase RLS, private Storage, typed payloads, durable queue, fenced commits.
- Never initiate paid deep research automatically; reserve budget before paid calls.
- Cite evidence by immutable revision and locator; never invent source content.
- Run targeted tests; report results and changed paths in <=12 lines.
- Update root `IMPLEMENTATION_STATUS.md` with concise state and next action.
- No destructive migrations or live paid calls without explicit approval.

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