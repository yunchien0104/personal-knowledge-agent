begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(32);

select has_table('public', 'research_validation_diagnostics', 'durable validation diagnostics exist');
select has_function('public', 'record_deep_research_validation_diagnostic',
  array['uuid','text','bigint','uuid','jsonb'], 'fenced diagnostic RPC exists');
select ok((select relrowsecurity and relforcerowsecurity from pg_class
  where oid='public.research_validation_diagnostics'::regclass), 'diagnostics use forced RLS');
select ok(not has_function_privilege('authenticated',
  'public.record_deep_research_validation_diagnostic(uuid,text,bigint,uuid,jsonb)',
  'execute'), 'authenticated users cannot write diagnostics through the Worker RPC');
select ok(has_function_privilege('service_role',
  'public.record_deep_research_validation_diagnostic(uuid,text,bigint,uuid,jsonb)',
  'execute'), 'service role can call the fenced diagnostic RPC');
select ok(has_table_privilege('authenticated','public.research_validation_diagnostics','select'),
  'authenticated owners may query safe diagnostics');
select ok(not has_table_privilege('authenticated','public.research_validation_diagnostics','insert'),
  'authenticated users cannot insert diagnostics directly');

insert into auth.users (
  instance_id,id,aud,role,email,encrypted_password,email_confirmed_at,
  raw_app_meta_data,raw_user_meta_data,created_at,updated_at
) values
('00000000-0000-0000-0000-000000000000','da000000-0000-4000-8000-000000000001',
 'authenticated','authenticated','diag-a@example.test','',now(),
 '{"provider":"email","providers":["email"]}','{}',now(),now()),
('00000000-0000-0000-0000-000000000000','db000000-0000-4000-8000-000000000001',
 'authenticated','authenticated','diag-b@example.test','',now(),
 '{"provider":"email","providers":["email"]}','{}',now(),now());

insert into public.deep_research_account_settings (
  user_id,max_cost_usd,daily_research_limit_usd,monthly_research_limit_usd
) values ('da000000-0000-4000-8000-000000000001',0.2000,2.0000,10.0000);

create temporary table diagnostic_state (run_id uuid, job_id uuid);
grant select,insert,update on diagnostic_state to authenticated,service_role;
set local role authenticated;
set local request.jwt.claim.sub='da000000-0000-4000-8000-000000000001';
insert into diagnostic_state
select (value->>'id')::uuid,(value->>'jobId')::uuid
from (select public.start_deep_research(
  'Validation diagnostic durability test','da000000-0000-4000-8000-000000000002',
  null,'disabled','{"maxRounds":2,"maxQueries":2,"maxCostUsd":0.2,"maxDurationSeconds":300}'::jsonb,
  null
) value) started;
reset role;
set local role service_role;

select is((select status from public.claim_job('diagnostic-worker-a','research',120)),
  'running','diagnostic test job is leased');
select is(public.reserve_deep_research_operation(
  (select job_id from diagnostic_state),'diagnostic-worker-a',1,
  'da000000-0000-4000-8000-000000000010','diagnostic:r2b','plan_research','r2b',
  repeat('a',64),0.0500
)->>'status','reserved','R2B operation is atomically reserved');
select ok(public.start_deep_research_operation(
  (select job_id from diagnostic_state),'diagnostic-worker-a',1,
  'da000000-0000-4000-8000-000000000010'
),'R2B operation crosses the paid boundary before diagnostics');

select is(public.record_deep_research_validation_diagnostic(
  (select job_id from diagnostic_state),'diagnostic-worker-a',1,
  'da000000-0000-4000-8000-000000000010',
  '{"schemaVersion":"r2b-validation-diagnostic-v1","attemptNumber":1,"attemptStage":"initial","requestId":"req-initial","finishReason":"stop","inputTokens":100,"outputTokens":50,"reasoningTokens":0,"validationStage":"provider_dto","errorCode":"provider_dto_invalid","fieldPath":"subquestions.0.requirements.0.evidenceType","ruleId":"literal_error","diagnosticSummary":"Input should match an allowed evidence type"}'::jsonb
)->>'validationStage','provider_dto','initial validation failure is durable');
select is((select count(*) from public.research_validation_diagnostics),1::bigint,
  'one initial attempt is stored');
