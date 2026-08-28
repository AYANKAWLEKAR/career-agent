-- 012: inspectable embedding input for occupations (design section 3).
--
-- The embedded text is a real column rather than an inline expression on purpose:
-- when retrieval returns something surprising, you need to read exactly what was
-- embedded. This is the single highest-value debugging affordance in the system.

declare
  l_count number;
begin
  select count(*) into l_count from user_tab_columns
  where table_name = 'OCCUPATIONS' and column_name = 'EMBEDDING_SOURCE';
  if l_count = 0 then
    execute immediate 'alter table occupations add (embedding_source clob)';
  end if;
end;
/
