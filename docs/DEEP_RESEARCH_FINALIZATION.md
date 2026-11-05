# Deep Research R2A/E/F Paid Finalization

Date: 2026-11-04

Branch: `feat/deep-research-llm-integration`

Status: **FINALIZATION READY OFFLINE/LOCAL_DB; REAL R2A/E/F REPLAY NOT YET PASSING**

No paid Provider request, hosted Supabase access, push, merge, or deployment was
performed for this change.

## 2026-11-05 Finalization readiness repair

The retained 13-Evidence replay proved that the old 2,560-token R2A completion
limit could be consumed entirely by reasoning (`finish_reason=length`, 2,560
reasoning tokens, zero visible JSON). Deep-only defaults are now 16,384 output
tokens for R2A and the full-draft R2F repair, and 4,096 for each compact R2E/R2F
verification result. The common transport accepts these limits and the final-stage
timeout is 180 seconds. Legacy clients and execution remain unchanged.

At verified GPT-6.1 Sol Standard pricing ($2/M input, $10/M output), conservative
per-operation rounding gives:

| Final claims | R2A | Initial R2E | R2F repair | R2F reverify | Final worst case |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | $0.2599 | $0.0490 | $0.2919 | $0.0490 | **$0.6498** |
| 3 | $0.2599 | $0.1470 | $0.2919 | $0.1470 | **$0.8458** |
| 6 | $0.2599 | $0.2940 | $0.2919 | $0.2940 | **$1.1398** |

The retained shaped payload is 47,713 conservative input units and dynamically
reserves **$0.8452** for three claims. Thus a finalization-only, three-claim replay
fits the $1 run cap; six claims do not. Including current R2B/R2C worst cases, even
the smallest recorded full-run matrix entry is $1.1857, while the recommended
six-query/three-claim shape is $1.4155. Full Deep execution must raise the run cap
explicitly or fail closed; finalization quality is not reduced to fit $1.

The Local-Supabase mock harness reuses the saved ResearchPlan, all 13 Evidence,
R2C assessment, and R2D checkpoint through the production coordinator. The saved
`qualified_partial` decision produced one verified partial result with seven
durable final artifacts and no false Note. A test-only `complete` outcome then
exercised formal publication: three initial claims, one insufficient R2E verdict,
one bounded R2F repair, two supported reverifications, one Note, two Claims, two
Claim-Evidence rows, and two immutable research citations. Idempotent replay kept
the counts unchanged. Owner RLS read 1/2/2 rows; another user read 0/0/0.

Migration `20261105044742_persist_research_publication_claims.sql` adds a
service-role-only Deep internal publication wrapper. It composes the existing
fenced/idempotent citation-validating RPC and atomically persists verified Claims
and their Evidence relations without changing Legacy publication behavior.

Verification: focused finalization/transport **110 passed**; complete non-live
Worker **288 passed, 1 skipped, 9 subtests passed**; new publication/RLS pgTAP
**5/5 PASS**; real 13-Evidence mock replay **PASS** for both truthful partial and
formal publication paths. The complete pgTAP suite could not be rerun against the
retained data DB because older tests assume globally empty tables; a schema-only
clone lacks the seeded private Storage bucket. This environmental limitation is
not represented as a full-suite PASS.

Remaining real-provider risk: 16,384 is materially above the observed exhaustion
but is not a guarantee against adaptive reasoning use. R2E and R2F have not yet
been exercised against the real Provider. Length, refusal, content-filter,
timeout, contract failure, and ambiguous outcome remain fail-closed with no
automatic paid replay. The controlled replay harness now also refuses to cross
the Provider boundary unless the audited 16,384/4,096/16,384/4,096 output
ceilings, 180-second timeout, and explicit three-claim bound are in effect.

## Paid Provider boundary inventory

| Boundary | Maximum calls | Usage before change | Deep safety now |
| --- | ---: | --- | --- |
| R2A synthesis | 1 | Legacy `generate()` discarded transport metadata | One immutable `final:synthesize:v1` operation; reserve/start before call; accepted draft and settlement commit atomically |
| R2E verification | `DEEP_FINAL_MAX_CLAIMS` | One call per cited claim; deterministic no-citation result has no call | One operation per claim slot; full verifier prompt is input-bounded; deterministic results cost zero; accepted result and settlement commit atomically |
| R2F repair | 1 | One gate-directed repair; no provider retry | Separately reserved `final:repair:v1`; repair/citation constraints must pass before artifact commit |
| R2F reverification | `DEEP_FINAL_MAX_CLAIMS` | One call per retained repaired claim | Separate operation per claim; same strict verifier and gate; no publication before all accepted results |
| R2F citation gate/report/publication | 0 | Deterministic gate/report; publication RPC already idempotent | Gate remains unchanged; `publishing` checkpoint precedes the existing idempotent publication RPC |

There is no automatic Provider retry in these boundaries. Provider timeout,
transport ambiguity, incomplete usage, unexpected tier/cache metadata, or a crash
after a call but before atomic artifact settlement becomes `outcome_unknown` or
reconciliation-required and is never replayed automatically.

## Implementation

- `deep_finalization.py` wraps the existing Legacy generator, verifier, repair,
  citation validation, gate, and report builder. It creates Deep-only clients with
  independent token ceilings but does not alter Legacy execution.
- `llm.py` and `verifier.py` add metadata-returning methods. Existing `synthesize`,
  `repair`, and `verify` contracts remain compatible.
