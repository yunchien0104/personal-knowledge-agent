# Deep Research full E2E readiness

## R2A evidence-aware payload shaping (2026-11-05)

Real Run `e7789eba-5d8e-454e-ac6e-252a65077e4f` reached R2A after six
PostgreSQL lexical queries and 13 distinct internal Evidence records, then
failed closed because its tokenizer-independent serialized-byte upper bound was
59,655 against the 48,000 R2A guard. Component replay showed that Evidence JSON
was 50,653 bytes (about 85%); the compacted ResearchPlan was 7,407 bytes, the
system prompt 1,228 bytes, and provenance metadata without excerpts 4,762
bytes. Whitespace normalization alone saved only about 500 bytes, so raising the
guard or deleting metadata would not have been a defensible fix.

Deep-only R2A preflight now performs deterministic extractive shaping. It uses
the accepted ResearchPlan plus the latest R2C requirement coverage, evidence
quality, contradictions, and conflicts to select exact source spans. Every
source viewpoint and immutable Evidence ID receives visible text; required
coverage and counter-evidence are mandatory. The Provider payload retains the
Evidence ID, asset/revision IDs, PDF locator, and an explicit marker that source
text was omitted and claims may use visible text only. The unmodified Evidence
remains in `asset_evidence` and `research_run_evidence`.

The same 13-Evidence replay now has a 47,683-byte upper bound. All 13 IDs and
all three source assets remain present; five Evidence excerpts are partial and
none are wholly excluded. Omitted source bytes are 1,194, 1,140, 2,436, 6,024,
and 1,197 for Evidence IDs ending `6e24`, `36c5`, `8e50`, `6b9e`, and `3136`,
respectively. R2C requirements `req_03_02` and `req_04_02`, which had no
assessed supporting or contradicting Evidence, remain explicitly
`unresolved_no_assessed_evidence`; shaping does not promote them to covered.

Migration `20261105090000_deep_research_r2a_payload_manifests.sql` adds a
lease-fenced, append-only, Owner-RLS manifest. It durably records the algorithm,
original/shaped bounds, included/partial/excluded IDs, source excerpt hashes,
selected character ranges, selection reasons, and required-coverage impact
before any R2A reservation or Provider call. Exact replay is idempotent;
conflicting replay is rejected. R2A citations are still checked against the
original Evidence. R2E receives only the cited exact quote plus as much exact
surrounding context as fits its existing guard, so it cannot cite omitted or
untraceable content. Legacy finalization is unchanged.

At Standard pricing ($2/M input, $10/M output), the shaped R2A slot reserves
$0.1210 instead of the configured-envelope $0.1216. The three-claim
finalization preflight is $0.3688, while the protected worst-case final reserve
remains $0.3694. Replaying this run's R2B and R2C envelopes ($0.1929 and
$0.2809) gives a full sequential worst case of $0.8426; R1/R2D are zero-cost.
The recommended next controlled configuration remains Internal-only, one round,
at most six queries, and three claims under $1/run, $2/day, and $10/month.

Free verification: actual 13-Evidence replay PASS; focused coordinator and
finalization **56 passed, 1 Local-DB harness skip**; complete non-provider
Worker **277 passed, 1 skipped, 9 subtests passed**. The retained 60322 stack
accepted the additive migration and the new protocol passed 18/18. A separate
schema-only, zero-data disposable database passed finalization, full-run budget,
durability, real two-session concurrency, and manifest pgTAP: **121/121**. It
was deleted after testing; the retained Run still has all 13 Evidence rows.
Supabase bootstrap plus clean 39-migration replay was not claimed because the
CLI could not write its sandboxed telemetry file. No paid Provider call ran.

## R2C 8192-token and required-evidence repair (2026-11-05)

The three-PDF Real Provider run showed that R2C consumed its entire 3584-token
completion allowance as reasoning and returned no visible JSON. R2C now uses an
8192-token ceiling, matching R2B; R2A, R2E, R2F, Standard tier, low reasoning,
timeout, and bounded-repair policy are unchanged. Length exhaustion remains
non-repairable, and every initial/repair call remains covered by the atomic
payload-aware reservation.

R2B now rejects a new plan when all Evidence Requirements are optional. At
least one requirement necessary to answer the core question must be required;
supplemental requirements may remain optional. R2C and R2D now agree that only
required requirements must be satisfied with `sufficiency=pass` for
`overallStatus=sufficient`, while any critical gap or material contradiction
still prevents quality completion. The R2D check repeats this validation when
resuming a persisted assessment.

Free verification: focused R2B/R2C/R2D contracts **67 passed**; complete
non-provider Worker **268 passed, 1 skipped, 9 subtests passed**. A newly
created disposable Local Supabase replayed **38/38 migrations** and all **23
pgTAP files / 632 assertions passed**. The retained 60321/60322 PDF stack was
not reset or used as a clean test fixture. No paid Provider call occurred.

## R2B 8192-token execution repair (2026-11-05)

