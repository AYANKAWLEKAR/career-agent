-- 009: allow 'work_style' in skill_elements.element_type.
--
-- O*NET has not rated eight in-scope occupations -- Data Scientists and Financial
-- and Investment Analysts among them -- on skills, abilities, knowledge, or work
-- activities, so they had no skill vector and no derived edges. Those occupations
-- do carry Work Styles ratings, which is the only quantitative signal available
-- for them, so work styles become a fifth element type.
--
-- Work styles are scored on WI ("Work Styles Impact", -3..3) rather than the
-- IM/LV pair the other four domains use; build_graph.py maps WI onto importance.

declare
  l_count number;
begin
  select count(*) into l_count from user_constraints
  where constraint_name = 'SKILL_ELEMENTS_TYPE_CK';
  if l_count > 0 then
    execute immediate 'alter table skill_elements drop constraint skill_elements_type_ck';
  end if;
  execute immediate q'[
    alter table skill_elements add constraint skill_elements_type_ck
      check (element_type in ('skill', 'ability', 'knowledge', 'work_activity', 'work_style'))
  ]';
end;
/
