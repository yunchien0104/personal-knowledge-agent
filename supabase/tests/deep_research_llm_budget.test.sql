create extension if not exists pgtap with schema extensions;
set search_path = public, extensions;

select plan(15);

select ok(0.0422::numeric + 0.0565::numeric <= 0.1000::numeric,
  'fresh R2B and R2C worst-case reservations fit the scoped test cap');

delete from auth.users where id = 'fa000000-0000-4000-8000-000000000001';
insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values (
  '00000000-0000-0000-0000-000000000000',
  'fa000000-0000-4000-8000-000000000001',
  'authenticated', 'authenticated', 'deep-llm-budget@example.test', '', now(),
  '{"provider":"email","providers":["email"]}'::jsonb, '{}'::jsonb, now(), now()
);
insert into public.deep_research_account_settings (
  user_id, max_cost_usd, daily_research_limit_usd, monthly_research_limit_usd
) values ('fa000000-0000-4000-8000-000000000001', 1.0000, 99.0000, 99.0000);

set role authenticated;
set request.jwt.claim.sub = 'fa000000-0000-4000-8000-000000000001';
select public.start_deep_research(
  'Verify real database R2B and R2C accounting',
  'fa000000-0000-4000-8000-000000000002', null, 'disabled',
  '{"maxRounds":3,"maxQueries":3,"maxCostUsd":1.0,"maxDurationSeconds":300}'::jsonb,
  null
);
reset role;
set role service_role;
select public.claim_job('deep-llm-budget-owner', 'research', 120);

select is(public.reserve_deep_research_operation(
  (select job_id from public.research_runs
   where request_key = 'fa000000-0000-4000-8000-000000000002'),
  'deep-llm-budget-owner', 1,
  'fa000000-0000-4000-8000-000000000003', 'llm:r2b',
  'plan_research', 'r2b', repeat('a', 64), 0.0422
) ->> 'status', 'reserved', 'R2B reserves its worst-case cost before execution');
select ok(public.start_deep_research_operation(
  (select job_id from public.research_runs
   where request_key = 'fa000000-0000-4000-8000-000000000002'),
  'deep-llm-budget-owner', 1, 'fa000000-0000-4000-8000-000000000003'
), 'R2B crosses the paid execution boundary');
select is(public.settle_deep_research_operation(
  (select job_id from public.research_runs
   where request_key = 'fa000000-0000-4000-8000-000000000002'),
  'deep-llm-budget-owner', 1, 'fa000000-0000-4000-8000-000000000003',
  'completed', 0.0200, 'request:r2b', null
) ->> 'status', 'completed', 'R2B settles authoritative actual cost');

select is(public.reserve_deep_research_operation(
  (select job_id from public.research_runs
   where request_key = 'fa000000-0000-4000-8000-000000000002'),
  'deep-llm-budget-owner', 1,
  'fa000000-0000-4000-8000-000000000004', 'llm:r2c',
  'evaluate_evidence', 'r2c', repeat('b', 64), 0.0565
) ->> 'status', 'reserved', 'R2C reserves its worst-case cost before execution');
select ok(public.start_deep_research_operation(
  (select job_id from public.research_runs
   where request_key = 'fa000000-0000-4000-8000-000000000002'),
  'deep-llm-budget-owner', 1, 'fa000000-0000-4000-8000-000000000004'
), 'R2C crosses the paid execution boundary');
select is(public.settle_deep_research_operation(
  (select job_id from public.research_runs
   where request_key = 'fa000000-0000-4000-8000-000000000002'),
  'deep-llm-budget-owner', 1, 'fa000000-0000-4000-8000-000000000004',
  'completed', 0.0300, 'request:r2c', null
) ->> 'status', 'completed', 'R2C settles authoritative actual cost');

select is((select count(*) from public.research_operations
  where user_id = 'fa000000-0000-4000-8000-000000000001'), 2::bigint,
  'R2B and R2C have separate durable operations');
select results_eq(
  $$select reserved_usd, actual_usd, uncertain_usd
    from public.research_run_budgets
    where user_id = 'fa000000-0000-4000-8000-000000000001'$$,
  $$values (0.0000::numeric, 0.0500::numeric, 0.0000::numeric)$$,
  'run budget reconciles both paid calls'
);
select results_eq(
  $$select reserved_usd, actual_usd, uncertain_usd
    from public.research_daily_budgets
    where user_id = 'fa000000-0000-4000-8000-000000000001'$$,
  $$values (0.0000::numeric, 0.0500::numeric, 0.0000::numeric)$$,
  'daily aggregate reconciles both paid calls'
);
select results_eq(
  $$select reserved_usd, actual_usd, uncertain_usd
    from public.research_budget_periods
    where user_id = 'fa000000-0000-4000-8000-000000000001'$$,
  $$values (0.0000::numeric, 0.0500::numeric, 0.0000::numeric)$$,
  'monthly aggregate reconciles both paid calls'
);
select is((select count(*) from public.research_budget_ledger
  where user_id = 'fa000000-0000-4000-8000-000000000001'
    and event_type = 'settled'), 2::bigint,
  'ledger records one settlement for each paid call');
select is((select count(*) from public.research_budget_ledger
  where user_id = 'fa000000-0000-4000-8000-000000000001'
    and event_type = 'released'), 2::bigint,
  'ledger releases both unused reservation balances');
select is((select limit_usd from public.research_daily_budgets
  where user_id = 'fa000000-0000-4000-8000-000000000001'), 2.0000::numeric,
  'daily budget cannot exceed the approved two-dollar hard cap');
select is((select limit_usd from public.research_budget_periods
  where user_id = 'fa000000-0000-4000-8000-000000000001'), 10.0000::numeric,
  'monthly budget cannot exceed the approved ten-dollar hard cap');
reset role;

begin;
alter table public.research_budget_ledger
  disable trigger research_budget_ledger_append_only;
delete from auth.users where id = 'fa000000-0000-4000-8000-000000000001';
alter table public.research_budget_ledger
  enable trigger research_budget_ledger_append_only;
commit;

select * from finish();
