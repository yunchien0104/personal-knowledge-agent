# Step 7A Research Suggestions Rollback

The Step 7A migration is a draft and is not deployed by this task. If it is later
deployed, rollback must be a reviewed forward migration; do not edit migration
history, run `db reset`, or use `CASCADE`.

Before rollback, disable `RESEARCH_SUGGESTIONS_ENABLED`, stop opportunity workers,
and export suggestions and feedback if they must be retained. Then, in one
transaction:

1. Revoke and drop `record_opportunity_feedback`,
   `publish_opportunity_ranking`, `pause_opportunity_budget`,
   `get_opportunity_context`, `enqueue_due_opportunity_rankings`, and
   `enqueue_opportunity_ranking` with their exact signatures.
2. Delete only `rank_opportunities` Job Attempts and Jobs after export.
3. Drop `opportunity_events`, `research_opportunities`, and
   `automatic_budget_periods` in that dependency order.
4. Restore `job_attempts_outcome_check`, `jobs_payload_identity_check`, and
   `jobs_resource_class_check` exactly from
   `20261021120000_research_agent_mvp.sql`, then restore that migration's exact
   `claim_job(text,text,integer)` function body and service-role grant so the
   removed `opportunity` resource class is no longer accepted.

Any unexpected dependency or non-Step-7A row must stop rollback for review.
