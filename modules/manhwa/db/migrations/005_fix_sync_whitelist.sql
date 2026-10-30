-- 005_fix_sync_whitelist.sql
-- Fix ambiguous column reference "r" in sync_whitelist RPC
-- Rename loop variable from "r" to "elem" to avoid conflict with table alias in return subquery

create or replace function sync_whitelist(p_rows text)
returns setof whitelist
language plpgsql
as $$
declare
  rows_json jsonb := p_rows::jsonb;
  elem jsonb;
begin
  for elem in select jsonb_array_elements(rows_json)
  loop
    insert into whitelist (title_key, title, cover, sources)
    values (
      elem->>'title_key',
      elem->>'title',
      elem->>'cover',
      coalesce(elem->'sources', '[]'::jsonb)
    )
    on conflict (title_key) do update
    set title   = excluded.title,
        cover   = coalesce(excluded.cover, whitelist.cover),
        sources = excluded.sources;
  end loop;
  return query
    select * from whitelist
    where title_key in (
      select r->>'title_key' from jsonb_array_elements(rows_json) as r
    );
end;
$$;
