-- 007: vector, hybrid, and spatial indexes.
--
-- Run this file only AFTER data is loaded. Running it before bulk load makes
-- the load an order of magnitude slower, because every inserted row must also
-- maintain the neighbor-graph and spatial structures.
--
-- Each index creation is individually guarded so a partial failure (for
-- example, a feature unavailable on the instance) does not block the rest.

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'OCCUPATIONS_EMBEDDING_VIX';
  if l_count = 0 then
    execute immediate q'[
      create vector index occupations_embedding_vix on occupations (embedding)
      organization inmemory neighbor graph
      distance cosine
      with target accuracy 95
    ]';
  end if;
exception
  when others then
    dbms_output.put_line('occupations_embedding_vix skipped: ' || sqlerrm);
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'TASKS_EMBEDDING_VIX';
  if l_count = 0 then
    execute immediate q'[
      create vector index tasks_embedding_vix on tasks (embedding)
      organization inmemory neighbor graph
      distance cosine
      with target accuracy 95
    ]';
  end if;
exception
  when others then
    dbms_output.put_line('tasks_embedding_vix skipped: ' || sqlerrm);
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'SAVED_PLANS_EMBEDDING_VIX';
  if l_count = 0 then
    execute immediate q'[
      create vector index saved_plans_embedding_vix on saved_plans (embedding)
      organization inmemory neighbor graph
      distance cosine
      with target accuracy 95
    ]';
  end if;
exception
  when others then
    dbms_output.put_line('saved_plans_embedding_vix skipped: ' || sqlerrm);
end;
/

-- Hybrid vector index over credentials text with the vector column as a
-- parameter and cosine distance.
declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'CREDENTIALS_HYBRID_VIX';
  if l_count = 0 then
    execute immediate q'[
      create hybrid vector index credentials_hybrid_vix on credentials (description)
      parameters ('vector_column embedding distance cosine')
    ]';
  end if;
exception
  when others then
    dbms_output.put_line('credentials_hybrid_vix skipped: ' || sqlerrm);
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'METRO_AREAS_CENTROID_SIX';
  if l_count = 0 then
    execute immediate q'[
      create index metro_areas_centroid_six on metro_areas (centroid)
      indextype is mdsys.spatial_index_v2
    ]';
  end if;
exception
  when others then
    dbms_output.put_line('metro_areas_centroid_six skipped: ' || sqlerrm);
end;
/

declare
  l_count number;
begin
  select count(*) into l_count from user_indexes where index_name = 'INSTITUTIONS_LOCATION_SIX';
  if l_count = 0 then
    execute immediate q'[
      create index institutions_location_six on institutions (location)
      indextype is mdsys.spatial_index_v2
    ]';
  end if;
exception
  when others then
    dbms_output.put_line('institutions_location_six skipped: ' || sqlerrm);
end;
/
