begin;
create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;
select plan(20);

select ok(
  not has_function_privilege('anon', 'public.record_opportunity_feedback(uuid,text)', 'execute'),
  'anonymous users cannot submit opportunity feedback'
);
select ok(
  has_function_privilege('authenticated', 'public.record_opportunity_feedback(uuid,text)', 'execute'),
  'authenticated users can submit scoped opportunity feedback'
);
select ok(
  not has_function_privilege('authenticated', 'public.enqueue_opportunity_ranking(uuid,date)', 'execute'),
  'authenticated users cannot enqueue automatic ranking directly'
);
select ok(
  has_function_privilege('service_role', 'public.enqueue_opportunity_ranking(uuid,date)', 'execute'),
  'service role can enqueue daily ranking'
);
select ok(
  not has_function_privilege('authenticated', 'public.enqueue_due_opportunity_rankings()', 'execute'),
  'authenticated users cannot trigger the daily scheduler'
);
select ok(
  has_function_privilege('service_role', 'public.enqueue_due_opportunity_rankings()', 'execute'),
  'service role can trigger the daily scheduler'
);
select ok(
  (select relrowsecurity and relforcerowsecurity from pg_class
   where oid = 'public.research_opportunities'::regclass),
  'Research Opportunities use forced RLS'
);
select ok(
  (select relrowsecurity and relforcerowsecurity from pg_class
   where oid = 'public.opportunity_events'::regclass),
  'Opportunity Events use forced RLS'
);
select ok(
  (select relrowsecurity and relforcerowsecurity from pg_class
   where oid = 'public.automatic_budget_periods'::regclass),
  'automatic budget periods use forced RLS'
);

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values
  ('00000000-0000-0000-0000-000000000000', 'a1000000-0000-4000-8000-000000000011',
   'authenticated', 'authenticated', 'opportunity-a@example.test', '', now(),
   '{"provider":"email","providers":["email"]}', '{}', now(), now()),
  ('00000000-0000-0000-0000-000000000000', 'a1000000-0000-4000-8000-000000000012',
   'authenticated', 'authenticated', 'opportunity-b@example.test', '', now(),
   '{"provider":"email","providers":["email"]}', '{}', now(), now());

set local role service_role;
with first_enqueue as (
  select public.enqueue_opportunity_ranking(
    'a1000000-0000-4000-8000-000000000011', date '2026-10-21'
  ) result
), second_enqueue as (
  select public.enqueue_opportunity_ranking(
    'a1000000-0000-4000-8000-000000000011', date '2026-10-21'
  ) result
  from first_enqueue
)
select is(
  (select result ->> 'jobId' from first_enqueue),
  (select result ->> 'jobId' from second_enqueue),
  'daily ranking enqueue is idempotent per user and date'
);
reset role;

insert into public.research_opportunities (
  id, user_id, question, rationale, score, signal_types, semantic_key,
  latest_evidence_at
) values
  ('a2000000-0000-4000-8000-000000000011', 'a1000000-0000-4000-8000-000000000011',
   'What supports claim A?', 'Claim A needs independent evidence.', 75,
   array['claim_gap'], repeat('a', 64), now()),
  ('a2000000-0000-4000-8000-000000000012', 'a1000000-0000-4000-8000-000000000012',
   'What supports claim B?', 'Claim B needs independent evidence.', 70,
   array['claim_gap'], repeat('b', 64), now());

set local role authenticated;
set local request.jwt.claim.sub = 'a1000000-0000-4000-8000-000000000011';
select is(
  (select count(*) from public.research_opportunities), 1::bigint,
  'user reads only owned opportunities'
);
select is(
  public.record_opportunity_feedback(
    'a2000000-0000-4000-8000-000000000011', 'skip'
  ) ->> 'status',
  'snoozed', 'skip feedback snoozes the suggestion'
);
select ok(
  (select cooldown_until > now() + interval '6 days'
   from public.research_opportunities
   where id = 'a2000000-0000-4000-8000-000000000011'),
  'skip feedback establishes the seven-day cooldown'
);
select is(
  (select count(*) from public.opportunity_events), 1::bigint,
  'feedback appends one owned event'
);
select is(
  (select count(*) from public.research_runs), 0::bigint,
  'feedback never creates a Research Run'
);
select is(
  (select count(*) from public.jobs where job_type = 'execute_research'), 0::bigint,
  'feedback never creates a Manual Research Job'
);

set local request.jwt.claim.sub = 'a1000000-0000-4000-8000-000000000012';
select is(
  (select count(*) from public.research_opportunities), 1::bigint,
  'second user reads only the second user suggestion'
);
select throws_matching(
  $$select public.record_opportunity_feedback(
    'a2000000-0000-4000-8000-000000000011', 'not_interested'
  )$$,
  'opportunity not found', 'second user cannot modify the first user suggestion'
);

reset role;
select throws_matching(
  $$insert into public.automatic_budget_periods (user_id, period_date, spent_usd)
    values ('a1000000-0000-4000-8000-000000000011', current_date, 0.500001)$$,
  'automatic_budget_periods_spent_usd_check', 'database rejects automatic spend over $0.50'
);
select throws_matching(
  $$insert into public.research_opportunities (
      user_id, question, rationale, score, signal_types, semantic_key,
      latest_evidence_at
    ) values (
      'a1000000-0000-4000-8000-000000000011', 'Duplicate?', 'Duplicate semantic key.',
      60, array['claim_gap'], repeat('a', 64), now()
    )$$,
  'research_opportunities_user_semantic_unique',
  'database deduplicates a semantic opportunity within one user'
);

select * from finish();
rollback;
