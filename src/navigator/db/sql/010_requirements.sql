-- 010: the shared requirement vocabulary (design section 6.2).
--
-- Created here because profile_requirements in 011 has a foreign key into it.
-- One canonical vocabulary is shared by occupations and profiles, which is what
-- turns matching into relational algebra rather than a similarity computation.
--
-- This creates the structure only; canonicalization and population are section 6.3.
--
-- skill_elements is NOT replaced. The two vocabularies serve different masters:
-- skill_elements holds O*NET's abstract rated elements and drives edge derivation;
-- requirement_items holds concrete named things and drives user matching.

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'REQUIREMENT_ITEMS';
  if l_count = 0 then
    execute immediate q'[
      create table requirement_items (
        requirement_id   number generated always as identity primary key,
        requirement_type varchar2(30) not null
                         constraint requirement_items_type_ck
                         check (requirement_type in
                           ('tool', 'skill', 'education_level', 'education_field',
                            'experience', 'certification')),
        canonical_text   varchar2(300) not null,
        normalized_key   varchar2(200) not null,
        -- education_level: 1-5 per the job-zone table in 7.1; experience: years;
        -- null for tool, skill, certification, education_field
        ordinal_value    number,
        embedding        vector(384, float32),
        created_at       timestamp with time zone default systimestamp not null,
        updated_at       timestamp with time zone default systimestamp not null,
        constraint requirement_items_uq unique (requirement_type, normalized_key)
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'OCCUPATION_REQUIREMENTS';
  if l_count = 0 then
    execute immediate q'[
      create table occupation_requirements (
        occupation_requirement_id number generated always as identity primary key,
        occupation_id     number not null
                          constraint occ_reqs_occupation_fk
                          references occupations (occupation_id) on delete cascade,
        requirement_id    number not null
                          constraint occ_reqs_item_fk
                          references requirement_items (requirement_id) on delete cascade,
        importance        number(5,2),
        frequency_pct     number(5,2),
        -- rows sharing a non-null group are alternatives: satisfying ANY satisfies the group
        alternative_group number,
        source            varchar2(50) not null,
        created_at        timestamp with time zone default systimestamp not null,
        constraint occ_reqs_pair_uq unique (occupation_id, requirement_id)
      )
    ]';
  end if;
end;
/
