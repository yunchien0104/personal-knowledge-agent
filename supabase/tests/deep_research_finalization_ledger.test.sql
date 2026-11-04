begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(40);

select has_table('public','research_finalization_artifacts',
  'durable finalization artifacts exist');
select has_function('public','reserve_deep_research_finalization',
  array['uuid','text','bigint','jsonb'],'atomic finalization preflight exists');
select has_function('public','complete_deep_research_final_operation',
  array['uuid','text','bigint','uuid','text','text','jsonb','text','text','text',
        'text','text','integer','integer','integer','numeric','integer','text'],
  'artifact and settlement commit RPC exists');
select has_function('public','load_deep_research_finalization',
  array['uuid','text','bigint'],'lease-fenced finalization load exists');
select ok(not has_table_privilege('authenticated',
  'public.research_finalization_artifacts','insert'),
  'authenticated users cannot write artifacts');
select ok(has_table_privilege('authenticated',
  'public.research_finalization_artifacts','select'),
  'authenticated owners may query artifacts through RLS');
select ok(not has_function_privilege('authenticated',
  'public.reserve_deep_research_finalization(uuid,text,bigint,jsonb)','execute'),
  'authenticated users cannot reserve final calls');
select ok(has_function_privilege('service_role',
  'public.reserve_deep_research_finalization(uuid,text,bigint,jsonb)','execute'),
  'service role can reserve final calls');

insert into auth.users (
  instance_id,id,aud,role,email,encrypted_password,email_confirmed_at,
  raw_app_meta_data,raw_user_meta_data,created_at,updated_at
) values
('00000000-0000-0000-0000-000000000000','fa000000-0000-4000-8000-000000000001',
 'authenticated','authenticated','final-a@example.test','',now(),
 '{"provider":"email","providers":["email"]}','{}',now(),now()),
('00000000-0000-0000-0000-000000000000','fb000000-0000-4000-8000-000000000001',
 'authenticated','authenticated','final-b@example.test','',now(),
 '{"provider":"email","providers":["email"]}','{}',now(),now());

insert into public.deep_research_account_settings (
  user_id,max_cost_usd,daily_research_limit_usd,monthly_research_limit_usd
) values ('fa000000-0000-4000-8000-000000000001',0.5000,2.0000,10.0000);

create temporary table final_state (run_id uuid, job_id uuid);
grant select,insert,update on final_state to authenticated,service_role;
set local role authenticated;
set local request.jwt.claim.sub='fa000000-0000-4000-8000-000000000001';
insert into final_state
select (value->>'id')::uuid,(value->>'jobId')::uuid
from (select public.start_deep_research(
  'Finalization ledger test','fa000000-0000-4000-8000-000000000002',
  null,'disabled','{"maxRounds":2,"maxQueries":2,"maxCostUsd":0.5,"maxDurationSeconds":300}'::jsonb,
  null
) value) started;
reset role;
set local role service_role;

select is((select status from public.claim_job('final-worker-a','research',120)),
  'running','finalization test job is leased');

select is(jsonb_array_length(public.reserve_deep_research_finalization(
  (select job_id from final_state),'final-worker-a',1,
  jsonb_build_array(
    jsonb_build_object('operationId','fa000000-0000-4000-8000-000000000010',
      'idempotencyKey','final:synthesize:v1','actionType','synthesize',
      'costCategory','r2a','parametersHash',repeat('a',64),'estimatedCostUsd',0.0500),
    jsonb_build_object('operationId','fa000000-0000-4000-8000-000000000011',
      'idempotencyKey','final:verify:0','actionType','verify',
      'costCategory','r2e','parametersHash',repeat('b',64),'estimatedCostUsd',0.0200),
    jsonb_build_object('operationId','fa000000-0000-4000-8000-000000000012',
      'idempotencyKey','final:repair:v1','actionType','synthesize',
      'costCategory','r2f','parametersHash',repeat('c',64),'estimatedCostUsd',0.0500),
    jsonb_build_object('operationId','fa000000-0000-4000-8000-000000000013',
      'idempotencyKey','final:reverify:0','actionType','verify',
      'costCategory','r2f','parametersHash',repeat('d',64),'estimatedCostUsd',0.0200)
  )
)->'operations'),4,'all worst-case final calls reserve in one preflight');
select is((select reserved_usd from public.research_run_budgets
  where research_run_id=(select run_id from final_state)),0.1400::numeric,
  'worst-case final cost is reserved before any call');
