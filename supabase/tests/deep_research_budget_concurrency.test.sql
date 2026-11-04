-- This test commits an isolated fixture because two independent dblink sessions
-- must observe the same run. Cleanup is restricted to the fixed test identity.

create extension if not exists pgtap with schema extensions;
create temporary table deep_budget_dblink_state (was_present boolean not null);
insert into deep_budget_dblink_state
select exists (select 1 from pg_extension where extname = 'dblink');
create extension if not exists dblink with schema extensions;
set search_path = public, extensions;

select plan(16);

delete from auth.users where id = 'f4000000-0000-4000-8000-000000000001';
insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values (
  '00000000-0000-0000-0000-000000000000',
  'f4000000-0000-4000-8000-000000000001',
  'authenticated', 'authenticated', 'deep-budget-concurrency@example.test', '', now(),
  '{"provider":"email","providers":["email"]}'::jsonb, '{}'::jsonb, now(), now()
);
insert into public.deep_research_account_settings (
  user_id, max_cost_usd, daily_research_limit_usd, monthly_research_limit_usd
) values ('f4000000-0000-4000-8000-000000000001', 0.5000, 0.3000, 10.0000);

set role authenticated;
set request.jwt.claim.sub = 'f4000000-0000-4000-8000-000000000001';
select public.start_deep_research(
  'Prove atomic Deep Research budget reservations',
  'f4000000-0000-4000-8000-000000000002', null, 'disabled',
  '{"maxRounds":3,"maxQueries":3,"maxCostUsd":0.5,"maxDurationSeconds":300}'::jsonb,
  null
);
reset role;
set role service_role;
select public.claim_job('deep-budget-owner', 'research', 120);
reset role;

create temporary table deep_budget_sessions (worker_id text primary key, backend_pid integer);
create temporary table deep_budget_results (worker_id text primary key, reservation_status text not null);
create temporary table deep_budget_settlements (worker_id text primary key, idempotent boolean not null);

do $$
declare
  connection_string text := format(
    'host=%s port=%s dbname=%L user=postgres password=postgres',
    inet_server_addr(), inet_server_port(), current_database()
  );
begin
  perform extensions.dblink_connect('deep_budget_one', connection_string);
  perform extensions.dblink_connect('deep_budget_two', connection_string);
end;
$$;

insert into deep_budget_sessions
select 'worker-one', backend_pid
from extensions.dblink('deep_budget_one', 'select pg_backend_pid()')
  as session(backend_pid integer);
insert into deep_budget_sessions
select 'worker-two', backend_pid
from extensions.dblink('deep_budget_two', 'select pg_backend_pid()')
  as session(backend_pid integer);

select isnt(
  (select backend_pid from deep_budget_sessions where worker_id = 'worker-one'),
  (select backend_pid from deep_budget_sessions where worker_id = 'worker-two'),
  'budget contenders use two independent PostgreSQL sessions'
);

do $$
begin
  perform extensions.dblink_exec('deep_budget_one', 'begin');
  perform extensions.dblink_exec('deep_budget_two', 'begin');
  perform extensions.dblink_exec('deep_budget_one', 'set local role service_role');
  perform extensions.dblink_exec('deep_budget_two', 'set local role service_role');
  perform extensions.dblink_exec(
    'deep_budget_two', 'set local statement_timeout = ''5s'''
  );
end;
$$;

-- The first reservation returns while its transaction remains open, retaining
-- the locked account/run budget rows. The second session then overlaps and
-- must wait for that transaction instead of reading a stale budget snapshot.
insert into deep_budget_results
select 'worker-one', reservation_status
from extensions.dblink(
  'deep_budget_one',
  $query$
    select public.reserve_deep_research_operation(
      (select job_id from public.research_runs
       where user_id = 'f4000000-0000-4000-8000-000000000001'
         and request_key = 'f4000000-0000-4000-8000-000000000002'),
      'deep-budget-owner', 1,
      'f4000000-0000-4000-8000-000000000003', 'concurrent:one',
      'evaluate_evidence', 'r2c', repeat('a',64), 0.3000
    ) ->> 'status'
  $query$
) as reservation(reservation_status text);

select extensions.dblink_send_query(
  'deep_budget_two',
  $query$
    select public.reserve_deep_research_operation(
      (select job_id from public.research_runs
       where user_id = 'f4000000-0000-4000-8000-000000000001'
         and request_key = 'f4000000-0000-4000-8000-000000000002'),
      'deep-budget-owner', 1,
      'f4000000-0000-4000-8000-000000000004', 'concurrent:two',
      'evaluate_evidence', 'r2c', repeat('b',64), 0.3000
    ) ->> 'status'
  $query$
);

select is(extensions.dblink_is_busy('deep_budget_two'), 1,
  'second reservation overlaps the uncommitted first transaction');
select extensions.dblink_exec('deep_budget_one', 'commit');

insert into deep_budget_results
select 'worker-two', reservation_status
from extensions.dblink_get_result('deep_budget_two')
  as reservation(reservation_status text);
do $$
begin
  perform * from extensions.dblink_get_result('deep_budget_two')
    as drained(reservation_status text);
  perform extensions.dblink_exec('deep_budget_two', 'commit');
end;
$$;

select is((select reservation_status from deep_budget_results where worker_id = 'worker-one'),
  'reserved', 'first transaction reserves its atomic budget');
select is((select reservation_status from deep_budget_results where worker_id = 'worker-two'),
  'budget_exhausted', 'second transaction observes the committed reservation');
select is((select count(*) from public.research_operations
  where user_id = 'f4000000-0000-4000-8000-000000000001'), 1::bigint,
  'only one concurrent operation is created');
