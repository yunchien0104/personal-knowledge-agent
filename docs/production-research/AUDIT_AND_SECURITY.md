# Production Research Pipeline — Audit and Security Review

## Audit outcome

The deployed `5430aa9` revision retained the R2 generator, atomic claims,
verifier, one-attempt repair, re-verification, gate, and fenced publication
adapter. It regressed the active worker from R1 `HybridRetriever` back to the
legacy `get_research_context()` Evidence FTS query. The R1 chunker, embedding
adapter, RRF SQL, and backfill CLI existed but were not in the R2 execution
path. `20261025000100_fix_research_cjk_retrieval.sql` was experimental and is
not used by this integration.

The production path is now:

`trusted Job scope -> deterministic multilingual plan -> bounded multi-query
hybrid retrieval -> per-query RRF -> cross-query RRF/dedup -> retrieval
checkpoint -> R2 generate -> verify -> repair -> reverify -> gate -> fenced
atomic publication`.

The experimental `20261025000100_fix_research_cjk_retrieval.sql` is preserved
verbatim under `docs/experimental/` and is no longer discoverable by Supabase's
migration runner. Its SHA-256 is
`adf237009ce052ad45f3b3dacb798a4a968decff865ed161e219f4ed5e265fff`.

PDF extraction now keeps exact extracted citation text separate from the NFKC,
CJK-line-repaired retrieval representation. Embeddings and lexical indexes use
the retrieval representation; quotes and publication use original Evidence.

## Security review

- The authenticated request RPC derives `user_id` from `auth.uid()` and checks
  every requested Asset against an owned, ready, active, validated Revision.
- The Research worker RPC is bound to `job_id + lease_owner + lease_generation`.
  It derives the tenant from the Job row and rejects scope mismatches.
- Selected Evidence is persisted in `research_run_evidence`; a database trigger
  rejects citations not selected for that exact run or with inconsistent
  user/Asset/Revision/Evidence provenance.
- Retrieval and citation queries require active ready Assets and validated
  active Revisions. Cascading deletion removes chunks and retrieval checkpoints;
  the existing deletion workflow tombstones published citations.
- New exposed tables have forced RLS, ownership policies, least-privilege grants,
  and indexes on ownership/foreign-key columns.
- The old unbound worker retrieval RPC loses its `service_role` execute grant.
- Explicit-user `SECURITY DEFINER` helpers have no `PUBLIC`, `anon`,
  `authenticated`, or direct `service_role` Data API grant. The worker can use
  only the fenced Job-bound wrapper; chunk publication remains service-role-only.
- Evidence text, prompts, API keys, and embeddings are not added to logs.

## Remote drift found during the gate

The linked remote migration history ends at `20261021193156`, so a dry run would
apply `20261022021519_unified_hybrid_retrieval.sql` followed by
`20261024185133_production_research_pipeline.sql`. Read-only remote table stats,
however, show `public.retrieval_chunks` already exists with about 30 rows. This
means the schema and `supabase_migrations.schema_migrations` history disagree.
Do not push, repair, or relabel this history until an operator captures a schema
dump, identifies how R1 was applied, and reviews the exact diff.

## Remaining security verification

Cloud Staging pgTAP/two-user tests and Supabase Security Advisor output are
**BLOCKED**. The linked project has no Supabase branch, no available physical
backup, and PITR is disabled. Production deployment is not authorized.
