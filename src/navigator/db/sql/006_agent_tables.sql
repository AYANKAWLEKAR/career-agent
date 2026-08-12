-- 006: agent observability and write-back tables.

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'TOOL_LOG';
  if l_count = 0 then
    execute immediate q'[
      create table tool_log (
        log_id         number generated always as identity primary key,
        thread_id      varchar2(100) not null,
        turn_seq       number not null,
        call_type      varchar2(50) not null
                       constraint tool_log_call_type_ck
                       check (call_type in (
                         'vector_search_occupations', 'hybrid_search_credentials',
                         'graph_traversal_routes', 'spatial_query_metros',
                         'relational_query_wages', 'relational_query_programs',
                         'llm_parse', 'llm_compose', 'llm_judge', 'plan_writeback')),
        inputs         json not null,
        outputs        json,
        result_count   number,
        top_similarity number(6,4),
        latency_ms     number not null,
        model_name     varchar2(100),
        provider       varchar2(50),
        cost_usd       number(12,6),
        status         varchar2(20)
                       constraint tool_log_status_ck
                       check (status in ('success', 'failure', 'timeout', 'fallback')),
        error_message  varchar2(4000),
        created_at     timestamp with time zone default systimestamp not null
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'TOOL_LOG_THREAD_TURN_IX';
  if l_count = 0 then
    execute immediate 'create index tool_log_thread_turn_ix on tool_log (thread_id, turn_seq)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'TOOL_LOG_CALL_TYPE_IX';
  if l_count = 0 then
    execute immediate 'create index tool_log_call_type_ix on tool_log (call_type)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'TOOL_LOG_CREATED_IX';
  if l_count = 0 then
    execute immediate 'create index tool_log_created_ix on tool_log (created_at desc)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'SAVED_PLANS';
  if l_count = 0 then
    execute immediate q'[
      create table saved_plans (
        plan_id               number generated always as identity primary key,
        user_ref              varchar2(200) not null,
        thread_id             varchar2(100),
        profile_summary       clob not null,
        target_occupation_id  number
                              constraint saved_plans_target_fk
                              references occupations (occupation_id),
        route                 json,
        ruled_out             json,
        geographic_constraint json,
        confirmed_at          timestamp with time zone,
        embedding             vector(384, float32),
        created_at            timestamp with time zone default systimestamp not null,
        updated_at            timestamp with time zone default systimestamp not null
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'SAVED_PLANS_USER_IX';
  if l_count = 0 then
    execute immediate 'create index saved_plans_user_ix on saved_plans (user_ref)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'RETRIEVAL_EVIDENCE';
  if l_count = 0 then
    execute immediate q'[
      create table retrieval_evidence (
        evidence_id     number generated always as identity primary key,
        thread_id       varchar2(100) not null,
        source_type     varchar2(50) not null
                        constraint retr_evidence_type_ck
                        check (source_type in
                          ('occupation', 'credential', 'program', 'metro', 'route_node')),
        source_id       number not null,
        retrieval_score number(6,4),
        used_in_plan    char(1) default 'N' not null
                        constraint retr_evidence_used_ck check (used_in_plan in ('Y', 'N')),
        user_confirmed  char(1) default 'N' not null
                        constraint retr_evidence_confirmed_ck check (user_confirmed in ('Y', 'N')),
        created_at      timestamp with time zone default systimestamp not null
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'RETR_EVIDENCE_THREAD_IX';
  if l_count = 0 then
    execute immediate 'create index retr_evidence_thread_ix on retrieval_evidence (thread_id)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'RETR_EVIDENCE_SOURCE_IX';
  if l_count = 0 then
    execute immediate
      'create index retr_evidence_source_ix on retrieval_evidence (source_type, source_id)';
  end if;
end;
/
