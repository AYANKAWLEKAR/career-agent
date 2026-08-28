-- 011: user profile, experiences, and stated requirements (design section 6.1).
--
-- The user tells the system what they have done in natural language -- effectively a
-- resume -- and llm_parse extracts from it. raw_text is retained permanently: the
-- embedding, the matched occupation, and the extracted requirements are all lossy
-- derivations, and when a path looks wrong the first question is always what the
-- user actually said.
--
-- Two multi-valued extractions are stored differently, on purpose:
--
--   education_majors is a JSON array of short labels. Majors carry signal ("Computer
--   Science" versus "Biology" strongly implies a different starting occupation) but
--   they are a handful of short strings, so they contribute through embedding_source
--   rather than earning vectors of their own.
--
--   Experiences get a table with one row and one vector each. Averaging a resume's
--   experiences into a single profile vector lands it between all of them and near
--   none -- someone who did a web development internship, a genomics research
--   assistantship, and calculus tutoring is not 'the average of those three'.
--   Embedding each separately lets A-inference match every experience against the
--   occupation vectors and take the strongest signal rather than the blurred mean.
--
-- Skills are deliberately absent here: they are profile_requirements rows of
-- requirement_type 'skill', matched against the shared vocabulary in 010. College
-- major likewise canonicalizes into 'education_field' rows for gap math; the
-- education_majors list is the stated layer that those rows derive from.

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'USER_PROFILES';
  if l_count = 0 then
    execute immediate q'[
      create table user_profiles (
        profile_id            number generated always as identity primary key,
        user_ref              varchar2(200) not null,
        raw_text              clob not null,
        -- structured extraction, produced by llm_parse
        current_title         varchar2(300),
        years_experience      number(4,1),
        education_level       varchar2(50),
        education_majors      json,
        inferred_job_zone     number constraint user_profiles_zone_ck
                              check (inferred_job_zone between 1 and 5),
        location_text         varchar2(200),
        -- matching. embedding_source is the composed document that was embedded,
        -- kept inspectable for the same reason as occupations.embedding_source:
        -- when retrieval surprises you, you need to read exactly what went in.
        embedding_source      clob,
        embedding             vector(384, float32),
        matched_occupation_id number
                              constraint user_profiles_matched_fk
                              references occupations (occupation_id),
        match_similarity      number(6,4),
        created_at            timestamp with time zone default systimestamp not null,
        updated_at            timestamp with time zone default systimestamp not null,
        constraint user_profiles_majors_ck check (education_majors is json)
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'PROFILE_EXPERIENCES';
  if l_count = 0 then
    execute immediate q'[
      create table profile_experiences (
        experience_id   number generated always as identity primary key,
        profile_id      number not null
                        constraint profile_exp_profile_fk
                        references user_profiles (profile_id) on delete cascade,
        -- resume order is meaningful: recency is signal, so it is preserved rather
        -- than left to whatever order rows happen to come back in
        ordinal         number not null,
        raw_text        clob not null,
        -- structured extraction over the entry above; all nullable because a user
        -- may describe an experience without naming an employer or a duration
        title           varchar2(300),
        organization    varchar2(300),
        duration_months number(5),
        embedding       vector(384, float32),
        created_at      timestamp with time zone default systimestamp not null,
        constraint profile_exp_ordinal_uq unique (profile_id, ordinal)
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'PROFILE_EXP_PROFILE_IX';
  if l_count = 0 then
    execute immediate 'create index profile_exp_profile_ix on profile_experiences (profile_id)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'PROFILE_REQUIREMENTS';
  if l_count = 0 then
    execute immediate q'[
      create table profile_requirements (
        profile_requirement_id number generated always as identity primary key,
        profile_id       number not null
                         constraint profile_reqs_profile_fk
                         references user_profiles (profile_id) on delete cascade,
        -- nullable while raw_text is not: a user will state things with no
        -- vocabulary entry ("bedside manner"). Requiring the mapping would force
        -- either discarding those or inventing bad matches; unmapped rows are kept
        -- for later reconciliation and excluded from coverage math.
        requirement_id   number
                         constraint profile_reqs_item_fk
                         references requirement_items (requirement_id),
        -- which experience this was drawn from, when it came from one
        experience_id    number
                         constraint profile_reqs_experience_fk
                         references profile_experiences (experience_id) on delete cascade,
        raw_text         varchar2(300) not null,
        requirement_type varchar2(30),
        ordinal_value    number,
        evidence         varchar2(500),
        source           varchar2(30) constraint profile_reqs_source_ck
                         check (source in ('stated', 'inferred_from_title',
                                           'inferred_from_text', 'inferred_from_experience')),
        created_at       timestamp with time zone default systimestamp not null
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'PROFILE_REQS_PROFILE_IX';
  if l_count = 0 then
    execute immediate 'create index profile_reqs_profile_ix on profile_requirements (profile_id)';
  end if;
end;
/
