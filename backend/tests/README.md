# Backend tests

Run the backend suite from the project root:

```bash
.venv/bin/python -B -m pytest backend/tests -q --tb=short -p no:cacheprovider
```

Tests cover the implemented measurements, sample audits, rule validation, local
Ollama request handling, extraction, review state transitions and API templates.
Ollama requests are mocked; these tests do not download or run a model.

PostgreSQL integration tests require `PRISM_TEST_DATABASE_URL`. Each test uses
an isolated schema and removes it afterward. See the database test instructions
in [the backend guide](../README.md#database-tests). The review tests exercise
version history, concurrent decisions, migration upgrades and rollback when
either insert or the transaction commit fails.
