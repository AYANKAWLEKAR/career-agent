# Career Path Navigator

Career Path Navigator is an agentic career-planning system built on Oracle Autonomous AI
Database. It ingests O*NET career data, builds a directed occupation graph, infers a user's
starting occupation from their experience, and computes concrete tool, education, and
experience gaps against a target occupation.

## Prerequisites

- Python 3.13 (see `NOTES.md` for why not 3.14)
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip` with a virtual environment
- No cloud credentials are needed to install or run the test suite

## Install

```bash
uv sync
```

Or with pip:

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e . --group dev
```

## Run tests

```bash
uv run pytest
```

All tests pass offline. Two tests are skipped until a live database wallet is present:
the live connection check and the live migration application.

## Run migrations

Once an Autonomous AI Database instance exists, copy `.env.example` to `.env`, fill in the
database variables, place the wallet in the directory named by `ORACLE_TNS_ADMIN`, then:

```bash
uv run python -m navigator.db.migrations
```

## Load requirement data

After the corrected O*NET 30.3 Oracle tables and curated occupations are loaded, populate the
shared requirement vocabulary and occupation mappings:

```bash
uv run python scripts/load_requirements.py
```

The loader is idempotent and commits tools, education, and experience independently. It also
reports vocabulary counts and verifies that every traversable occupation has at least one hot
technology requirement.

Application code can then obtain an explainable gap report with:

```python
from navigator.requirements import coverage

report = coverage(pool, profile_id=profile_id, occupation_id=target_occupation_id)
print(report.coverage_pct, report.missing_tools, report.education_needed)
```

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `ORACLE_TNS_ADMIN` | yes | Wallet directory (must contain `tnsnames.ora` and `ewallet.pem`) |
| `ORACLE_DSN` | yes | Service alias from `tnsnames.ora` |
| `ORACLE_USER` | yes | Database user |
| `ORACLE_PASSWORD` | yes | Database password |
| `ORACLE_WALLET_PASSWORD` | yes | Wallet password |
| `OCI_TENANCY`, `OCI_USER`, `OCI_FINGERPRINT`, `OCI_REGION`, `OCI_COMPARTMENT_ID`, `OCI_KEY_FILE` | no | OCI API access (later phases) |
| `CAREERONESTOP_USER_ID`, `CAREERONESTOP_TOKEN` | for CareerOneStop | CareerOneStop API credentials |
| `SCORECARD_API_KEY` | no | College Scorecard API key |
| `BLS_API_KEY` | no | BLS API key (unused this phase) |
| `DEMO_MODE` | no (default `true`) | Demo mode flag |
| `LOG_LEVEL` | no (default `INFO`) | Log level |
| `POOL_MIN`, `POOL_MAX`, `POOL_INCREMENT` | no (defaults 1/4/1) | Connection pool sizing |