select is(public.record_deep_research_validation_diagnostic(
  (select job_id from diagnostic_state),'diagnostic-worker-a',1,
  'da000000-0000-4000-8000-000000000010',
  '{"schemaVersion":"r2b-validation-diagnostic-v1","attemptNumber":1,"attemptStage":"initial","requestId":"req-initial","finishReason":"stop","inputTokens":100,"outputTokens":50,"reasoningTokens":0,"validationStage":"provider_dto","errorCode":"provider_dto_invalid","fieldPath":"subquestions.0.requirements.0.evidenceType","ruleId":"literal_error","diagnosticSummary":"Input should match an allowed evidence type"}'::jsonb
)->>'attemptStage','initial','exact replay is idempotent');
select is((select count(*) from public.research_validation_diagnostics),1::bigint,
  'idempotent replay does not duplicate diagnostics');
select throws_ok($sql$select public.record_deep_research_validation_diagnostic(
  (select job_id from diagnostic_state),'diagnostic-worker-a',1,
  'da000000-0000-4000-8000-000000000010',
  '{"schemaVersion":"r2b-validation-diagnostic-v1","attemptNumber":1,"attemptStage":"initial","requestId":"different","finishReason":"stop","inputTokens":100,"outputTokens":50,"reasoningTokens":0,"validationStage":"accepted","errorCode":null,"fieldPath":null,"ruleId":null,"diagnosticSummary":"Different replay"}'::jsonb
)$sql$,'23000','validation diagnostic attempt conflicts with durable record',
  'conflicting replay cannot rewrite an attempt');

select is(public.record_deep_research_validation_diagnostic(
  (select job_id from diagnostic_state),'diagnostic-worker-a',1,
  'da000000-0000-4000-8000-000000000010',
  '{"schemaVersion":"r2b-validation-diagnostic-v1","attemptNumber":2,"attemptStage":"repair","requestId":"req-repair","finishReason":"stop","inputTokens":110,"outputTokens":55,"reasoningTokens":0,"validationStage":"quality_gate","errorCode":"quality_gate_rejected","fieldPath":"essentialClarifications","ruleId":"no_unresolved_essential_clarifications","diagnosticSummary":"Essential clarification remains unresolved"}'::jsonb
)->>'attemptStage','repair','repair diagnostic is stored separately');
select is(public.record_deep_research_validation_diagnostic(
  (select job_id from diagnostic_state),'diagnostic-worker-a',1,
  'da000000-0000-4000-8000-000000000010',
  '{"schemaVersion":"r2b-validation-diagnostic-v1","attemptNumber":3,"attemptStage":"fallback","requestId":null,"finishReason":null,"inputTokens":0,"outputTokens":0,"reasoningTokens":0,"validationStage":"fallback","errorCode":"r2b_fallback_used","fieldPath":null,"ruleId":"bounded_repair_exhausted","diagnosticSummary":"Deterministic fallback used after bounded repair"}'::jsonb
)->>'attemptStage','fallback','fallback diagnostic is stored separately');
select is((select count(*) from public.research_validation_diagnostics),3::bigint,
  'initial repair and fallback form one complete attempt history');

reset role;
set local role authenticated;
set local request.jwt.claim.sub='da000000-0000-4000-8000-000000000001';
select is((select count(*) from public.research_validation_diagnostics),3::bigint,
  'owner can query diagnostics by run and operation');
set local request.jwt.claim.sub='db000000-0000-4000-8000-000000000001';
select is((select count(*) from public.research_validation_diagnostics),0::bigint,
  'cross-user diagnostic reads are denied');
reset role;
set local role service_role;

