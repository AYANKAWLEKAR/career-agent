-- 014: College Scorecard outcome columns, the CIP vector table, and the
-- major-outcomes JSON Relational Duality view.
--
-- Programs gain the 5/7-year earnings horizons, the typical monthly loan
-- payment, and the award count; cip_codes holds the 4-digit CIP taxonomy with
-- in-database title embeddings so a free-text major ("applied math") resolves
-- to a CIP by vector similarity. The CIP vector index is created by
-- scripts/load_scorecard.py AFTER the embeddings exist (an index built over
-- NULL vectors indexes nothing -- the TASKS_EMBEDDING_VIX lesson).

declare
  procedure add_col(p_name varchar2, p_ddl varchar2) is
    l_count number;
  begin
    select count(*) into l_count from user_tab_columns
    where table_name = 'PROGRAMS' and column_name = p_name;
    if l_count = 0 then
      execute immediate 'alter table programs add (' || p_ddl || ')';
    end if;
  end;
begin
  add_col('MEDIAN_EARNINGS_5YR', 'median_earnings_5yr number(12,2)');
  add_col('MEDIAN_EARNINGS_7YR', 'median_earnings_7yr number(12,2)');
  add_col('MEDIAN_DEBT_PAYMENT', 'median_debt_payment number(10,2)');
  add_col('AWARDS', 'awards number(8)');
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'CIP_CODES';
  if l_count = 0 then
    execute immediate q'[
      create table cip_codes (
        cip_code   varchar2(4) primary key,
        title      varchar2(500) not null,
        embedding  vector(384, float32),
        created_at timestamp with time zone default systimestamp not null
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_json_duality_views
  where view_name = 'MAJOR_OUTCOMES_DV';
  if l_count = 0 then
    execute immediate q'[
      create json relational duality view major_outcomes_dv as
        institutions @insert @update
        {
          _id: institution_id,
          unitId: unit_id,
          name: name,
          city: city,
          state: state,
          control: control,
          programs: programs @insert @update [
            {
              programId: program_id,
              cipCode: cip_code,
              programName: program_name,
              credentialLevel: credential_level,
              earnings1yr: median_earnings_1yr,
              earnings4yr: median_earnings_4yr,
              earnings5yr: median_earnings_5yr,
              earnings7yr: median_earnings_7yr,
              medianDebt: median_debt,
              dataYear: data_year
            }
          ]
        }
    ]';
  end if;
exception
  when others then
    dbms_output.put_line('major_outcomes_dv skipped: ' || sqlerrm);
end;
/
