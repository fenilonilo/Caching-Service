# Caching Service

A FastAPI microservice that builds payloads from two lists of strings: every string goes
through a (simulated, expensive) external transformer and the results are interleaved.
Transformer results and generated payloads live in a database, so work already paid for is
never paid again.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) (the CLI and the local run) and, for the Docker path, Docker.

With Docker (API + PostgreSQL):

```sh
docker compose up --build -d
uv run cache-cli -i examples/sample.json -r 3
```

Locally (SQLite file `cache.db`):

```sh
uv sync
# terminal 1: start the API and leave it running
uv run uvicorn caching_service.main:create_app --factory --reload
# terminal 2: once the API is up, send a payload
uv run cache-cli -i examples/sample.json
```

Interactive API docs: http://localhost:8000/docs

## API

| Method | Path            | Success                                              | Errors                                           |
|--------|-----------------|------------------------------------------------------|--------------------------------------------------|
| POST   | `/payload`      | `201 {"id", "message"}`; `200` if it already existed | `422` invalid body, `502` transformer failed     |
| GET    | `/payload/{id}` | `200 {"output"}`                                     | `404` unknown id, `422` malformed id             |

Request body: `{"list_1": [...], "list_2": [...]}`, both lists with the same length,
1 to 1000 items, strings up to 1000 characters, so one request cannot trigger unbounded
transformer work; violations are a 422, never a 500.

Validation errors keep FastAPI's `{"detail": [...]}` shape but leave out each error's `input` field: echoing
a lone surrogate such as `"\ud800"` back would make the error response itself impossible to encode.

## Architecture

```
api/ (FastAPI: schemas, routes, dependency)              infrastructure/ (SQLAlchemy, transformer)
                     \                                      /
                      ->  application/ (PayloadService, ports)  <-   implements the ports
                                     |
                                  domain.py (pure rules: interleaving, payload ids)
```

Dependencies point inwards: infrastructure implements the ports, and the application layer
imports nothing from `api` or `infrastructure`. The use case only knows the `Transformer`,
`TransformationCache` and `PayloadRepository` protocols, so it is unit-tested with
in-memory fakes, and the SQL adapters are tested against a real database. `main.create_app()`
is the only place that chooses concrete classes: the transformer, the database engine and the
SQL adapters. It builds one `PayloadService` at startup, and `api/dependencies.py` only hands
it to the routes.

## Following one request

`POST /payload`
1. `api/schemas.py` validates the body (equal lengths, size limits, no unknown fields).
2. `api/dependencies.py` hands the routes the `PayloadService` built at startup. Its SQL
   adapters open a short session per call, so no connection is held while the transformer runs.
3. `PayloadService.create` derives the payload id from the input (UUIDv5). If that payload
   exists, it answers right away: no cache lookup, no transformer call.
4. Otherwise it collects the distinct strings, reads the cached ones in one query, calls the
   transformer concurrently for the misses only, and stores the new results in one insert.
5. It interleaves the outputs, stores the payload and returns its id.

`GET /payload/{id}` is a single primary-key lookup.

## Design decisions

- **Payload id derived from the input** (UUIDv5 of the canonical JSON): identical requests
  get the same id without a lookup table, and JSON keeps string boundaries unambiguous.
  Identity is the input, so two inputs that happen to produce the same output get two ids.
- **Cache keyed by SHA-256 of the string**: fixed-size keys keep the index small and avoid
  PostgreSQL's B-tree entry limit (~2.7 kB) for long strings.
- **Idempotent writes** (`INSERT ... ON CONFLICT DO NOTHING`): concurrent identical requests,
  on one process or many replicas, never fail on a duplicate key.
- **One commit per write**: transformer results are stored as soon as they exist, even if
  the rest of the request fails.
- **Partial transformer failures**: successful results are cached before the error is
  reported (HTTP 502), so a retry only pays for the strings that failed.
- **Async end to end**: the transformer stands for a network service; asyncio lets one
  request wait on all its cache misses at once and one worker serve many requests.
- **Stateless API**: all state is in the database, so the service scales by adding replicas.

## CLI

```
cache-cli [-H|--host URL] [-r|--repeat N] [-i|--input FILE|-] [-j|--json JSON] [-o|--output FILE|-] [-h|--help]
```

Exactly one of `--input` and `--json` is required (inline JSON as in
`-j '{"list_1": ["a"], "list_2": ["b"]}'` is quoted for POSIX shells; in Windows PowerShell
use `--input`). Each iteration POSTs the payload, GETs it
back and writes one JSON line: `{"iteration", "id", "output", "elapsed_ms"}`. With
`--repeat`, the first iteration pays the simulated transformer latency (unless the payload
already exists in the database) and the rest come from the cache.

`--host` defaults to `http://127.0.0.1:8000` rather than `localhost`: uvicorn binds IPv4 by default, and on
Windows `localhost` resolves to `::1` first, which costs about two seconds per refused attempt.

The brief lists `-h` for both `--host` and `--help`; argparse cannot give one flag two
meanings, so `--host` uses `-H`. Arguments are validated by pydantic-settings before any
request is sent (positive repeat, valid URL, existing file, valid payload JSON).
Environment variables are deliberately ignored, so a stray `HOST` cannot redirect the tool.

## Configuration

| Variable                            | Default                            | Purpose                       |
|-------------------------------------|------------------------------------|-------------------------------|
| `CACHE_DATABASE_URL`                | `sqlite+aiosqlite:///./cache.db`   | SQLAlchemy async database URL |
| `CACHE_TRANSFORMER_LATENCY_SECONDS` | `0.1`                              | Simulated transformer latency |

## Tests

```sh
uv run pytest                 # everything
uv run pytest tests/unit      # domain, use case with fakes, CLI parsing
uv run pytest tests/integration  # SQL repositories, HTTP API and CLI against the real app
```

The repository tests also run against PostgreSQL:

```sh
docker compose up -d db
docker compose exec db createdb -U cache cache_test
TEST_DATABASE_URL=postgresql+asyncpg://cache:cache@localhost:55432/cache_test \
  uv run pytest tests/integration/test_repositories.py
```

The host port is 55432 because compose publishes PostgreSQL there, so it does not clash with a PostgreSQL installed locally on 5432.

Quality gates: `uv run ruff format --check . && uv run ruff check . && uv run mypy`.

## Known shortcuts

- **`create_all` on startup instead of Alembic migrations.** Fine for two tables; migrations
  become necessary once the schema changes or several replicas start at the same time.
- **No single-flight between concurrent requests.** Two simultaneous requests with the same
  new string both call the transformer; the result stays correct thanks to idempotent
  writes. An in-process lock map or a distributed lock would remove the duplicate call.
- **Concurrent identical creates can both answer 201.** Two simultaneous identical POSTs can
  both see "not created yet" and return 201 with the same id; the data stays correct thanks
  to idempotent writes.
- **Unbounded concurrency towards the transformer** (at most 2000 calls per request, given
  the input limits). An `asyncio.Semaphore` is the fix if the real service rate-limits.
- **No in-memory cache in front of the database** and **no expiry or transformer
  versioning** of cached results.
- **Compose credentials are for local development only.**