select is((select count(*) from public.research_budget_ledger
  where event_type='reserved'),4::bigint,'each paid boundary has a Ledger reservation');

select ok(public.start_deep_research_operation(
  (select job_id from final_state),'final-worker-a',1,
  'fa000000-0000-4000-8000-000000000010'),
  'R2A call crosses provider boundary only after start');
select is(public.complete_deep_research_final_operation(
  (select job_id from final_state),'final-worker-a',1,
  'fa000000-0000-4000-8000-000000000010','draft','initial',
  '{"schemaVersion":"atomic-research-v1","title":"T","summary":"S","findings":[]}'::jsonb,
  repeat('1',64),'fixture-provider','fixture-model','request-r2a','stop',
  100,50,0,0.0010,12,'{"requestIds":["request-r2a"]}'
)->>'status','completed','artifact and R2A settlement commit atomically');
select is((select actual_cost_usd from public.research_operations
  where id='fa000000-0000-4000-8000-000000000010'),0.0010::numeric,
  'R2A actual cost is authoritative');
select is((select count(*) from public.research_finalization_artifacts
  where artifact_type='draft'),1::bigint,'accepted R2A draft is durable');
select is((select count(*) from public.research_budget_ledger
  where operation_id='fa000000-0000-4000-8000-000000000010'
    and event_type='settled'),1::bigint,'R2A settlement is in the append-only Ledger');
select is((select sum(amount_usd) from public.research_budget_ledger
  where operation_id='fa000000-0000-4000-8000-000000000010'
    and event_type='released'),0.0490::numeric,'unused R2A reservation is released');

select ok(public.start_deep_research_operation(
  (select job_id from final_state),'final-worker-a',1,
  'fa000000-0000-4000-8000-000000000011'),
  'R2E claim verification starts after reservation');
select is(public.complete_deep_research_final_operation(
  (select job_id from final_state),'final-worker-a',1,
  'fa000000-0000-4000-8000-000000000011','verification','claim-1',
  '{"claimId":"claim-1","verdict":"supported","explanation":"supported"}'::jsonb,
  repeat('2',64),'fixture-provider','fixture-model','request-r2e','stop',
  80,20,0,0.0004,8,'{"requestIds":["request-r2e"]}'
)->>'status','completed','per-claim R2E result and settlement are atomic');
select is((select count(*) from public.research_finalization_artifacts
  where artifact_type='verification'),1::bigint,'accepted R2E result is durable');

select ok(public.start_deep_research_operation(
  (select job_id from final_state),'final-worker-a',1,
  'fa000000-0000-4000-8000-000000000012'),
  'R2F repair starts after reservation');
update public.jobs set lease_owner='final-worker-b',lease_generation=2,
  lease_expires_at=now()+interval '120 seconds'
where id=(select job_id from final_state);
select is((public.reserve_deep_research_finalization(
  (select job_id from final_state),'final-worker-b',2,
  jsonb_build_array(
    jsonb_build_object('operationId','fa000000-0000-4000-8000-000000000010',
      'idempotencyKey','final:synthesize:v1','actionType','synthesize',
      'costCategory','r2a','parametersHash',repeat('a',64),'estimatedCostUsd',0.0500),
    jsonb_build_object('operationId','fa000000-0000-4000-8000-000000000011',
      'idempotencyKey','final:verify:0','actionType','verify',
      'costCategory','r2e','parametersHash',repeat('b',64),'estimatedCostUsd',0.0200),
    jsonb_build_object('operationId','fa000000-0000-4000-8000-000000000012',
      'idempotencyKey','final:repair:v1','actionType','synthesize',
      'costCategory','r2f','parametersHash',repeat('c',64),'estimatedCostUsd',0.0500),
    jsonb_build_object('operationId','fa000000-0000-4000-8000-000000000013',
      'idempotencyKey','final:reverify:0','actionType','verify',
      'costCategory','r2f','parametersHash',repeat('d',64),'estimatedCostUsd',0.0200)
  )
)->'operations'->2->>'status'),'outcome_unknown',
  'lease takeover conservatively marks an in-flight repair uncertain');
