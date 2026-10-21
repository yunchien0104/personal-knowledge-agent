# Step 6 Research Agent Rollback

For Staging or Production, implement rollback as a reviewed forward migration. Do
not edit migration history or use `db reset`. Stop Research workers and disable
`/api/research` before rollback; export Research Runs, draft Notes, citations, Jobs,
and Attempts if they must be retained.

The rollback is destructive to Research history and drafts. In one transaction:

1. Revoke and drop `start_research`, `get_research_context`, and
   `publish_research_draft` with their exact signatures.
2. Drop `jobs_sync_research_run_status`, then drop
   `sync_research_run_job_status()`.
3. Delete only `execute_research` Job Attempts and Jobs after exporting them.
4. Delete only Research Draft Notes after confirming the user-retention policy.
5. Drop `research_citations`, then `research_runs`.
6. Restore `claim_job` and `jobs_resource_class_check` from the prior migration;
   restore the previous Jobs payload constraint and `asset_id not null` only after
   proving no row has a null Asset.

Do not use `CASCADE`; an unexpected dependency must stop the rollback for review.
