-- 005: institutions, programs, and the CIP-to-SOC crosswalk.

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'INSTITUTIONS';
  if l_count = 0 then
    execute immediate q'[
      create table institutions (
        institution_id number generated always as identity primary key,
        unit_id        varchar2(20) not null constraint institutions_unit_uq unique,
        name           varchar2(500) not null,
        city           varchar2(200),
        state          varchar2(2),
        location       sdo_geometry,
        control        varchar2(30),
        created_at     timestamp with time zone default systimestamp not null,
        updated_at     timestamp with time zone default systimestamp not null
      )
    ]';
  end if;
end;
/

-- Spatial metadata registration for institutions.location, mirroring
-- metro_areas.centroid.
declare
  l_count number;
begin
  select count(*) into l_count
  from user_sdo_geom_metadata
  where table_name = 'INSTITUTIONS' and column_name = 'LOCATION';
  if l_count = 0 then
    insert into user_sdo_geom_metadata (table_name, column_name, diminfo, srid)
    values (
      'INSTITUTIONS',
      'LOCATION',
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
  select count(*) into l_count from user_tables where table_name = 'PROGRAMS';
  if l_count = 0 then
    execute immediate q'[
      create table programs (
        program_id          number generated always as identity primary key,
        institution_id      number not null
                            constraint programs_institution_fk
                            references institutions (institution_id) on delete cascade,
        cip_code            varchar2(20) not null,
        program_name        varchar2(500),
        credential_level    varchar2(50),
        median_earnings_1yr number(12,2),
        median_earnings_4yr number(12,2),
        median_debt         number(12,2),
        data_year           number(4),
        created_at          timestamp with time zone default systimestamp not null,
        updated_at          timestamp with time zone default systimestamp not null,
        constraint programs_uq unique (institution_id, cip_code, credential_level, data_year)
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'PROGRAMS_CIP_IX';
  if l_count = 0 then
    execute immediate 'create index programs_cip_ix on programs (cip_code)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'PROGRAM_OCCUPATIONS';
  if l_count = 0 then
    execute immediate q'[
      create table program_occupations (
        crosswalk_id  number generated always as identity primary key,
        cip_code      varchar2(20) not null,
        occupation_id number not null
                      constraint prog_occ_occupation_fk
                      references occupations (occupation_id) on delete cascade,
        created_at    timestamp with time zone default systimestamp not null,
        constraint prog_occ_pair_uq unique (cip_code, occupation_id)
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'PROG_OCC_CIP_IX';
  if l_count = 0 then
    execute immediate 'create index prog_occ_cip_ix on program_occupations (cip_code)';
  end if;
end;
/