select throws_ok($sql$select public.record_deep_research_validation_diagnostic(
  (select job_id from diagnostic_state),'diagnostic-worker-a',1,
  'da000000-0000-4000-8000-000000000010',
  '{"schemaVersion":"r2b-validation-diagnostic-v1","attemptNumber":2,"attemptStage":"repair","requestId":"req-repair","finishReason":"stop","inputTokens":1,"outputTokens":1,"reasoningTokens":0,"validationStage":"provider_dto","errorCode":"provider_dto_invalid","fieldPath":"objective","ruleId":"literal_error","diagnosticSummary":"safe","rawOutput":"secret-model-output"}'::jsonb
)$sql$,'22023','validation diagnostic has unexpected fields',
  'raw model output cannot be smuggled into diagnostics');
select is((select count(*) from public.research_validation_diagnostics
  where diagnostic_summary ilike '%secret%' or field_path ilike '%secret%'),0::bigint,
  'stored diagnostics contain no secret fixture text');

update public.jobs set lease_owner='diagnostic-worker-b',lease_generation=2,
  lease_expires_at=now()+interval '120 seconds'
where id=(select job_id from diagnostic_state);
select is(public.reserve_deep_research_operation(
  (select job_id from diagnostic_state),'diagnostic-worker-b',2,
  'da000000-0000-4000-8000-000000000010','diagnostic:r2b','plan_research','r2b',
  repeat('a',64),0.0500
)->>'status','outcome_unknown','lease takeover preserves paid replay safety');
select is((select count(*) from public.research_validation_diagnostics),3::bigint,
  'diagnostics survive crash and lease takeover');
select is(public.record_deep_research_validation_diagnostic(
  (select job_id from diagnostic_state),'diagnostic-worker-a',1,
  'da000000-0000-4000-8000-000000000010',
  '{"schemaVersion":"r2b-validation-diagnostic-v1","attemptNumber":2,"attemptStage":"repair","requestId":"req-repair","finishReason":"stop","inputTokens":110,"outputTokens":55,"reasoningTokens":0,"validationStage":"quality_gate","errorCode":"quality_gate_rejected","fieldPath":"essentialClarifications","ruleId":"no_unresolved_essential_clarifications","diagnosticSummary":"Essential clarification remains unresolved"}'::jsonb
),null::jsonb,'stale Worker cannot append diagnostics');
select is(public.record_deep_research_validation_diagnostic(
  (select job_id from diagnostic_state),'diagnostic-worker-b',2,
  'da000000-0000-4000-8000-000000000010',
  '{"schemaVersion":"r2b-validation-diagnostic-v1","attemptNumber":2,"attemptStage":"repair","requestId":"req-repair","finishReason":"stop","inputTokens":110,"outputTokens":55,"reasoningTokens":0,"validationStage":"quality_gate","errorCode":"quality_gate_rejected","fieldPath":"essentialClarifications","ruleId":"no_unresolved_essential_clarifications","diagnosticSummary":"Essential clarification remains unresolved"}'::jsonb
),null::jsonb,'terminal operation cannot append diagnostics');
select is((select count(*) from public.research_budget_ledger where event_type='uncertain'),
  1::bigint,'diagnostics do not change uncertain-outcome Ledger behavior');
select is((select actual_usd+reserved_usd+uncertain_usd from public.research_run_budgets),
  0.0500::numeric,'diagnostics do not change run budget accounting');
select throws_ok($$update public.research_validation_diagnostics set diagnostic_summary='changed'$$,
  '23000','research validation diagnostics are append-only',
  'diagnostics cannot be updated');
select throws_ok($$delete from public.research_validation_diagnostics$$,
  '23000','research validation diagnostics are append-only',
  'diagnostics cannot be deleted');
select hasnt_column('public','research_validation_diagnostics','raw_prompt',
  'diagnostics schema has no raw Prompt column');
select hasnt_column('public','research_validation_diagnostics','raw_model_output',
  'diagnostics schema has no raw model-output column');

select * from finish();
rollback;
