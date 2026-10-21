-- Supabase may provision explicit API-role EXECUTE grants when a function is
-- created. Reset every API role before granting only the intended callers.
revoke all on function public.enqueue_source_note_generation(uuid)
  from public, anon, authenticated, service_role;
grant execute on function public.enqueue_source_note_generation(uuid)
  to authenticated, service_role;

revoke all on function public.update_note_content(uuid, integer, text, text)
  from public, anon, authenticated, service_role;
grant execute on function public.update_note_content(uuid, integer, text, text)
  to authenticated, service_role;

revoke all on function public.get_source_note_context(uuid, text, bigint)
  from public, anon, authenticated, service_role;
grant execute on function public.get_source_note_context(uuid, text, bigint)
  to service_role;

revoke all on function public.publish_source_note(
  uuid, text, bigint, text, text, text, text, text, text, jsonb
) from public, anon, authenticated, service_role;
grant execute on function public.publish_source_note(
  uuid, text, bigint, text, text, text, text, text, text, jsonb
) to service_role;
