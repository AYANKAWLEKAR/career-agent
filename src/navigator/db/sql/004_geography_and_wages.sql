-- 004: metro areas (with spatial metadata registration), wages, and projections.

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'METRO_AREAS';
  if l_count = 0 then
    execute immediate q'[
      create table metro_areas (
        metro_id    number generated always as identity primary key,
        msa_code    varchar2(20) not null constraint metro_areas_msa_uq unique,
        msa_name    varchar2(300) not null,
        state_codes json,
        centroid    sdo_geometry,
        population  number,
        created_at  timestamp with time zone default systimestamp not null,
        updated_at  timestamp with time zone default systimestamp not null
      )
    ]';
  end if;
end;
/

-- Spatial metadata registration for metro_areas.centroid. Required before a
-- spatial index can be created; this is the step most commonly forgotten.
declare
  l_count number;
begin
  select count(*) into l_count
  from user_sdo_geom_metadata
  where table_name = 'METRO_AREAS' and column_name = 'CENTROID';
  if l_count = 0 then
    insert into user_sdo_geom_metadata (table_name, column_name, diminfo, srid)
    values (
      'METRO_AREAS',
      'CENTROID',
      sdo_dim_array(
        sdo_dim_element('LONGITUDE', -180, 180, 0.05),
        sdo_dim_element('LATITUDE', -90, 90, 0.05)
      ),
      4326
    );
    commit;
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'METRO_WAGES';
  if l_count = 0 then
    execute immediate q'[
      create table metro_wages (
        wage_id                 number generated always as identity primary key,
        occupation_id           number not null
                                constraint metro_wages_occupation_fk
                                references occupations (occupation_id) on delete cascade,
        metro_id                number
                                constraint metro_wages_metro_fk
                                references metro_areas (metro_id) on delete cascade,
        employment_count        number,
        employment_per_thousand number(8,3),
        wage_p10                number(12,2),
        wage_p25                number(12,2),
        wage_median             number(12,2),
        wage_p75                number(12,2),
        wage_p90                number(12,2),
        wage_mean               number(12,2),
        data_year               number(4) not null,
        created_at              timestamp with time zone default systimestamp not null,
        constraint metro_wages_uq unique (occupation_id, metro_id, data_year)
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'METRO_WAGES_OCCUPATION_IX';
  if l_count = 0 then
    execute immediate 'create index metro_wages_occupation_ix on metro_wages (occupation_id)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'METRO_WAGES_METRO_IX';
  if l_count = 0 then
    execute immediate 'create index metro_wages_metro_ix on metro_wages (metro_id)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'EMPLOYMENT_PROJECTIONS';
  if l_count = 0 then
    execute immediate q'[
      create table employment_projections (
        projection_id        number generated always as identity primary key,
        occupation_id        number not null
                             constraint emp_proj_occupation_fk
                             references occupations (occupation_id) on delete cascade,
        base_year            number(4),
        projected_year       number(4),
        base_employment      number,
        projected_employment number,
        percent_change       number(6,2),
        annual_openings      number,
        created_at           timestamp with time zone default systimestamp not null,
        constraint emp_proj_uq unique (occupation_id, base_year)
      )
    ]';
  end if;
end;
/
