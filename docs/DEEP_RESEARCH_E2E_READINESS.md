# Deep Research full E2E readiness

## Real Provider verification (2026-11-04)

`REAL_PROVIDER_FULL_E2E`: **PASS** on the local
`feat/deep-research-llm-integration` branch. The run was Internal-only and used
GPT-6.1 Sol with Standard pricing configured at `$2/M` input and `$10/M`
output. No hosted Supabase, Production, Push, Merge, or Deploy operation was
used.

The former controlled runner rejected the real R2B payload because it compared
the production preflight with a hard-coded `$0.0759` test allowance. The new
runner has no stage-dollar constants: R2B and R2C call their payload-aware model
preflights, finalization uses its configured worst-case slot envelope, and all
admission decisions remain authoritative in the atomic Budget RPCs. The actual
R2B reservation was `$0.0787`, proving that the retired guard is no longer
active. Provider-required verification also fails closed on R2B/R2C fallback.

The first paid execution exposed a separate contract defect: R2A was not told
that only three claim-verification slots had been reserved, so its otherwise
valid draft exceeded `DEEP_FINAL_MAX_CLAIMS=3`. That run stopped before R2E and
publication with `$0.0592` actual and `$0` uncertain. The Deep-only R2A call now
receives both `executionConstraints.maxTotalClaims` and the same hard limit in
its system instructions. Calls from the Legacy workflow omit the optional
limit and retain their prior prompt and payload.

The successful run used Local Supabase project
`personal-brain-full-real-e2e-20261104` on loopback ports 56421/56422 with an
isolated account policy: one search round, three queries, `$0.50` run, `$2`
daily, and `$10` monthly. All 37 migrations replayed; 23 pgTAP files and
628/628 assertions passed; DB lint had zero errors. The Worker regression was
261 passed, 1 skipped, and 9 subtests passed. Three legacy manual scripts that
make Provider calls at module import were intentionally excluded from the
offline suite. Web offline unit tests were 34/34 PASS; regenerated Next route
types, TypeScript typecheck, and ESLint also passed.

Successful run: `c8a484e5-9c2f-4591-a060-280f6f4c7858`.

| Stage | Durable result | Reserved maximum | Actual |
|---|---|---:|---:|
| R2B | accepted plan, 7 requirements, 3 queries | `$0.0787` | `$0.0133` |
| R1 | 3 lexical evidence rows from 3 assets/revisions | `$0` | `$0` |
| R2C | `sufficient`; diagnostics accepted; no Provider error | `$0.1452` | `$0.0296` |
| R2D | `quality_reached`; deterministic transition to synthesis | `$0` | `$0` |
| R2A | 3-claim draft accepted | `$0.0496` | `$0.0113` |
| R2E | 3/3 claims supported | `$0.0471` | `$0.0073` |
| R2F | gate passed without repair; all four worst-case slots cancelled/released | `$0.1127` | `$0` |
| Publication | one Note, four citations, three evidence sources | `$0` | `$0` |

The successful run made 6 paid Provider calls: R2B, R2C, R2A, and three R2E
verifications. Every call has a unique request ID, `finish_reason=stop`,
authoritative token usage, zero cache/reasoning tokens, and a completed Ledger
settlement. Total actual cost was `$0.0615`; reserved and uncertain balances
ended at `$0`. Including the prior contract-failure run, the isolated daily and
monthly rows both reconcile to `$0.1207` actual, `$0` reserved, and `$0`
uncertain.

Seven durable checkpoints cover planning, retrieving, assessing, deciding,
synthesizing, verifying, and publishing. The completed Job and `draft_ready`
Research Run point to exactly one output Note and one output hash. Final Report
ID: `ee254517-e097-45da-acfe-93d3059c3bbc`; the report contains three verified
claims and four provenance-preserving citations. R2F did not require a paid
repair because the strict citation gate passed.

The formal run/daily/monthly Budget RPCs, Lease Fencing, idempotent operation
identities, RLS, and publication RPC were not weakened or replaced. No database
migration or Production budget default changed.

## Historical pre-authorization baseline

Verified locally on 2026-11-04 from `d9b026c`. No paid Provider, hosted
Supabase, Production, Push, Merge, or Deploy operation was used.

## Execution audit

