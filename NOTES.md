# Environment notes

- Python 3.14 fallback was needed (2026-08-03): `oracleagentmemory` 26.6.0 publishes wheels
  only for cp310–cp313, and the local `uv` (0.8.0) offers only a 3.14 beta interpreter.
  Project pinned to Python 3.13 per the setup spec's fallback rule. `oracledb` 4.0.2 does
  publish cp314 wheels, so revisit once `oracleagentmemory` ships a cp314 wheel.
- `CREATE PROPERTY GRAPH` outcome on the live instance: not yet attempted (no database
  provisioned in this phase). The migration runner appends the result here when
  008_property_graph.sql runs against a real instance.
- `oracleagentmemory` documented API vs installed wheel: no divergence checked yet; the
  package is declared but unused in this phase.
- Schema deviation: `occupation_skills.level` is named `level_value` because `LEVEL` is
  an Oracle reserved word (the CONNECT BY pseudocolumn) and cannot be an unquoted
  column name.
