# Quickstart: AI Engine Service

```bash
cd service
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,ai]'

ruff check . && ruff format --check .
pytest -q                 # all
pytest -q -m 'not slow'   # skip real 16-page renders

# API + worker + fake Backend as three processes, with fault injection
./devtools/local_stack.sh

# container
docker compose up --build
```

Full detail: `service/docs/04-local-development.md`, `05-testing.md`, `08-operations.md`.