select is((select uncertain_cost_usd from public.research_operations
  where id='fa000000-0000-4000-8000-000000000012'),0.0500::numeric,
  'unknown repair retains its full reservation as uncertain');
select is((select count(*) from public.research_finalization_artifacts
  where operation_id='fa000000-0000-4000-8000-000000000012'),0::bigint,
  'unknown repair has no fabricated durable output');
select is(public.settle_deep_research_operation(
  (select job_id from final_state),'final-worker-b',2,
  'fa000000-0000-4000-8000-000000000013','cancelled',0,null,
  'finalization_slot_unused'
)->>'status','cancelled','unused reverification reservation is released');
select is((select reserved_usd from public.research_run_budgets
  where research_run_id=(select run_id from final_state)),0::numeric,
  'no final reservation remains after completion, unknown outcome, and release');
select is((select actual_usd from public.research_run_budgets
  where research_run_id=(select run_id from final_state)),0.0014::numeric,
  'R2A and R2E actual costs reconcile exactly');
select is((select uncertain_usd from public.research_run_budgets
  where research_run_id=(select run_id from final_state)),0.0500::numeric,
  'unknown R2F spend remains conservatively accounted');

select is(jsonb_array_length(public.load_deep_research_finalization(
  (select job_id from final_state),'final-worker-b',2
)->'artifacts'),2,'restart loads accepted R2A and R2E artifacts');

reset role;
set local role authenticated;
set local request.jwt.claim.sub='fa000000-0000-4000-8000-000000000001';
select is((select count(*) from public.research_finalization_artifacts),2::bigint,
  'owner can read finalization artifacts');
set local request.jwt.claim.sub='fb000000-0000-4000-8000-000000000001';
select is((select count(*) from public.research_finalization_artifacts),0::bigint,
  'cross-user finalization artifact reads are denied');
reset role;
set local role service_role;
select throws_ok($$update public.research_finalization_artifacts
  set latency_ms=latency_ms+1$$,'23000',
  'research finalization artifacts are append-only',
  'accepted finalization artifacts cannot be mutated');
select throws_ok($$delete from public.research_finalization_artifacts$$,'23000',
  'research finalization artifacts are append-only',
  'accepted finalization artifacts cannot be deleted');
select is(public.complete_deep_research_final_operation(
  (select job_id from final_state),'final-worker-b',2,
  'fa000000-0000-4000-8000-000000000010','draft','initial',
  '{"schemaVersion":"atomic-research-v1","title":"T","summary":"S","findings":[]}'::jsonb,
  repeat('1',64),'fixture-provider','fixture-model','request-r2a','stop',
  100,50,0,0.0010,12,'{"requestIds":["request-r2a"]}'
),null::jsonb,'stale lease cannot replay a completed paid operation');
select is((select count(*) from public.research_finalization_artifacts),2::bigint,
  'terminal replay does not duplicate artifacts');
select is((select count(*) from public.research_budget_ledger
  where event_type='uncertain' and cost_category='r2f'),1::bigint,
  'unknown R2F outcome has exactly one immutable Ledger event');
select is((select count(*) from public.research_budget_ledger
  where event_type='released'),3::bigint,
  'successful and unused final reservations release exactly once');
select throws_ok($sql$select public.reserve_deep_research_finalization(
  (select job_id from final_state),'final-worker-b',2,
  jsonb_build_array(jsonb_build_object(
    'operationId','fa000000-0000-4000-8000-000000000020',
    'idempotencyKey','final:exhaustion:test','actionType','synthesize',
    'costCategory','r2a','parametersHash',repeat('e',64),
    'estimatedCostUsd',0.4500
  ))
)$sql$,'23514','deep finalization budget is exhausted',
  'final-stage worst-case reservation fails closed at the run cap');
select is((select count(*) from public.research_operations
  where id='fa000000-0000-4000-8000-000000000020'),0::bigint,
  'failed finalization preflight leaves no partial reservation');

select * from finish();
rollback;