After the three-PDF run exhausted the prior 2560-token R2B ceiling entirely as
reasoning, that repair raised R2B to 8192 while R2C retained its prior lower
ceiling. The repair above now raises R2C to 8192 as well. R2A, R2E, and R2F
limits are unchanged. The existing error-directed policy also
remains unchanged: only contract validation may use the single bounded repair,
while length, timeout, refusal, content filter, and unknown outcomes do not
retry automatically.

The three-PDF payload-aware preflight below includes an initial call plus one
possible contract repair per R2B/R2C stage. The matrix includes the configured
R2A/R2E/R2F repair and reverification envelope. It does not assume average
usage, successful cache discounts, or zero cost for unknown outcomes.

Verification was free/offline: 83 focused tests passed; the full non-provider
Worker suite passed with 263 tests, 1 skip, and 9 script subtests; a clean
38-migration Local Supabase stack passed all 23 pgTAP files / 632 assertions.
No Provider call, hosted Supabase access, Production change, Push, Merge, or
Deploy occurred.

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
isolated account policy that was then configured as one search round, three
queries, a `$0.50` run, `$2` daily, and `$10` monthly. That historical run limit
is superseded by migration `20261104171531_increase_deep_research_run_budget_cap.sql`;
the current per-run hard cap is `$1.00`. All 37 migrations replayed; 23 pgTAP files and
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

The accepted three-PDF plan contains eight queries. A read-only PostgreSQL
lexical replay returned 12 rows per query and 23 distinct Evidence IDs. R2B is
`$0.1921`; internal R1 and R2D are `$0`; finalization is
`$0.2752 + $0.0314 × claims`. Because one round now correctly batches multiple
queries before one R2C assessment, query count—not round count—drives this
fixture's R2C payload.

| Query prefix | Distinct evidence | R2C worst case | R2A byte/token upper bound | 1 claim | 3 claims | 6 claims |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 8 | $0.3438 | 28,490 | $0.8425 | $0.9053 | $0.9995 |
| 2 | 9 | $0.3583 | 31,324 | $0.8570 | $0.9198 | $1.0140 |
| 6 | 11 | $0.3776 | 34,595 | $0.8763 | $0.9391 | $1.0333 |
| 7 | 18 | $0.4367 | 43,848 | $0.9354 | $0.9982 | $1.0924 |
| 8 | 23 | $0.4873 | 52,573 | $0.9860 | $1.0488 | $1.1430 |

For Web-enabled runs add the configured `$0.0100` envelope per Web round, so
the three rows add `$0.0100`, `$0.0200`, and `$0.0300` respectively. Repairs and
reverification are already included. Unknown outcomes remain fully charged.

The old `$0.10` final reserve is not sufficient for any configured finalization
envelope: the verified final costs are `$0.3066`, `$0.3694`, and `$0.4636` for
1/3/6 claims. The dynamic reserve fixes that unsafe assumption
within the current `$1.00` hard cap. Staged settlement/release still improves
typical capacity while every worst-case reservation remains fail-closed.

`BUDGET_FEASIBILITY` is **PASS** for the retained fixture at six queries / three
claims (`$0.9391`) or seven queries / three claims (`$0.9982`). The latter has
too little operating margin to recommend. Eight queries also exceed the 48,000
R2A input upper-bound guard, independently of cost. The recommended controlled
configuration is therefore one round, at most six queries, and at most three
claims. Staged
settlement and release can admit a later round only when actual earlier spend
leaves enough capacity for that round and the protected final reserve;
worst-case planning must not assume that saving. The system still fails
before R2B when its configured final envelope cannot be protected, and
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
- `BUDGET_FEASIBILITY`: **PASS** for the configured maxima above
- `REPLAY_AND_PUBLICATION_SAFETY`: **PASS**
- `LEGACY_COMPATIBILITY`: **PASS** (unchanged Legacy path; full non-paid regression)
- `REAL_PROVIDER_FULL_E2E`: **PASS** for the controlled Internal-only run documented above

Any subsequent controlled Real Provider E2E should remain Internal-only unless
Web is separately authorized, use no concurrent paid run on the test account,
retain sufficient daily/monthly headroom, and verify model/pricing/tier/cache
configuration. The current `$1.00` run cap does not replace explicit paid-test
authorization. Acceptance requires all
request IDs and usage, exact Ledger reconciliation, strict R2E/R2F gate outcome,
one publication, and zero automatic replay of an uncertain operation.
# Finalization ceiling supersession (2026-11-05)

The budget tables below describe the former 2,560/768 final-stage ceilings and
are retained as historical audit evidence. Current Deep-only defaults are R2A
16,384, R2E 4,096, R2F repair 16,384, and R2F reverify 4,096. Current finalization
worst cases are $0.6498/$0.8458/$1.1398 for 1/3/6 claims. Consequently the former
`$0.9391` six-query/three-claim full-run recommendation is no longer admissible
under the $1 run cap; its conservative current total is **$1.4155**. See
`docs/DEEP_RESEARCH_FINALIZATION.md` for the authoritative readiness result.
