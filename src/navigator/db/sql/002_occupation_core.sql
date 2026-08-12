-- 002: occupations, tasks, skill elements, and the occupation graph edge table.
--
-- RULE: only one-hop edges are ever inserted into occupation_edges. Multi-hop
-- routes are computed by traversal at query time. Do not precompute paths.
--
-- occupation_skills stores the O*NET "level" rating in level_value, because
-- LEVEL is an Oracle reserved word.

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'OCCUPATIONS';
  if l_count = 0 then
    execute immediate q'[
      create table occupations (
        occupation_id  number generated always as identity primary key,
        soc_code       varchar2(20) not null constraint occupations_soc_uq unique,
        title          varchar2(300) not null,
        description    clob,
        job_zone       number constraint occupations_job_zone_ck
                       check (job_zone between 1 and 5),
        bright_outlook char(1) constraint occupations_bright_ck
                       check (bright_outlook in ('Y', 'N')),
        embedding      vector(384, float32),
        created_at     timestamp with time zone default systimestamp not null,
        updated_at     timestamp with time zone default systimestamp not null
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'OCCUPATIONS_TITLE_IX';
  if l_count = 0 then
    execute immediate 'create index occupations_title_ix on occupations (title)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'TASKS';
  if l_count = 0 then
    execute immediate q'[
      create table tasks (
        task_id        number generated always as identity primary key,
        occupation_id  number not null
                       constraint tasks_occupation_fk
                       references occupations (occupation_id) on delete cascade,
        task_statement clob not null,
        task_type      varchar2(20),
        importance     number(5,2),
        embedding      vector(384, float32),
        created_at     timestamp with time zone default systimestamp not null,
        updated_at     timestamp with time zone default systimestamp not null
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'TASKS_OCCUPATION_IX';
  if l_count = 0 then
    execute immediate 'create index tasks_occupation_ix on tasks (occupation_id)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'SKILL_ELEMENTS';
  if l_count = 0 then
    execute immediate q'[
      create table skill_elements (
        element_id   number generated always as identity primary key,
        element_code varchar2(30) not null constraint skill_elements_code_uq unique,
        element_name varchar2(300) not null,
        element_type varchar2(30) not null
                     constraint skill_elements_type_ck
                     check (element_type in ('skill', 'ability', 'knowledge', 'work_activity')),
        description  clob,
        created_at   timestamp with time zone default systimestamp not null,
        updated_at   timestamp with time zone default systimestamp not null
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'OCCUPATION_SKILLS';
  if l_count = 0 then
    execute immediate q'[
      create table occupation_skills (
        occupation_skill_id number generated always as identity primary key,
        occupation_id       number not null
                            constraint occ_skills_occupation_fk
                            references occupations (occupation_id) on delete cascade,
        element_id          number not null
                            constraint occ_skills_element_fk
                            references skill_elements (element_id) on delete cascade,
        importance          number(5,2),
        level_value         number(5,2),
        created_at          timestamp with time zone default systimestamp not null,
        constraint occ_skills_pair_uq unique (occupation_id, element_id)
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'OCC_SKILLS_OCCUPATION_IX';
  if l_count = 0 then
    execute immediate 'create index occ_skills_occupation_ix on occupation_skills (occupation_id)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'OCC_SKILLS_ELEMENT_IX';
  if l_count = 0 then
    execute immediate 'create index occ_skills_element_ix on occupation_skills (element_id)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'OCCUPATION_EDGES';
  if l_count = 0 then
    execute immediate q'[
      create table occupation_edges (
        edge_id            number generated always as identity primary key,
        src_occupation_id  number not null
                           constraint occ_edges_src_fk
                           references occupations (occupation_id) on delete cascade,
        dst_occupation_id  number not null
                           constraint occ_edges_dst_fk
                           references occupations (occupation_id) on delete cascade,
        edge_type          varchar2(30) not null
                           constraint occ_edges_type_ck
                           check (edge_type in ('related', 'skill_overlap', 'career_ladder')),
        weight             number(6,4),
        source             varchar2(50),
        created_at         timestamp with time zone default systimestamp not null,
        constraint occ_edges_triple_uq unique (src_occupation_id, dst_occupation_id, edge_type)
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'OCC_EDGES_SRC_IX';
  if l_count = 0 then
    execute immediate 'create index occ_edges_src_ix on occupation_edges (src_occupation_id)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'OCC_EDGES_DST_IX';
  if l_count = 0 then
    execute immediate 'create index occ_edges_dst_ix on occupation_edges (dst_occupation_id)';
  end if;
end;
/
