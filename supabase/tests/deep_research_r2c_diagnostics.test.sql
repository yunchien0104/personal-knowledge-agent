begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(28);

select has_column('public','research_validation_diagnostics','research_module',
  'diagnostics identify the R2B or R2C module');
select has_column('public','research_validation_diagnostics','attempt_cost_usd',
  'attempt cost is durable');
select has_column('public','research_validation_diagnostics','latency_ms',
  'attempt latency is durable');
select has_function('public','record_deep_research_evaluation_diagnostic',
  array['uuid','text','bigint','uuid','jsonb'],'R2C fenced diagnostic RPC exists');
select ok(not has_function_privilege('authenticated',
  'public.record_deep_research_evaluation_diagnostic(uuid,text,bigint,uuid,jsonb)',
  'execute'),'authenticated users cannot call the R2C Worker RPC');
select ok(has_function_privilege('service_role',
  'public.record_deep_research_evaluation_diagnostic(uuid,text,bigint,uuid,jsonb)',
  'execute'),'service role can call the R2C Worker RPC');

insert into auth.users (
  instance_id,id,aud,role,email,encrypted_password,email_confirmed_at,
  raw_app_meta_data,raw_user_meta_data,created_at,updated_at
) values
('00000000-0000-0000-0000-000000000000','ca000000-0000-4000-8000-000000000001',
 'authenticated','authenticated','r2c-diag-a@example.test','',now(),
 '{"provider":"email","providers":["email"]}','{}',now(),now()),
('00000000-0000-0000-0000-000000000000','cb000000-0000-4000-8000-000000000001',
 'authenticated','authenticated','r2c-diag-b@example.test','',now(),
 '{"provider":"email","providers":["email"]}','{}',now(),now());

insert into public.deep_research_account_settings (
  user_id,max_cost_usd,daily_research_limit_usd,monthly_research_limit_usd
) values ('ca000000-0000-4000-8000-000000000001',0.2000,2.0000,10.0000);

create temporary table r2c_diagnostic_state (run_id uuid, job_id uuid);
grant select,insert,update on r2c_diagnostic_state to authenticated,service_role;
set local role authenticated;
set local request.jwt.claim.sub='ca000000-0000-4000-8000-000000000001';
insert into r2c_diagnostic_state
select (value->>'id')::uuid,(value->>'jobId')::uuid
from (select public.start_deep_research(
  'R2C diagnostic durability test','ca000000-0000-4000-8000-000000000002',
  null,'disabled','{"maxRounds":2,"maxQueries":2,"maxCostUsd":0.2,"maxDurationSeconds":300}'::jsonb,
  null
) value) started;
reset role;
set local role service_role;

select is((select status from public.claim_job('r2c-diagnostic-worker-a','research',120)),
  'running','R2C diagnostic test job is leased');
select is(public.reserve_deep_research_operation(
  (select job_id from r2c_diagnostic_state),'r2c-diagnostic-worker-a',1,
  'ca000000-0000-4000-8000-000000000010','diagnostic:r2c','evaluate_evidence','r2c',
  repeat('c',64),0.0600
)->>'status','reserved','R2C operation is atomically reserved');
select ok(public.start_deep_research_operation(
  (select job_id from r2c_diagnostic_state),'r2c-diagnostic-worker-a',1,
  'ca000000-0000-4000-8000-000000000010'
),'R2C operation starts before diagnostics');

select is(public.record_deep_research_evaluation_diagnostic(
  (select job_id from r2c_diagnostic_state),'r2c-diagnostic-worker-a',1,
  'ca000000-0000-4000-8000-000000000010',
  '{"schemaVersion":"r2c-validation-diagnostic-v1","attemptNumber":1,"attemptStage":"initial","requestId":"req-r2c-initial","finishReason":"stop","inputTokens":200,"outputTokens":80,"reasoningTokens":10,"costUsd":0.0012,"latencyMs":25,"validationStage":"backend_contract","errorCode":"backend_contract_invalid","fieldPath":"requirementCoverage.0.requirementId","ruleId":"r2c_reference_or_contract_validation","diagnosticSummary":"Assessment references an unknown requirement"}'::jsonb
)->>'researchModule','r2c','R2C initial failure is durable');
select is((select attempt_cost_usd from public.research_validation_diagnostics
  where operation_id='ca000000-0000-4000-8000-000000000010'),0.0012::numeric,
  'R2C attempt cost is recorded');
select is((select reasoning_tokens from public.research_validation_diagnostics
  where operation_id='ca000000-0000-4000-8000-000000000010'),10,
  'R2C reasoning usage is recorded');
select is((select latency_ms from public.research_validation_diagnostics
  where operation_id='ca000000-0000-4000-8000-000000000010'),25,
  'R2C latency is recorded');
