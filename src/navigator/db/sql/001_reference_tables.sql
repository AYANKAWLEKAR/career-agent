-- 001: staging and ingest bookkeeping tables.
-- staging_raw is deliberately dumb: no foreign keys, no constraints beyond the
-- source check, because its job is to accept whatever arrives.

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'STAGING_RAW';
  if l_count = 0 then
    execute immediate q'[
      create table staging_raw (
        staging_id  number generated always as identity primary key,
        source      varchar2(50) not null
                    constraint staging_raw_source_ck
                    check (source in ('onet', 'bls_oews', 'scorecard', 'careeronestop')),
        record_key  varchar2(200) not null,
        payload     json,
        batch_id    varchar2(100),
        loaded_at   timestamp with time zone default systimestamp not null
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'STAGING_RAW_SOURCE_IX';
  if l_count = 0 then
    execute immediate 'create index staging_raw_source_ix on staging_raw (source)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'STAGING_RAW_BATCH_IX';
  if l_count = 0 then
    execute immediate 'create index staging_raw_batch_ix on staging_raw (batch_id)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'INGEST_RUNS';
  if l_count = 0 then
    execute immediate q'[
      create table ingest_runs (
        run_id        number generated always as identity primary key,
        source        varchar2(50) not null,
        batch_id      varchar2(100),
        started_at    timestamp with time zone default systimestamp not null,
        completed_at  timestamp with time zone,
        record_count  number,
        status        varchar2(20) not null
                      constraint ingest_runs_status_ck
                      check (status in ('running', 'succeeded', 'failed')),
        error_message varchar2(4000)
      )
    ]';
  end if;
end;
/
