begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(20);

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values (
  '00000000-0000-0000-0000-000000000000', 'f1000000-0000-4000-8000-000000000001',
  'authenticated', 'authenticated', 'deep-repair@example.test', '', now(),
  '{"provider":"email","providers":["email"]}', '{}', now(), now()
);

create temporary table repair_state (run_id uuid, job_id uuid);
grant select, update, insert on repair_state to authenticated, service_role;
set local role authenticated;
set local request.jwt.claim.sub = 'f1000000-0000-4000-8000-000000000001';
insert into repair_state
select (result ->> 'id')::uuid, (result ->> 'jobId')::uuid
from (select public.start_deep_research(
  'Repair abandoned operation reservation',
  'f2000000-0000-4000-8000-000000000001', null, 'disabled',
  '{"maxRounds":3,"maxQueries":3,"maxCostUsd":0.5,"maxDurationSeconds":300}'::jsonb,
  null
) result) started;
reset role;
set local role service_role;

select isnt((select deadline_at from public.research_runs
  where id = (select run_id from repair_state)), null::timestamptz,
  'Deep Research receives a durable deadline');
select is((select status from public.claim_job('repair-worker-a', 'research', 120)),
  'running', 'first Worker acquires the repair fixture');
select is(public.reserve_deep_research_operation(
  (select job_id from repair_state), 'repair-worker-a', 1,
  'f3000000-0000-4000-8000-000000000001', 'paid:reserved-before-start',
  'evaluate_evidence', 'r2c', repeat('a',64), 0.0500
) ->> 'status', 'reserved', 'paid operation is reserved before execution');

update public.jobs set lease_owner = 'repair-worker-b', lease_generation = 2,
  lease_expires_at = now() + interval '120 seconds'
where id = (select job_id from repair_state);

select is(public.reserve_deep_research_operation(
  (select job_id from repair_state), 'repair-worker-b', 2,
  'f3000000-0000-4000-8000-000000000001', 'paid:reserved-before-start',
  'evaluate_evidence', 'r2c', repeat('a',64), 0.0500
) ->> 'status', 'reserved', 'new lease recovers a reservation that never started');
select is((public.reserve_deep_research_operation(
  (select job_id from repair_state), 'repair-worker-b', 2,
  'f3000000-0000-4000-8000-000000000001', 'paid:reserved-before-start',
  'evaluate_evidence', 'r2c', repeat('a',64), 0.0500
) ->> 'recovered')::boolean, false,
  'replay under the recovered lease is stable');
select is((select retry_count from public.research_operations), 1,
  'recovery increments retry metadata exactly once');
select is((select count(*) from public.research_budget_ledger
  where event_type = 'reserved'), 1::bigint,
  'recovery does not duplicate the reservation ledger entry');
select is((select reserved_usd from public.research_run_budgets), 0.0500::numeric,
  'recovery does not consume the reservation twice');
select is(public.start_deep_research_operation(
  (select job_id from repair_state), 'repair-worker-a', 1,
  'f3000000-0000-4000-8000-000000000001'
), false, 'stale Worker cannot start the recovered operation');
select ok(public.start_deep_research_operation(
  (select job_id from repair_state), 'repair-worker-b', 2,
  'f3000000-0000-4000-8000-000000000001'
), 'new lease can start the recovered operation');
select is(public.settle_deep_research_operation(
  (select job_id from repair_state), 'repair-worker-b', 2,
  'f3000000-0000-4000-8000-000000000001', 'completed', 0.0300, null, null
) ->> 'status', 'completed', 'recovered operation settles once');
select is((select actual_usd from public.research_run_budgets), 0.0300::numeric,
  'actual cost replaces the reservation');
select is((select amount_usd from public.research_budget_ledger
  where event_type = 'released'), 0.0200::numeric,
  'unused reservation is explicitly released');
select is((public.settle_deep_research_operation(
  (select job_id from repair_state), 'repair-worker-b', 2,
  'f3000000-0000-4000-8000-000000000001', 'completed', 0.0300, null, null
) ->> 'idempotent')::boolean, true, 'settlement replay is idempotent');
select is((select count(*) from public.research_budget_ledger), 3::bigint,
  'settlement replay adds no ledger rows');

select is(public.record_deep_research_result(
  (select job_id from repair_state), 'repair-worker-b', 2,
  'verified_partial', 'max_rounds_reached', 'Verified partial', 'Partial body',
  '[{"text":"Verified claim","uncertainty":"Partial","citations":[]}]'::jsonb,
  '{"gate":"passed","published":false}'::jsonb, repeat('b',64)
) ->> 'resultStatus', 'verified_partial', 'verified partial result is persisted');
select is(public.complete_deep_research_without_publication(
  (select job_id from repair_state), 'repair-worker-b', 2,
  'qualified_partial', 'max_rounds_reached'
) ->> 'runStatus', 'partial_ready', 'verified partial receives a truthful status');
select is((select output_note_id from public.research_runs
  where id = (select run_id from repair_state)), null::uuid,
  'verified partial does not claim a published draft');
select is((select count(*) from public.notes where title = 'Verified partial'), 0::bigint,
  'verified partial creates no Note');
select is((select status from public.research_runs
  where id = (select run_id from repair_state)), 'partial_ready',
  'partial result is distinguishable from draft_ready');

select * from finish();
rollback;