select is((select reserved_usd from public.research_run_budgets
  where user_id = 'f4000000-0000-4000-8000-000000000001'), 0.3000::numeric,
  'run budget contains exactly one reservation');
select is((select reserved_usd from public.research_daily_budgets
  where user_id = 'f4000000-0000-4000-8000-000000000001'), 0.3000::numeric,
  'daily aggregate contains exactly one reservation');
select is((select count(*) from public.research_budget_ledger
  where user_id = 'f4000000-0000-4000-8000-000000000001'
    and event_type = 'reserved'), 1::bigint,
  'ledger contains exactly one reservation event');

select ok(public.start_deep_research_operation(
  (select job_id from public.research_runs
   where user_id = 'f4000000-0000-4000-8000-000000000001'
     and request_key = 'f4000000-0000-4000-8000-000000000002'),
  'deep-budget-owner', 1, 'f4000000-0000-4000-8000-000000000003'
), 'winning reservation crosses the execution boundary once');

do $$
begin
  perform extensions.dblink_exec('deep_budget_one', 'begin');
  perform extensions.dblink_exec('deep_budget_two', 'begin');
  perform extensions.dblink_exec('deep_budget_one', 'set local role service_role');
  perform extensions.dblink_exec('deep_budget_two', 'set local role service_role');
  perform extensions.dblink_exec(
    'deep_budget_two', 'set local statement_timeout = ''5s'''
  );
end;
$$;

insert into deep_budget_settlements
select 'worker-one', idempotent
from extensions.dblink(
  'deep_budget_one',
  $query$
    select (public.settle_deep_research_operation(
      (select job_id from public.research_runs
       where user_id = 'f4000000-0000-4000-8000-000000000001'
         and request_key = 'f4000000-0000-4000-8000-000000000002'),
      'deep-budget-owner', 1, 'f4000000-0000-4000-8000-000000000003',
      'completed', 0.2000, null, null
    ) ->> 'idempotent')::boolean
  $query$
) as settlement(idempotent boolean);

select extensions.dblink_send_query(
  'deep_budget_two',
  $query$
    select (public.settle_deep_research_operation(
      (select job_id from public.research_runs
       where user_id = 'f4000000-0000-4000-8000-000000000001'
         and request_key = 'f4000000-0000-4000-8000-000000000002'),
      'deep-budget-owner', 1, 'f4000000-0000-4000-8000-000000000003',
      'completed', 0.2000, null, null
    ) ->> 'idempotent')::boolean
  $query$
);
select is(extensions.dblink_is_busy('deep_budget_two'), 1,
  'second settlement overlaps the uncommitted first settlement');
select extensions.dblink_exec('deep_budget_one', 'commit');
insert into deep_budget_settlements
select 'worker-two', idempotent
from extensions.dblink_get_result('deep_budget_two')
  as settlement(idempotent boolean);
do $$
begin
  perform * from extensions.dblink_get_result('deep_budget_two')
    as drained(idempotent boolean);
  perform extensions.dblink_exec('deep_budget_two', 'commit');
end;
$$;

select is((select idempotent from deep_budget_settlements where worker_id = 'worker-one'),
  false, 'first settlement performs the accounting mutation');
select is((select idempotent from deep_budget_settlements where worker_id = 'worker-two'),
  true, 'concurrent duplicate settlement is an idempotent replay');
select results_eq(
  $$select reserved_usd, actual_usd, uncertain_usd
    from public.research_run_budgets
    where user_id = 'f4000000-0000-4000-8000-000000000001'$$,
  $$values (0.0000::numeric, 0.2000::numeric, 0.0000::numeric)$$,
  'concurrent settlement leaves the exact final run balance'
);
select results_eq(
  $$select reserved_usd, actual_usd, uncertain_usd
    from public.research_daily_budgets
    where user_id = 'f4000000-0000-4000-8000-000000000001'$$,
  $$values (0.0000::numeric, 0.2000::numeric, 0.0000::numeric)$$,
  'settlement leaves the exact final daily aggregate balance'
);
select is((public.reserve_deep_research_operation(
  (select job_id from public.research_runs
   where user_id = 'f4000000-0000-4000-8000-000000000001'
     and request_key = 'f4000000-0000-4000-8000-000000000002'),
  'deep-budget-owner', 1,
  'f4000000-0000-4000-8000-000000000005', 'daily:exhausted',
  'evaluate_evidence', 'r2c', repeat('c',64), 0.1100
) ->> 'status'), 'budget_exhausted',
  'daily aggregate rejects work that still fits the run and monthly budgets');
select is((select count(*) from public.research_budget_ledger
  where user_id = 'f4000000-0000-4000-8000-000000000001'), 3::bigint,
  'duplicate settlement creates no additional ledger event');

do $$
begin
  perform extensions.dblink_disconnect('deep_budget_one');
  perform extensions.dblink_disconnect('deep_budget_two');
end;
$$;

-- The ledger is deliberately append-only, including against cascading deletes.
-- The database owner temporarily disables only that guard inside one transaction
-- to remove this exact disposable fixture; rollback restores the trigger on error.
begin;
alter table public.research_budget_ledger
  disable trigger research_budget_ledger_append_only;
delete from auth.users where id = 'f4000000-0000-4000-8000-000000000001';
alter table public.research_budget_ledger
  enable trigger research_budget_ledger_append_only;
commit;

do $$
begin
  if not (select was_present from deep_budget_dblink_state) then
    execute 'drop extension dblink';
  end if;
end;
$$;

select * from finish();