| Stage | Contract and durable boundary | Failure/replay behavior |
|---|---|---|
| R2B | `ResearchRequestV1` to `ResearchPlanV1`; immutable `plan:v1` operation; accepted plan version and diagnostics persist | reservation precedes the call; terminal/unknown operations never re-enter; fallback is not Provider success |
| R1 | approved `RetrievalProposalV1` to fenced `EvidenceRecordV1`; one operation per query fingerprint/round | zero-cost internal retrieval is checkpointed and safely recoverable |
| R2C | plan plus bounded evidence to `EvidenceAssessmentV1`; one operation per evidence fingerprint/round | usage, diagnostics, assessment, and settlement persist; oversized evidence fails closed |
| R2D | assessment plus budget/checkpoint to deterministic decision | every round checkpoints; no Provider boundary; only approved actions execute |
| R2A | accepted evidence to strict atomic draft | one durable paid slot; artifact and settlement commit atomically |
| R2E | one verification slot per bounded claim | accepted verdicts are durable; crash reuse never repeats a completed call |
| R2F | separately reserved repair and per-claim reverification slots | strict gate remains authoritative; uncertain calls require reconciliation |
| Publication | verified report and current citation provenance to one output hash | publishing checkpoint and database idempotency prevent duplicate Notes/citations |

The Coordinator now protects `DeepFinalizationEngine.maximum_cost_usd()` before
R2B/R2C execution. The protected amount is returned into the in-memory state and
subsequent checkpoint, so R2D sees the same authoritative reserve. The database
RPC is service-role-only, lease-generation-fenced, and atomically checks actual,
reserved, uncertain, and protected run spend.

## Worst-case budget

Configured assumptions: R2B `$0.0759`; each representative-evidence R2C round
`$0.1221` (the supplied combined B/C figure is `$0.1980` for one round); internal
R1 and R2D `$0`; finalization is `$0.1152 + $0.0314 × claims`.

| Search rounds | 1 claim | 3 claims | 6 claims |
|---:|---:|---:|---:|
| 1 | $0.3446 | $0.4074 | **$0.5016** |
| 2 | $0.4667 | **$0.5295** | **$0.6237** |
| 3 | **$0.5888** | **$0.6516** | **$0.7458** |

For Web-enabled runs add the configured `$0.0100` envelope per Web round, so
the three rows add `$0.0100`, `$0.0200`, and `$0.0300` respectively. Repairs and
reverification are already included. Unknown outcomes remain fully charged.

The old `$0.10` final reserve is not sufficient for any configured finalization
envelope above one claim: the verified final costs are `$0.1466`, `$0.2094`, and
`$0.3036` for 1/3/6 claims. The new dynamic reserve fixes that unsafe assumption
without changing the `$0.50` hard cap. Staged settlement/release improves typical
capacity, but it cannot make an infeasible worst-case combination feasible.

`BUDGET_FEASIBILITY` is therefore **FAIL** for the unrestricted default (six
claims, even one search round, is `$0.0016` over the run cap). The system now
fails before R2B when its configured final envelope cannot be protected, and
before any later paid operation that would consume protected run funds. A
separate aggregate hold across concurrent runs is not yet represented in the
daily/monthly budget rows; controlled Real E2E must use an isolated test account
with no concurrent paid run until that P1 gap is closed.

## Offline and Local DB evidence

- Focused coordinator/finalization: **44 passed**. This includes full R2B through
  publication, crash after atomic settlement, lease takeover, unknown outcome,
  Provider failure, invalid claim contract, budget exhaustion, and publication
  replay.
- Complete non-paid Worker regression: **256 passed, 9 subtests passed**. Four
  explicitly live Provider modules were excluded; this is not Real Provider E2E.
- Disposable Supabase Local: **37/37 migrations** replayed on a new project and
  public-schema lint returned **0 errors**.
- Complete database suite: **23 files, 628/628 pgTAP assertions PASS**. The two
  real two-session tests used independent PostgreSQL connections.
- Real Local persistence plus Mock Providers: **1 passed**. It persisted one
  plan, two assessments, ten checkpoints, two accepted final artifacts, one
  published Note, and one internal citation. Ledger ended at `$0.0024` actual,
  `$0` reserved, and `$0` uncertain for the deterministic fixture.
- Web Provider was not exercised. This readiness run was explicitly
  Internal-only; existing Web publication/RLS tests passed offline/pgTAP.

## Readiness verdict

- `FULL_PIPELINE_OFFLINE_E2E`: **PASS**
- `BUDGET_FEASIBILITY`: **FAIL** for unrestricted default maxima
- `REPLAY_AND_PUBLICATION_SAFETY`: **PASS**
- `LEGACY_COMPATIBILITY`: **PASS** (unchanged Legacy path; full non-paid regression)
- `REAL_PROVIDER_FULL_E2E`: **BLOCKED** (not authorized and budget conditions below)

Recommended first controlled Real Provider E2E: Internal-only, one retrieval
round, at most three claims, no concurrent run on the test account, at least
`$0.50` daily/monthly headroom, verified model/pricing/tier/cache configuration,
and a scoped authorization of **$0.42**. Its conservative worst case is `$0.4074`;
the unchanged `$0.50` run cap leaves `$0.0926` headroom. Acceptance requires all
request IDs and usage, exact Ledger reconciliation, strict R2E/R2F gate outcome,
one publication, and zero automatic replay of an uncertain operation.