- `deep_coordinator.py` reserves the complete worst-case final-stage envelope in
  one database transaction before the first call. Every slot then transitions
  `reserved -> running -> terminal` under lease-generation fencing.
- Migration `20261104073654_deep_research_finalization_ledger.sql` adds an
  append-only, forced-RLS artifact table and service-role-only atomic reserve,
  completion, and resume RPCs. Accepted artifacts contain structured results and
  accounting metadata, not prompts, API keys, or raw request headers.
- Checkpoints now include `synthesizing`, `verifying`, `repairing`, and
  `publishing`. A restart reuses only a completed operation with a matching durable
  artifact. A running call taken over by a new lease is uncertain and blocks.
- Unused worst-case R2E/R2F slots are explicitly cancelled and released. The
  existing $1.00 run, $2 daily, $10 monthly, and protected final reserve rules are
  enforced by the existing budget RPCs.

## Root cause to fix to regression mapping

| Risk | Fix | Verification |
| --- | --- | --- |
| Entire Legacy finalizer was one untracked crash window | Each paid call has a deterministic operation identity and independent atomic reservation | Per-boundary reserve/start/settle assertions and pgTAP Ledger checks |
| Crash after settlement could repeat a paid call | Artifact insert and settlement are one DB transaction; resume loads completed artifacts | Lost-response tests for R2A, R2E, R2F repair, and R2F reverification |
| Crash before durable result made replay ambiguous | Lease takeover converts running operation to `outcome_unknown` | Offline takeover test and real-PostgreSQL uncertain Ledger assertion |
| Backend-invalid output could look completed or lose its cost | Draft, citation, claim-count, repair, and verification checks run before atomic commit; failures retain usage/cost metadata | Post-provider contract rejection regression |
| Average-cost reservation could underfund repair/reverification | Full max-claims initial verification, one repair, and full reverification are reserved up front | Preflight and final-cap exhaustion tests |
| Duplicate publication after a lost response | Durable `publishing` checkpoint plus existing output-hash idempotency | Lost publication response regression |

## Configuration and worst-case cost

Deep finalization requires `DEEP_FINAL_PRICING_MODEL` to equal `RESEARCH_MODEL`,
`DEEP_FINAL_PRICING_VERIFIED=true`, explicit positive input/output prices, and the
Deep final-stage limits documented in `.env.example`. Missing or mismatched pricing
fails Worker construction closed when Deep Research is enabled.

At $2/M input and $10/M output with the documented defaults and six claims:

- R2A synthesis: 48,000 input + 2,560 output = **$0.1216**.
- Six R2E calls: 4,000 input + 768 output each = **$0.0942** after per-operation conservative rounding.
- One R2F repair: 64,000 input + 2,560 output = **$0.1536**.
- Six R2F reverifications: 4,000 input + 768 output each = **$0.0942**.
- Maximum Deep finalization reservation: **$0.4636**.

The input guard deliberately counts the UTF-8 bytes of the serialized prompt as
a tokenizer-independent **upper bound**, not as the exact model token count. The
failed PDF run's 39,423-byte payload therefore did not prove that the provider
would consume 39,423 tokens; it proved that the old 12,000-token safe envelope
could not admit the request without an authoritative tokenizer. Deep synthesis
now removes execution-only plan metadata and exact duplicate Evidence IDs while
retaining every research-semantic field and complete citation provenance. The
same reconstructed payload falls from a 40,701 to 37,598 upper bound and fits
the new 48,000 guard. Conflicting duplicate Evidence IDs still fail closed.

The Coordinator now writes the configured finalizer envelope as the protected
run reserve before R2B/R2C. Earlier actual, uncertain, reserved, and protected
spend must fit the $1.00 run cap or execution fails before the next Provider
boundary. This is a hard upper-bound candidate, not a Real Provider sufficiency
claim. The accepted PDF plan's recommended six-query/three-claim configuration
is $0.9391; its unrestricted eight-query/six-claim shape is $1.1430 and fails
closed. See `DEEP_RESEARCH_E2E_READINESS.md`.

## Verification results

- Focused finalization: **14 passed**.
- Complete non-paid Worker regression (four explicitly live Provider modules
  excluded): **250 passed, 9 subtests passed**.
- Python compileall and `git diff --check`: **PASS**.
- Disposable Supabase Local: **36/36 migrations** replayed from a new stack.
- Complete database suite: **22 files, 608/608 pgTAP assertions PASS**.
- Public-schema database lint: **0 errors**.
- Dedicated finalization pgTAP: **40/40 PASS**, including Owner RLS,
  service-role-only writes/RPCs, append-only artifacts, atomic batch reservation,
  actual/release/uncertain accounting, stale lease fencing, idempotency, and final
  cap rollback with no partial reservation.

## Remaining blockers and controlled Full Deep E2E conditions

- MOCK/LOCAL_DB is not Real Provider verification. No R2A/E/F Provider output has
  been exercised through this new wrapper.
- A controlled Full Deep E2E needs explicit new paid authorization, a fresh
  isolated Local PostgreSQL stack, verified final-stage model/pricing/tier/cache
  configuration, representative non-sensitive evidence, and a preflight that fits
  both the $0.4636 final envelope and the $1.00 total run cap after all prior spend.
- Acceptance requires real R2A output, every R2E/R2F usage/request ID, exact Ledger
  reconciliation, strict gate success or truthful insufficient result, and no
  fallback represented as Provider success. No automatic replay is permitted for
  an uncertain operation.
