# Backend tests

Run the backend suite from the project root:

```bash
.venv/bin/python -B -m pytest backend/tests -q --tb=short -p no:cacheprovider
```

Tests cover inclusive box geometry, partial mask overlap, independent sample
checks, annotation metadata, finite JSON values, rule validation, local
Ollama configuration precedence (process environment, `.env`, TOML), request
handling, extraction, review state transitions and API templates.
Ollama requests are mocked; these tests do not download or run a model.

The contract suite compares generated files with their source and checks that
audit/export fixtures use the confirmed rulebook version. Run tests before
committing changes.

PostgreSQL integration tests require `PRISM_TEST_DATABASE_URL`. Each test uses
an isolated schema and removes it afterward. See the database test instructions
in [the backend guide](../README.md#database-tests). The review tests exercise
version history, concurrent decisions, migration upgrades and rollback when
either insert or the transaction commit fails.
