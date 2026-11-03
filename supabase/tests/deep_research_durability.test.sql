begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(23);

select has_table('public', 'research_checkpoints', 'durable checkpoints exist');
select has_table('public', 'research_operations', 'operation ledger state exists');
select has_table('public', 'research_budget_ledger', 'append-only budget ledger exists');
select has_table('public', 'deep_research_evidence_rounds', 'Evidence round lineage exists');
select ok((select relrowsecurity and relforcerowsecurity from pg_class where oid = 'public.research_checkpoints'::regclass), 'checkpoints use forced RLS');
select ok((select relrowsecurity and relforcerowsecurity from pg_class where oid = 'public.research_operations'::regclass), 'operations use forced RLS');
select ok(not has_function_privilege('authenticated',
  'public.reserve_deep_research_operation(uuid,text,bigint,uuid,text,text,text,text,numeric)',
  'execute'), 'authenticated clients cannot reserve Worker operations');
select ok(has_function_privilege('service_role',
  'public.reserve_deep_research_operation(uuid,text,bigint,uuid,text,text,text,text,numeric)',
  'execute'), 'service role can reserve fenced operations');

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values (
  '00000000-0000-0000-0000-000000000000', 'd1000000-0000-4000-8000-000000000001',
  'authenticated', 'authenticated', 'deep-durable@example.test', '', now(),
  '{"provider":"email","providers":["email"]}', '{}', now(), now()
);

create temporary table deep_state (run_id uuid, job_id uuid);
grant select, update, insert on deep_state to authenticated, service_role;
set local role authenticated;
set local request.jwt.claim.sub = 'd1000000-0000-4000-8000-000000000001';
insert into deep_state
select (result ->> 'id')::uuid, (result ->> 'jobId')::uuid
from (select public.start_deep_research(
  'Durable budget test request',
  'd2000000-0000-4000-8000-000000000001',
  null, 'disabled',
  '{"maxRounds":3,"maxQueries":3,"maxCostUsd":0.2,"maxDurationSeconds":300}'::jsonb,
  null
) result) started;
reset role;
set local role service_role;

select is(
  (select status from public.claim_job('deep-worker-one', 'research', 120)),
  'running', 'Deep Research job is claimed with a lease'
);
select is(
  public.reserve_deep_research_operation(
    (select job_id from deep_state), 'deep-worker-one', 1,
    'd3000000-0000-4000-8000-000000000001', 'round:1:evaluate',
    'evaluate_evidence', 'r2c', repeat('a', 64), 0.0500
  ) ->> 'status',
  'reserved', 'first atomic reservation succeeds'
);
select is(
  public.reserve_deep_research_operation(
    (select job_id from deep_state), 'deep-worker-one', 1,
    'd3000000-0000-4000-8000-000000000001', 'round:1:evaluate',
    'evaluate_evidence', 'r2c', repeat('a', 64), 0.0500
  ) ->> 'status',
  'reserved', 'reservation replay is idempotent'
);
select is(
  (select count(*) from public.research_operations), 1::bigint,
  'idempotent replay creates one operation'
);
select is(
  public.reserve_deep_research_operation(
    (select job_id from deep_state), 'deep-worker-one', 1,
    'd3000000-0000-4000-8000-000000000002', 'round:2:evaluate',
    'evaluate_evidence', 'r2c', repeat('b', 64), 0.0600
  ) ->> 'status',
  'budget_exhausted', 'protected final reserve prevents over-reservation'
);
select is(
  (select reserved_usd from public.research_run_budgets), 0.0500::numeric,
  'rejected reservation does not mutate the run budget'
);
select ok(
  public.start_deep_research_operation(
    (select job_id from deep_state), 'deep-worker-one', 1,
    'd3000000-0000-4000-8000-000000000001'
  ), 'reserved operation transitions to running'
);

update public.jobs set
  lease_owner = 'deep-worker-two', lease_generation = 2,
  lease_expires_at = now() + interval '120 seconds'
where id = (select job_id from deep_state);

select is(
  public.reserve_deep_research_operation(
    (select job_id from deep_state), 'deep-worker-two', 2,
    'd3000000-0000-4000-8000-000000000001', 'round:1:evaluate',
    'evaluate_evidence', 'r2c', repeat('a', 64), 0.0500
  ) ->> 'status',
  'outcome_unknown', 'new lease fences a possibly paid running operation'
);
select is(
  (select uncertain_usd from public.research_run_budgets), 0.0500::numeric,
  'uncertain paid outcome remains charged conservatively'
);
select is(
  (select count(*) from public.research_budget_ledger where event_type = 'uncertain'),
  1::bigint, 'uncertain transition is appended to the ledger'
);
select is(
  public.reserve_deep_research_operation(
    (select job_id from deep_state), 'deep-worker-one', 1,
    'd3000000-0000-4000-8000-000000000003', 'stale-attempt',
    'search_internal', 'r1', repeat('c', 64), 0
  ), null::jsonb, 'stale lease cannot reserve an operation'
);
select throws_ok(
  $$update public.research_budget_ledger set amount_usd = 0$$,
  '23000', 'research budget ledger is append-only',
  'budget ledger rows cannot be updated'
);
select throws_ok(
  $$delete from public.research_budget_ledger$$,
  '23000', 'research budget ledger is append-only',
  'budget ledger rows cannot be deleted'
);
select is(
  (select reserved_usd + actual_usd + uncertain_usd from public.research_budget_periods),
  0.0500::numeric, 'account-period accounting remains balanced'
);
select is(
  (select reserved_usd + actual_usd + uncertain_usd from public.research_run_budgets),
  0.0500::numeric, 'run accounting remains balanced'
);

select * from finish();
rollback;
