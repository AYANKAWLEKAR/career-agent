-- 008: SQL property graph over occupations and occupation_edges.
--
-- If CREATE PROPERTY GRAPH is unavailable on the instance, the migration
-- runner catches the failure, appends the outcome to NOTES.md, and continues.
-- The next phase reads NOTES.md to decide between the GRAPH_TABLE path and the
-- recursive CTE fallback.
--
-- Example bounded variable-length traversal for the next phase (kept here so
-- the syntax is documented in the repository):
--
-- select *
-- from graph_table (
--   occupation_graph
--   match (a is occupations) -[e is occupation_edges]->{1,3} (b is occupations)
--   where a.soc_code = '15-1252.00'
--   columns (a.title as from_title, b.title as to_title, b.soc_code as to_soc)
-- );

declare
  l_count number;
begin
  select count(*) into l_count from user_property_graphs
  where graph_name = 'OCCUPATION_GRAPH';
  if l_count = 0 then
    execute immediate q'[
      create property graph occupation_graph
        vertex tables (
          occupations
            key (occupation_id)
            properties (occupation_id, soc_code, title, job_zone)
        )
        edge tables (
          occupation_edges
            key (edge_id)
            source key (src_occupation_id) references occupations (occupation_id)
            destination key (dst_occupation_id) references occupations (occupation_id)
            properties (edge_id, edge_type, weight)
        )
    ]';
  end if;
end;
/
