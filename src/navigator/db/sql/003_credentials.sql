-- 003: certifications, licenses, apprenticeships, and their occupation links.

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'CREDENTIALS';
  if l_count = 0 then
    execute immediate q'[
      create table credentials (
        credential_id        number generated always as identity primary key,
        credential_name      varchar2(500) not null,
        credential_type      varchar2(50) not null
                             constraint credentials_type_ck
                             check (credential_type in ('certification', 'license', 'apprenticeship')),
        issuing_organization varchar2(500),
        abbreviation         varchar2(50),
        description          clob,
        jurisdiction         varchar2(100),
        embedding            vector(384, float32),
        created_at           timestamp with time zone default systimestamp not null,
        updated_at           timestamp with time zone default systimestamp not null
      )
    ]';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'CREDENTIALS_TYPE_IX';
  if l_count = 0 then
    execute immediate 'create index credentials_type_ix on credentials (credential_type)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'CREDENTIALS_ABBREV_IX';
  if l_count = 0 then
    execute immediate 'create index credentials_abbrev_ix on credentials (abbreviation)';
  end if;
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'OCCUPATION_CREDENTIALS';
  if l_count = 0 then
    execute immediate q'[
      create table occupation_credentials (
        occupation_credential_id number generated always as identity primary key,
        occupation_id            number not null
                                 constraint occ_creds_occupation_fk
                                 references occupations (occupation_id) on delete cascade,
        credential_id            number not null
                                 constraint occ_creds_credential_fk
                                 references credentials (credential_id) on delete cascade,
        requirement_level        varchar2(30)
                                 constraint occ_creds_level_ck
                                 check (requirement_level in ('required', 'commonly_held', 'optional')),
        created_at               timestamp with time zone default systimestamp not null,
        constraint occ_creds_pair_uq unique (occupation_id, credential_id)
      )
    ]';
  end if;
end;
/