select is(public.record_deep_research_evaluation_diagnostic(
  (select job_id from r2c_diagnostic_state),'r2c-diagnostic-worker-a',1,
  'ca000000-0000-4000-8000-000000000010',
  '{"schemaVersion":"r2c-validation-diagnostic-v1","attemptNumber":1,"attemptStage":"initial","requestId":"req-r2c-initial","finishReason":"stop","inputTokens":200,"outputTokens":80,"reasoningTokens":10,"costUsd":0.0012,"latencyMs":25,"validationStage":"backend_contract","errorCode":"backend_contract_invalid","fieldPath":"requirementCoverage.0.requirementId","ruleId":"r2c_reference_or_contract_validation","diagnosticSummary":"Assessment references an unknown requirement"}'::jsonb
)->>'attemptStage','initial','exact R2C replay is idempotent');
select is((select count(*) from public.research_validation_diagnostics),1::bigint,
  'R2C replay does not duplicate rows');

select throws_ok($sql$select public.record_deep_research_evaluation_diagnostic(
  (select job_id from r2c_diagnostic_state),'r2c-diagnostic-worker-a',1,
  'ca000000-0000-4000-8000-000000000010',
  '{"schemaVersion":"r2b-validation-diagnostic-v1","attemptNumber":1,"attemptStage":"initial","validationStage":"accepted","diagnosticSummary":"wrong module"}'::jsonb
)$sql$,'22023','R2C validation diagnostic is invalid',
  'R2C RPC rejects R2B schema');
select throws_ok($sql$select public.record_deep_research_evaluation_diagnostic(
  (select job_id from r2c_diagnostic_state),'r2c-diagnostic-worker-a',1,
  'ca000000-0000-4000-8000-000000000010',
  '{"schemaVersion":"r2c-validation-diagnostic-v1","attemptNumber":2,"attemptStage":"repair","validationStage":"accepted","diagnosticSummary":"safe","rawEvidence":"secret"}'::jsonb
)$sql$,'22023','validation diagnostic has unexpected fields',
  'raw evidence cannot enter diagnostics');

reset role;
set local role authenticated;
set local request.jwt.claim.sub='ca000000-0000-4000-8000-000000000001';
select is((select count(*) from public.research_validation_diagnostics),1::bigint,
  'owner can read R2C diagnostics');
set local request.jwt.claim.sub='cb000000-0000-4000-8000-000000000001';
select is((select count(*) from public.research_validation_diagnostics),0::bigint,
  'cross-user R2C diagnostic reads are denied');
reset role;
set local role service_role;

select is(public.reserve_deep_research_operation(
  (select job_id from r2c_diagnostic_state),'r2c-diagnostic-worker-a',1,
  'ca000000-0000-4000-8000-000000000011','diagnostic:r2b-compatible','plan_research','r2b',
  repeat('b',64),0.0300
)->>'status','reserved','existing R2B operation remains reservable');
select ok(public.start_deep_research_operation(
  (select job_id from r2c_diagnostic_state),'r2c-diagnostic-worker-a',1,
  'ca000000-0000-4000-8000-000000000011'
),'existing R2B operation remains startable');
select is(public.record_deep_research_validation_diagnostic(
  (select job_id from r2c_diagnostic_state),'r2c-diagnostic-worker-a',1,
  'ca000000-0000-4000-8000-000000000011',
  '{"schemaVersion":"r2b-validation-diagnostic-v1","attemptNumber":1,"attemptStage":"initial","inputTokens":10,"outputTokens":5,"reasoningTokens":0,"validationStage":"accepted","diagnosticSummary":"Legacy R2B diagnostic remains accepted"}'::jsonb
)->>'researchModule','r2b','existing R2B payload remains backward compatible');
select is((select attempt_cost_usd from public.research_validation_diagnostics
  where operation_id='ca000000-0000-4000-8000-000000000011'),0::numeric,
  'omitted R2B cost retains zero default');

update public.jobs set lease_owner='r2c-diagnostic-worker-b',lease_generation=2,
  lease_expires_at=now()+interval '120 seconds'
where id=(select job_id from r2c_diagnostic_state);
select is(public.record_deep_research_evaluation_diagnostic(
  (select job_id from r2c_diagnostic_state),'r2c-diagnostic-worker-a',1,
  'ca000000-0000-4000-8000-000000000010',
  '{"schemaVersion":"r2c-validation-diagnostic-v1","attemptNumber":2,"attemptStage":"repair","validationStage":"accepted","diagnosticSummary":"stale"}'::jsonb
),null::jsonb,'stale Worker cannot append R2C diagnostics');
select is((select count(*) from public.research_validation_diagnostics),2::bigint,
  'R2B and R2C diagnostics survive lease takeover');
select is((select count(*) from public.research_budget_ledger where event_type='settled'),
  0::bigint,'diagnostics do not settle or alter the Ledger');
select is((select reserved_usd+actual_usd+uncertain_usd from public.research_run_budgets),
  0.0900::numeric,'R2B and R2C reservations remain fully accounted');
select throws_ok($$update public.research_validation_diagnostics set latency_ms=1$$,
  '23000','research validation diagnostics are append-only',
  'extended diagnostics remain append-only');

select * from finish();
rollback;
