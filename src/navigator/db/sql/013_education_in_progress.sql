-- 013: record whether the profile's stated education is still in progress.
--
-- The start-zone ceiling depends on COMPLETED preparation: a bachelor's in
-- progress supports a zone-3 start, a completed bachelor's zone 4. Without
-- this flag a sophomore parses as bachelor's-complete and A-inference seeds
-- students into zone-5 occupations -- which are terminal (the graph never
-- routes downward), so the exact audience this product serves got the one
-- output with no ladder in it.

declare
  l_count number;
begin
  select count(*) into l_count from user_tab_columns
  where table_name = 'USER_PROFILES' and column_name = 'EDUCATION_IN_PROGRESS';
  if l_count = 0 then
    execute immediate q'[
      alter table user_profiles add (
        education_in_progress char(1) default 'N' not null
          constraint user_profiles_edu_prog_ck
          check (education_in_progress in ('Y', 'N'))
      )
    ]';
  end if;
end;
/
