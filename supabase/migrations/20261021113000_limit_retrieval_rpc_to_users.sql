-- Retrieval is always on behalf of an authenticated user so auth.uid() remains
-- the tenant boundary. Backend secrets do not need a direct execution grant.
revoke execute on function public.search_knowledge(text, integer, integer)
  from service_role;
