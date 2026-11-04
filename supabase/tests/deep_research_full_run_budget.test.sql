begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(20);

select has_function('public','ensure_deep_research_final_reserve',
  array['uuid','text','bigint','numeric'],
  'dynamic finalization protection RPC exists');
select ok(not has_function_privilege('authenticated',
  'public.ensure_deep_research_final_reserve(uuid,text,bigint,numeric)','execute'),
  'authenticated users cannot change protected finalization budget');
select ok(has_function_privilege('service_role',
  'public.ensure_deep_research_final_reserve(uuid,text,bigint,numeric)','execute'),
  'service role can protect finalization budget');

insert into auth.users (
  instance_id,id,aud,role,email,encrypted_password,email_confirmed_at,
  raw_app_meta_data,raw_user_meta_data,created_at,updated_at
) values
('00000000-0000-0000-0000-000000000000','fc000000-0000-4000-8000-000000000001',
 'authenticated','authenticated','full-budget-a@example.test','',now(),
 '{"provider":"email","providers":["email"]}','{}',now(),now()),
('00000000-0000-0000-0000-000000000000','fd000000-0000-4000-8000-000000000001',
 'authenticated','authenticated','full-budget-b@example.test','',now(),
 '{"provider":"email","providers":["email"]}','{}',now(),now());

insert into public.deep_research_account_settings (
  user_id,max_cost_usd,daily_research_limit_usd,monthly_research_limit_usd
) values ('fc000000-0000-4000-8000-000000000001',0.5000,2.0000,10.0000);

create temporary table full_budget_state (run_id uuid, job_id uuid);
grant select,insert on full_budget_state to authenticated,service_role;
set local role authenticated;
set local request.jwt.claim.sub='fc000000-0000-4000-8000-000000000001';
insert into full_budget_state
select (value->>'id')::uuid,(value->>'jobId')::uuid
from (select public.start_deep_research(
  'Full run budget protection','fc000000-0000-4000-8000-000000000002',
  null,'disabled',
  '{"maxRounds":3,"maxQueries":3,"maxCostUsd":0.5,"maxDurationSeconds":300}'::jsonb,
  null
) value) started;
reset role;
set local role service_role;
select is((select status from public.claim_job('full-budget-worker','research',120)),
  'running','full-run budget fixture is leased');

select is(public.ensure_deep_research_final_reserve(
  (select job_id from full_budget_state),'full-budget-worker',1,0.3036
)->>'status','protected','six-claim worst-case finalization is protected');
select is((select protected_final_reserve_usd from public.research_run_budgets
  where research_run_id=(select run_id from full_budget_state)),0.3036::numeric,
  'protected reserve stores the actual finalizer envelope, not the old 0.10 default');

select is(public.reserve_deep_research_operation(
  (select job_id from full_budget_state),'full-budget-worker',1,
  'fc000000-0000-4000-8000-000000000010','full:r2b','plan_research','r2b',
  repeat('a',64),0.0759
)->>'status','reserved','R2B is admitted while the complete final envelope remains');
select ok(public.start_deep_research_operation(
  (select job_id from full_budget_state),'full-budget-worker',1,
  'fc000000-0000-4000-8000-000000000010'),
  'R2B crosses the provider boundary after reservation');
select is(public.settle_deep_research_operation(
  (select job_id from full_budget_state),'full-budget-worker',1,
  'fc000000-0000-4000-8000-000000000010','completed',0.0100,null,null
)->>'status','completed','R2B settlement releases unused reservation');

select is(public.reserve_deep_research_operation(
  (select job_id from full_budget_state),'full-budget-worker',1,
  'fc000000-0000-4000-8000-000000000011','full:r2c:1','evaluate_evidence','r2c',
  repeat('b',64),0.1221
)->>'status','reserved','first R2C round fits after actual R2B settlement');
select ok(public.start_deep_research_operation(
  (select job_id from full_budget_state),'full-budget-worker',1,
  'fc000000-0000-4000-8000-000000000011'),
  'R2C crosses the provider boundary after reservation');
select is(public.settle_deep_research_operation(
  (select job_id from full_budget_state),'full-budget-worker',1,
  'fc000000-0000-4000-8000-000000000011','outcome_unknown',0,null,
  'provider_timeout'
)->>'status','outcome_unknown','ambiguous R2C spend remains uncertain');
select is((select uncertain_usd from public.research_run_budgets
  where research_run_id=(select run_id from full_budget_state)),0.1221::numeric,
  'unknown outcome is never reinterpreted as zero');

select is(public.reserve_deep_research_operation(
  (select job_id from full_budget_state),'full-budget-worker',1,
  'fc000000-0000-4000-8000-000000000012','full:r2c:2','evaluate_evidence','r2c',
  repeat('c',64),0.1221
)->>'status','budget_exhausted',
  'another R2C round stops before payment when it would consume final funds');
select is((select count(*) from public.research_operations
  where id='fc000000-0000-4000-8000-000000000012'),0::bigint,
  'rejected R2C does not create an unreserved provider operation');
select is((select actual_usd+uncertain_usd+protected_final_reserve_usd
  from public.research_run_budgets
  where research_run_id=(select run_id from full_budget_state)),0.4357::numeric,
  'actual, uncertain, and protected amounts reconcile below the hard run cap');

reset role;
set local role authenticated;
set local request.jwt.claim.sub='fc000000-0000-4000-8000-000000000001';
select is((select count(*) from public.research_run_budgets),1::bigint,
  'owner can read the protected run budget through RLS');
set local request.jwt.claim.sub='fd000000-0000-4000-8000-000000000001';
select is((select count(*) from public.research_run_budgets),0::bigint,
  'cross-user protected budget reads are denied');

reset role;
set local role service_role;
update public.jobs set lease_owner='stale-worker',lease_generation=2,
  lease_expires_at=now()+interval '120 seconds'
where id=(select job_id from full_budget_state);
select is(public.ensure_deep_research_final_reserve(
  (select job_id from full_budget_state),'full-budget-worker',1,0.3036
),null::jsonb,'stale lease cannot change finalization protection');
select is((select count(*) from public.research_budget_ledger
  where research_run_id=(select run_id from full_budget_state)
    and event_type in ('reserved','settled','released','uncertain')),5::bigint,
  'paid-stage ledger records reservation, settlement, release, and uncertainty exactly once');

select * from finish();
rollback;
