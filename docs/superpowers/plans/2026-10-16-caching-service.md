# Caching Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Microsserviço FastAPI que gera payloads (strings transformadas e intercaladas de duas listas), cacheia cada resultado do "transformer" em banco (SQLite/PostgreSQL) para minimizar chamadas, reutiliza ids de payloads idênticos, e um CLI `cache-cli` (pydantic-settings) para exercitar o serviço — tudo construído em TDD.

**Architecture:** Clean Architecture enxuta, com dependências apontando para dentro: `domain` (regras puras) ← `application` (caso de uso `PayloadService` + `Protocol`s de portas) ← `infrastructure` (SQLAlchemy async, transformer simulado) e `api` (FastAPI). `main.create_app()` é o composition root e monta um único `PayloadService` com os adaptadores SQL. Cada operação de banco abre uma sessão curta, então nenhuma conexão fica presa enquanto se espera o transformer. API stateless + cache compartilhado no banco + escritas idempotentes (`ON CONFLICT DO NOTHING`) = escala horizontal por réplicas sem coordenação.

**Tech Stack:** Python 3.13, uv 0.8, FastAPI 0.143, SQLAlchemy 2.1 (asyncio) + aiosqlite / asyncpg, pydantic 2.14 + pydantic-settings 2.16 (CLI), httpx2 2.13 (cliente HTTP; base do `TestClient` no Starlette 1.8), pytest + pytest-asyncio, ruff, mypy, Docker + Compose (PostgreSQL 17).

**Spec:** `Test task Python.md` (raiz do repositório)

---

## Decisões de design (validar antes de executar)

Cada item vira uma seção "Design decisions" no README (Task 8) — o enunciado pede para comunicar dúvidas e justificar atalhos.

| # | Decisão | Por quê | Alternativa descartada |
|---|---------|---------|------------------------|
| D1 | **Id do payload = UUIDv5 do JSON canônico da entrada** (`[list_1, list_2]`) | Entrada idêntica ⇒ mesmo id sem tabela de lookup; checagem de "já existe" é 1 SELECT por PK; nenhum transformer nem cache é tocado no reuso. JSON preserva fronteiras (`"a, b"+"c"` ≠ `"a"+"b, c"`). | UUID4 + coluna `input_hash` única (uma coluna e um índice a mais, mesmo efeito). |
| D2 | **Identidade do payload = entrada, não saída** | Permite responder sem calcular nada. Entradas diferentes com mesma saída (`"a"` vs `"A"`) geram ids diferentes — documentado. | Chavear pela saída exige transformar tudo antes de saber se existe. |
| D3 | **Cache de transformações chaveado por SHA-256 da string** | Chave de tamanho fixo: índice pequeno e sem estourar o limite de ~2,7 kB por entrada de B-tree do PostgreSQL com strings longas. | String crua como PK (quebra no Postgres com strings grandes). |
| D4 | **Escritas idempotentes** (`INSERT … ON CONFLICT DO NOTHING`) em ambas as tabelas | Dois POSTs idênticos concorrentes (ou réplicas) não geram 500 por conflito de PK. | `try/except IntegrityError` (aborta o batch inteiro). |
| D5 | **Cada escrita de repositório é uma transação curta própria** (`sessions.begin()`); os adaptadores recebem um `async_sessionmaker` e abrem uma sessão por método | Resultados caros do transformer sobrevivem a falhas posteriores **e** nenhuma conexão do pool fica "idle in transaction" enquanto o transformer roda (teto do pool / `max_connections` sob carga). Dispensa Unit of Work. | Sessão por request (segura uma conexão em transação aberta durante todo o fan-out; achado da revisão final) e UoW com commit único no fim (perde o trabalho pago em falhas). |
| D6 | **Falha parcial do transformer**: `gather(return_exceptions=True)`, cacheia sucessos, depois levanta `TransformerError` → HTTP 502 | Retry paga só o que falhou — "minimize the number of calls". | Propagar a primeira exceção (descarta resultados já pagos). |
| D7 | **Dedup por requisição**: transformer chamado 1× por string distinta, misses em paralelo (`asyncio.gather`) | Requisito central + latência ≈ uma chamada, não N. | Chamada sequencial por string. |
| D8 | **POST responde 201 (criado) ou 200 (payload idêntico já existia)**, corpo `{"id", "message"}` | Semântica HTTP correta e torna o reuso visível. | Sempre 201. |
| D9 | **Limites na fronteira**: 1–1000 itens por lista, ≤1000 chars por string, campos extras proibidos | Requisição não pode disparar trabalho ilimitado; erro de cliente vira 422, nunca 500. | Sem limites. |
| D10 | **CLI: `-H/--host` em vez de `-h/--host`** | O enunciado usa `-h` para host *e* help; argparse não suporta (verificado: `conflicting option string: -h`). Exige `case_sensitive=True`, senão pydantic-settings baixa `H`→`h`. O argparse do Python 3.13 imprime `-H, --host AnyHttpUrl` no help. | Desligar `--help`. |
| D11 | **CLI: exatamente um de `--input`/`--json`**; `-` = stdin/stdout; saída = 1 linha JSON por iteração `{"iteration","id","output","elapsed_ms"}` | Erro explícito em vez de travar lendo stdin; `elapsed_ms` mostra o cache agindo no `--repeat`. | Default stdin implícito. |
| D12 | **CLI ignora variáveis de ambiente** (`settings_customise_sources → (init_settings,)`) | Verificado: com alias, `env_prefix` não se aplica, então um `HOST`/`H` no ambiente mudaria o destino silenciosamente. | `env_prefix` (não protege campos com alias). |
| D13 | **`create_all` no startup, sem Alembic** | Schema de 2 tabelas; atalho documentado com teto (`ponytail:`). Migrations quando o schema evoluir ou várias réplicas subirem juntas. | Alembic desde o dia 1. |
| D14 | **SQLite (dev/testes) e PostgreSQL (Compose)** via `CACHE_DATABASE_URL` | SQLite: testes rápidos, zero infra. Postgres: banco compartilhado entre réplicas. Testes de repositório rodam nos dois (`TEST_DATABASE_URL`). O PostgreSQL do Compose é publicado em `127.0.0.1:5432` (só loopback: credenciais de dev não vão para a rede). | Só SQLite (não escala entre containers). |
| D15 | **`Test task Python.md` fora do repo público** (`.gitignore`); este plano em `docs/` **é** versionado | O enunciado da avaliação não deve ser publicado; o plano documenta como a solução foi planejada e executada. | Commitar o enunciado. |
| D16 | **CLI: host padrão `http://127.0.0.1:8000`, não `localhost`** | O uvicorn escuta em IPv4; no Windows `localhost` resolve `::1` primeiro e cada tentativa recusada custa ~2 s (1ª iteração do `--repeat` medida: 2189 ms → 141 ms). | `localhost`. |
| D17 | **422 mantém o formato `{"detail": [...]}` do FastAPI, sem o `input` de cada erro** | O FastAPI ecoava um surrogate solitário (`"\ud800"`) e a própria resposta de erro falhava ao codificar (HTTP 500). | `AfterValidator` nas strings (código morto: `StringConstraints` já rejeita surrogates solitários). |
| D18 | **CLI termina com mensagem (não traceback) em `--output` não gravável ou `--input` não-UTF-8, e escreve linhas JSON ASCII-escapadas** | Qualquer encoding de console funciona (o cp1252 do Windows quebrava com "☕"). | `ensure_ascii=False` e traceback. |
| D19 | **Healthcheck do Compose via TCP** (`pg_isready -h 127.0.0.1`) | A checagem pelo socket pode passar durante o init do primeiro boot da imagem, antes de o TCP subir. | `pg_isready` sem `-h`. |

**Atalhos conscientes (README "Known shortcuts"):** sem single-flight entre requisições concorrentes com a mesma string nova (ambas chamam o transformer; o resultado continua correto por D4); dois POSTs idênticos simultâneos podem ambos responder 201 com o mesmo id (os dados continuam corretos graças às escritas idempotentes); fan-out sem limite de concorrência (teto = 2000 chamadas por request por D9; `asyncio.Semaphore` se o serviço real limitar taxa); sem LRU em memória na frente do banco; entradas do cache não expiram nem são versionadas por transformer.

---

## Estrutura de arquivos

```
pyproject.toml                    # deps, entry point cache-cli, config ruff/mypy/pytest
uv.lock
.python-version                   # 3.13
.gitignore
.dockerignore
Dockerfile                        # multi-stage com uv, usuário não-root
compose.yaml                      # api + postgres
README.md
examples/sample.json              # exemplo do enunciado, usado pelo CLI
src/caching_service/
  __init__.py
  domain.py                       # build_output(), payload_id() — puro, sem I/O
  application/
    __init__.py
    ports.py                      # Protocols: Transformer, TransformationCache, PayloadRepository
    service.py                    # PayloadService (create/get), erros, CreatedPayload
  infrastructure/
    __init__.py
    database.py                   # modelos SQLAlchemy + create_schema()
    repositories.py               # SqlTransformationCache, SqlPayloadRepository (sessão curta por operação)
    transformer.py                # UppercaseTransformer (serviço externo simulado)
  api/
    __init__.py
    schemas.py                    # PayloadRequest/PayloadCreated/PayloadOut + limites
    dependencies.py               # entrega o PayloadService montado no lifespan
    routes.py                     # POST /payload, GET /payload/{id}
  config.py                       # Settings (CACHE_*)
  main.py                         # composition root: engine, adapters, PayloadService, handler de 422
  cli.py                          # cache-cli
tests/
  __init__.py
  support.py                      # fakes das portas + dados de exemplo do enunciado
  unit/
    __init__.py
    test_domain.py
    test_service.py
    test_transformer.py
    test_cli_settings.py
  integration/
    __init__.py
    conftest.py                   # banco descartável, app in-process
    test_repositories.py
    test_api.py
    test_cli.py
```

Regra de dependência: `domain` não importa nada do projeto; `application` importa só `domain`; `infrastructure` e `api` importam `application`/`domain`; só `main.py` conhece as classes concretas dos adaptadores.

---

## Global Constraints

- Endpoints: `POST /payload` com `{"list_1": [...], "list_2": [...]}`; `GET /payload/{id}` → `{"output": "..."}`.
- Formato da saída: strings transformadas intercaladas `l1[0], l2[0], l1[1], l2[1], …` unidas por `", "` — exemplo do enunciado deve produzir exatamente `FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING`.
- As duas listas têm o mesmo tamanho (validado).
- FastAPI para a API; SQLAlchemy para o banco; SQLite ou PostgreSQL.
- Aplicação dockerizada.
- CLI chamado `cache-cli`, parsing e sanitização com Pydantic Settings; flags `--host`, `-r/--repeat N`, `-i/--input FILE|-`, `-j/--json JSON`, `-o/--output FILE|-`, `-h/--help` (host em `-H`, ver D10).
- Python `>=3.13`; ruff com `line-length = 100`; mypy `strict`.
- Comentários e docs explicam o **porquê**, não o como. Código, comentários, README e commits em inglês.
- Commits pequenos, Conventional Commits, nunca squash (a banca lê o histórico).
- Nome do repositório neutro, sem o nome da empresa.
- Comandos assumem Git Bash na raiz do repo; variantes PowerShell indicadas quando diferem.

## Review Focus

1. **Strings repetidas dentro ou entre as listas** (`["a","b","a"]` / `["b","a","c"]`) → transformer chamado uma vez por string distinta, saída ainda correta. *Teste:* Task 2 `test_each_distinct_string_is_transformed_once`.
2. **Dois clientes enviam o mesmo payload ao mesmo tempo** → nenhum 500 por conflito de PK; ambos recebem o mesmo id. *Teste:* Task 3 `test_adding_existing_payload_is_a_noop` e `test_cache_put_is_idempotent_and_keeps_first_value`.
3. **Corpo inválido** (tamanhos diferentes, listas vazias, item não-string, campo extra, string/lista grande demais, surrogate solitário `"\ud800"`) → 422 e **zero** chamadas ao transformer. *Teste:* Task 4 `test_invalid_body_is_rejected_before_any_transformer_call` e `test_lone_surrogate_is_rejected_before_any_transformer_call`.
4. **Strings não-ASCII e longas** (`"straße"` → `"STRASSE"`, emoji, 10 000 chars) → round-trip correto no banco, id estável, índice do Postgres não estoura. *Teste:* Task 3 `test_cache_round_trips_non_ascii_and_long_strings`, Task 4 `test_non_ascii_strings_round_trip`.
5. **Transformer falha em parte das strings** → HTTP 502 (não 500 com traceback), sucessos ficam cacheados, retry paga só a falha. *Teste:* Task 2 `test_transformer_failure_caches_successes_and_stores_no_payload`, Task 4 `test_transformer_failure_returns_502_and_keeps_successful_results`.

---

### Task 1: Scaffold do projeto + regras de domínio

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `src/caching_service/__init__.py`, `src/caching_service/domain.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/unit/test_domain.py`
- Generated: `uv.lock`

**Interfaces:**
- Consumes: nada.
- Produces: `build_output(first: Sequence[str], second: Sequence[str]) -> str` (levanta `ValueError` se tamanhos diferem); `payload_id(list_1: Sequence[str], list_2: Sequence[str]) -> uuid.UUID`.

- [ ] **Step 1: Criar `pyproject.toml`**

```toml
[project]
name = "caching-service"
version = "0.1.0"
description = "FastAPI microservice that caches transformer results and serves interleaved payloads"
requires-python = ">=3.13"
dependencies = [
    "aiosqlite>=0.20",
    "asyncpg>=0.30",
    "fastapi>=0.143",
    "httpx2>=2.13",
    "pydantic-settings>=2.16",
    "sqlalchemy[asyncio]>=2.1",
    "uvicorn[standard]>=0.30",
]

[project.scripts]
cache-cli = "caching_service.cli:main"

[dependency-groups]
dev = [
    "mypy>=1.11",
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "ruff>=0.6",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "UP", "SIM", "ASYNC", "RUF"]

[tool.mypy]
strict = true
mypy_path = "src"
packages = ["caching_service"]
plugins = ["pydantic.mypy"]
```

- [ ] **Step 2: Criar `.python-version`, `.gitignore` e pacotes vazios**

`.python-version`:
```
3.13
```

`.gitignore`:
```
.venv/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.db
# Assessment brief: not part of the public repository.
Test task Python.md
```

`src/caching_service/__init__.py`:
```python
"""Caching service: transforms and interleaves string lists, caching every transformer call."""
```

`tests/__init__.py` e `tests/unit/__init__.py`: arquivos vazios (tornam `tests` um pacote, permitindo `from tests.support import ...`).

- [ ] **Step 3: Instalar e conferir o ambiente**

Run: `uv sync && uv run pytest`
Expected: uv baixa Python 3.13 se preciso, cria `.venv` e `uv.lock`; pytest termina com `no tests ran` (exit code 5).

- [ ] **Step 4: Commit do scaffold**

```bash
git add pyproject.toml uv.lock .python-version .gitignore src/caching_service/__init__.py tests/__init__.py tests/unit/__init__.py
git commit -m "chore: scaffold project with uv, pytest, ruff and mypy"
```

- [ ] **Step 5: Escrever o teste que falha — `tests/unit/test_domain.py`**

```python
from uuid import UUID

import pytest

from caching_service.domain import build_output, payload_id


def test_build_output_interleaves_lists_starting_with_the_first() -> None:
    assert build_output(["A1", "A2", "A3"], ["B1", "B2", "B3"]) == "A1, B1, A2, B2, A3, B3"


def test_build_output_rejects_lists_of_different_length() -> None:
    with pytest.raises(ValueError):
        build_output(["A1", "A2"], ["B1"])


def test_payload_id_is_stable_for_identical_input() -> None:
    assert payload_id(["a", "b"], ["c", "d"]) == payload_id(["a", "b"], ["c", "d"])


def test_payload_id_never_changes_for_a_known_input() -> None:
    # Ids are persisted: changing the canonical form would orphan every stored payload.
    assert payload_id(["a"], ["b"]) == UUID("5919001c-2d11-5440-91c4-6d299c2190b4")


def test_payload_id_changes_with_order_or_list_assignment() -> None:
    reference = payload_id(["a", "b"], ["c", "d"])
    assert payload_id(["b", "a"], ["c", "d"]) != reference
    assert payload_id(["c", "d"], ["a", "b"]) != reference


def test_payload_id_keeps_string_boundaries() -> None:
    # Naively joining the strings would turn both inputs into "a, b, c".
    assert payload_id(["a, b", "c"], ["x", "y"]) != payload_id(["a", "b, c"], ["x", "y"])
```

- [ ] **Step 6: Rodar e ver falhar**

Run: `uv run pytest tests/unit/test_domain.py -v`
Expected: FAIL na coleta com `ModuleNotFoundError: No module named 'caching_service.domain'`

- [ ] **Step 7: Implementar `src/caching_service/domain.py`**

```python
"""Pure payload rules: how an output is assembled and how a payload is identified.

Kept free of I/O so the core behaviour is testable without a database or a network.
"""

import json
from collections.abc import Sequence
from itertools import chain
from uuid import NAMESPACE_URL, UUID, uuid5

# Derived instead of random so ids stay identical across deployments and restarts.
_PAYLOAD_NAMESPACE = uuid5(NAMESPACE_URL, "caching-service/payload")


def build_output(first: Sequence[str], second: Sequence[str]) -> str:
    """Interleave two equally long lists: first[0], second[0], first[1], second[1], ..."""
    return ", ".join(chain.from_iterable(zip(first, second, strict=True)))


def payload_id(list_1: Sequence[str], list_2: Sequence[str]) -> UUID:
    """Content-derived id: identical input always maps to the same payload, with no lookup.

    JSON keeps list and string boundaries unambiguous, unlike joining with a separator.
    """
    canonical = json.dumps([list(list_1), list(list_2)], ensure_ascii=False, separators=(",", ":"))
    return uuid5(_PAYLOAD_NAMESPACE, canonical)
```

- [ ] **Step 8: Rodar e ver passar**

Run: `uv run pytest tests/unit/test_domain.py -v`
Expected: 6 passed

- [ ] **Step 9: Lint, tipos e commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add src/caching_service/domain.py tests/unit/test_domain.py
git commit -m "feat(domain): add output interleaving and content-derived payload ids"
```

---

### Task 2: Portas + caso de uso `PayloadService`

**Files:**
- Create: `src/caching_service/application/__init__.py` (vazio), `src/caching_service/application/ports.py`, `src/caching_service/application/service.py`, `tests/support.py`, `tests/unit/test_service.py`

**Interfaces:**
- Consumes: `build_output`, `payload_id` (Task 1).
- Produces:
  - `Transformer.transform(value: str) -> str` (async)
  - `TransformationCache.get_many(values: Collection[str]) -> dict[str, str]`, `.put_many(results: Mapping[str, str]) -> None` (async)
  - `PayloadRepository.get(payload_id: UUID) -> str | None`, `.add(payload_id: UUID, output: str) -> None` (async)
  - `PayloadService(transformer, cache, payloads)` com `async create(list_1, list_2) -> CreatedPayload` e `async get(payload_id: UUID) -> str`
  - `CreatedPayload(id: UUID, created: bool)`, `PayloadNotFoundError(LookupError)`, `TransformerError(RuntimeError)`
  - `tests/support.py`: `CountingTransformer(fail_on=())` com `.calls: list[str]`, `InMemoryCache` com `.data`, `InMemoryPayloads` com `.data`, `SAMPLE_LIST_1`, `SAMPLE_LIST_2`, `SAMPLE_BODY`, `SAMPLE_OUTPUT`

- [ ] **Step 1: Criar os fakes e dados de exemplo — `tests/support.py`**

```python
"""Shared test doubles for the application ports, plus the example from the task brief."""

from collections.abc import Collection, Mapping
from uuid import UUID

SAMPLE_LIST_1 = ["first string", "second string", "third string"]
SAMPLE_LIST_2 = ["other string", "another string", "last string"]
SAMPLE_BODY = {"list_1": SAMPLE_LIST_1, "list_2": SAMPLE_LIST_2}
SAMPLE_OUTPUT = (
    "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"
)


class CountingTransformer:
    """Uppercases like the real adapter, records every call, fails on chosen inputs."""

    def __init__(self, fail_on: Collection[str] = ()) -> None:
        self.calls: list[str] = []
        self._fail_on = set(fail_on)

    async def transform(self, value: str) -> str:
        self.calls.append(value)
        if value in self._fail_on:
            raise RuntimeError(f"transformer unavailable for {value!r}")
        return value.upper()


class InMemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get_many(self, values: Collection[str]) -> dict[str, str]:
        return {value: self.data[value] for value in values if value in self.data}

    async def put_many(self, results: Mapping[str, str]) -> None:
        for value, output in results.items():
            self.data.setdefault(value, output)


class InMemoryPayloads:
    def __init__(self) -> None:
        self.data: dict[UUID, str] = {}

    async def get(self, payload_id: UUID) -> str | None:
        return self.data.get(payload_id)

    async def add(self, payload_id: UUID, output: str) -> None:
        self.data.setdefault(payload_id, output)
```

- [ ] **Step 2: Escrever o teste que falha — `tests/unit/test_service.py`**

```python
from uuid import uuid4

import pytest

from caching_service.application.service import (
    CreatedPayload,
    PayloadNotFoundError,
    PayloadService,
    TransformerError,
)
from tests.support import (
    SAMPLE_LIST_1,
    SAMPLE_LIST_2,
    SAMPLE_OUTPUT,
    CountingTransformer,
    InMemoryCache,
    InMemoryPayloads,
)


@pytest.fixture
def transformer() -> CountingTransformer:
    return CountingTransformer(fail_on={"boom"})


@pytest.fixture
def cache() -> InMemoryCache:
    return InMemoryCache()


@pytest.fixture
def payloads() -> InMemoryPayloads:
    return InMemoryPayloads()


@pytest.fixture
def service(transformer, cache, payloads) -> PayloadService:
    return PayloadService(transformer, cache, payloads)


async def test_create_then_get_returns_interleaved_transformed_output(service) -> None:
    created = await service.create(SAMPLE_LIST_1, SAMPLE_LIST_2)
    assert created.created
    assert await service.get(created.id) == SAMPLE_OUTPUT


async def test_identical_request_reuses_id_without_transformer_calls(service, transformer) -> None:
    first = await service.create(SAMPLE_LIST_1, SAMPLE_LIST_2)
    transformer.calls.clear()
    second = await service.create(SAMPLE_LIST_1, SAMPLE_LIST_2)
    assert second == CreatedPayload(id=first.id, created=False)
    assert transformer.calls == []


async def test_each_distinct_string_is_transformed_once(service, transformer) -> None:
    created = await service.create(["a", "b", "a"], ["b", "a", "c"])
    assert sorted(transformer.calls) == ["a", "b", "c"]
    assert await service.get(created.id) == "A, B, B, A, A, C"


async def test_new_payload_only_transforms_strings_not_seen_before(service, transformer) -> None:
    await service.create(["a", "b"], ["c", "d"])
    transformer.calls.clear()
    await service.create(["d", "c"], ["b", "e"])
    assert transformer.calls == ["e"]


async def test_get_unknown_id_raises_not_found(service) -> None:
    with pytest.raises(PayloadNotFoundError):
        await service.get(uuid4())


async def test_transformer_failure_caches_successes_and_stores_no_payload(
    service, transformer, cache, payloads
) -> None:
    with pytest.raises(TransformerError):
        await service.create(["ok", "boom"], ["fine", "other"])
    assert cache.data == {"ok": "OK", "fine": "FINE", "other": "OTHER"}
    assert payloads.data == {}

    transformer.calls.clear()
    with pytest.raises(TransformerError):
        await service.create(["ok", "boom"], ["fine", "other"])
    assert transformer.calls == ["boom"]  # a retry only pays for what failed
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `uv run pytest tests/unit/test_service.py -v`
Expected: FAIL na coleta com `ModuleNotFoundError: No module named 'caching_service.application'`

- [ ] **Step 4: Implementar `src/caching_service/application/ports.py`**

```python
"""Boundaries the use cases depend on. Adapters in `infrastructure` implement them, which
keeps the application layer free of SQLAlchemy, HTTP and the concrete transformer."""

from collections.abc import Collection, Mapping
from typing import Protocol
from uuid import UUID


class Transformer(Protocol):
    """The external string transformation service. Calls are expensive: minimise them."""

    async def transform(self, value: str) -> str: ...


class TransformationCache(Protocol):
    async def get_many(self, values: Collection[str]) -> dict[str, str]:
        """Cached outputs for the given inputs; inputs never transformed are left out."""
        ...

    async def put_many(self, results: Mapping[str, str]) -> None:
        """Store outputs; entries that already exist are kept as they are."""
        ...


class PayloadRepository(Protocol):
    async def get(self, payload_id: UUID) -> str | None: ...

    async def add(self, payload_id: UUID, output: str) -> None:
        """Store a payload; adding an existing id is a no-op, since creators may race."""
        ...
```

- [ ] **Step 5: Implementar `src/caching_service/application/service.py`**

```python
"""Payload use cases: create (transform + interleave, reusing earlier work) and read."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from caching_service import domain
from caching_service.application.ports import (
    PayloadRepository,
    TransformationCache,
    Transformer,
)


class PayloadNotFoundError(LookupError):
    pass


class TransformerError(RuntimeError):
    """The external transformer failed; the results that did succeed are already cached."""


@dataclass(frozen=True, slots=True)
class CreatedPayload:
    id: UUID
    created: bool  # False when an identical payload already existed


class PayloadService:
    def __init__(
        self,
        transformer: Transformer,
        cache: TransformationCache,
        payloads: PayloadRepository,
    ) -> None:
        self._transformer = transformer
        self._cache = cache
        self._payloads = payloads

    async def create(self, list_1: Sequence[str], list_2: Sequence[str]) -> CreatedPayload:
        payload_id = domain.payload_id(list_1, list_2)
        # Identical input means identical output: skip the cache and the transformer entirely.
        if await self._payloads.get(payload_id) is not None:
            return CreatedPayload(id=payload_id, created=False)

        transformed = await self._transform_all({*list_1, *list_2})
        output = domain.build_output(
            [transformed[value] for value in list_1],
            [transformed[value] for value in list_2],
        )
        await self._payloads.add(payload_id, output)
        return CreatedPayload(id=payload_id, created=True)

    async def get(self, payload_id: UUID) -> str:
        output = await self._payloads.get(payload_id)
        if output is None:
            raise PayloadNotFoundError(payload_id)
        return output

    async def _transform_all(self, values: set[str]) -> dict[str, str]:
        """Transformed form of every distinct value, calling the transformer only on misses."""
        known = await self._cache.get_many(values)
        missing = [value for value in values if value not in known]
        if not missing:
            return known

        # ponytail: unbounded fan-out (capped by request limits); wrap the calls in an
        # asyncio.Semaphore if the real service enforces a rate limit.
        results = await asyncio.gather(
            *(self._transformer.transform(value) for value in missing),
            return_exceptions=True,
        )
        fresh = {
            value: result
            for value, result in zip(missing, results, strict=True)
            if isinstance(result, str)
        }
        # Persist successes before reporting failures, so a retry only pays for what failed.
        await self._cache.put_many(fresh)
        failure = next((r for r in results if isinstance(r, BaseException)), None)
        if failure is not None:
            raise TransformerError("transformer call failed") from failure
        return known | fresh
```

- [ ] **Step 6: Rodar e ver passar**

Run: `uv run pytest tests/unit -v`
Expected: 12 passed

- [ ] **Step 7: Lint, tipos e commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add src/caching_service/application tests/support.py tests/unit/test_service.py
git commit -m "feat(application): add payload service that reuses cached transformer results"
```

---

### Task 3: Banco de dados + repositórios SQL

**Files:**
- Create: `src/caching_service/infrastructure/__init__.py` (vazio), `src/caching_service/infrastructure/database.py`, `src/caching_service/infrastructure/repositories.py`, `tests/integration/__init__.py` (vazio), `tests/integration/conftest.py`, `tests/integration/test_repositories.py`

**Interfaces:**
- Consumes: protocolos `TransformationCache`, `PayloadRepository` (Task 2) — implementados estruturalmente.
- Produces: `Base`, `TransformationRow`, `PayloadRow`, `async create_schema(engine: AsyncEngine) -> None`; `SqlTransformationCache(sessions: async_sessionmaker[AsyncSession])`, `SqlPayloadRepository(sessions: async_sessionmaker[AsyncSession])`; fixtures `database_url`, `engine` e `sessions`.

- [ ] **Step 1: Fixtures de banco — `tests/integration/conftest.py`**

```python
"""Fixtures for tests that run the real adapters against a throwaway database."""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from caching_service.infrastructure.database import Base, create_schema


def sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    # TEST_DATABASE_URL runs the repository tests against PostgreSQL instead (see README).
    return os.environ.get("TEST_DATABASE_URL", sqlite_url(tmp_path / "repositories.db"))


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    await create_schema(engine)
    yield engine
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
```

- [ ] **Step 2: Escrever o teste que falha — `tests/integration/test_repositories.py`**

```python
from uuid import uuid4

from caching_service.application.service import PayloadService
from caching_service.infrastructure.repositories import (
    SqlPayloadRepository,
    SqlTransformationCache,
)


async def test_cache_returns_only_stored_values(sessions) -> None:
    cache = SqlTransformationCache(sessions)
    await cache.put_many({"a": "A", "b": "B"})
    assert await cache.get_many(["a", "b", "missing"]) == {"a": "A", "b": "B"}


async def test_cache_round_trips_non_ascii_and_long_strings(sessions) -> None:
    long_value = "x" * 10_000  # longer than a PostgreSQL B-tree entry may be
    cache = SqlTransformationCache(sessions)
    await cache.put_many(
        {"straße": "STRASSE", "café ☕": "CAFÉ ☕", long_value: long_value.upper()}
    )
    assert await cache.get_many(["straße", "café ☕", long_value]) == {
        "straße": "STRASSE",
        "café ☕": "CAFÉ ☕",
        long_value: long_value.upper(),
    }


async def test_cache_put_is_idempotent_and_keeps_first_value(sessions) -> None:
    cache = SqlTransformationCache(sessions)
    await cache.put_many({"a": "A"})
    await cache.put_many({"a": "ignored", "b": "B"})  # concurrent writers must not fail
    assert await cache.get_many(["a", "b"]) == {"a": "A", "b": "B"}


async def test_cache_accepts_empty_input(sessions) -> None:
    cache = SqlTransformationCache(sessions)
    await cache.put_many({})
    assert await cache.get_many([]) == {}


async def test_payload_round_trip_and_unknown_id(sessions) -> None:
    payloads = SqlPayloadRepository(sessions)
    payload_id = uuid4()
    await payloads.add(payload_id, "A, B")
    assert await payloads.get(payload_id) == "A, B"
    assert await payloads.get(uuid4()) is None


async def test_adding_existing_payload_is_a_noop(sessions) -> None:
    payloads = SqlPayloadRepository(sessions)
    payload_id = uuid4()
    await payloads.add(payload_id, "A, B")
    await payloads.add(payload_id, "A, B")  # two identical POSTs racing each other
    assert await payloads.get(payload_id) == "A, B"


async def test_no_connection_is_held_while_the_transformer_runs(engine, sessions) -> None:
    checked_out_during_transform: list[int] = []

    class ProbeTransformer:
        async def transform(self, value: str) -> str:
            checked_out_during_transform.append(engine.pool.checkedout())
            return value.upper()

    service = PayloadService(
        ProbeTransformer(), SqlTransformationCache(sessions), SqlPayloadRepository(sessions)
    )
    await service.create(["a"], ["b"])

    # A held connection would starve the pool for the whole (slow, remote) transformer call.
    assert checked_out_during_transform == [0, 0]
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `uv run pytest tests/integration/test_repositories.py -v`
Expected: FAIL na coleta com `ModuleNotFoundError: No module named 'caching_service.infrastructure'`

- [ ] **Step 4: Implementar `src/caching_service/infrastructure/database.py`**

```python
"""SQLAlchemy schema shared by the repositories and the app's startup."""

from uuid import UUID

from sqlalchemy import String, Text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TransformationRow(Base):
    __tablename__ = "transformations"

    # SHA-256 of the input rather than the input itself: fixed-size keys keep the index small
    # and stay below PostgreSQL's B-tree entry limit (~2.7 kB) for long strings.
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    output: Mapped[str] = mapped_column(Text)


class PayloadRow(Base):
    __tablename__ = "payloads"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    output: Mapped[str] = mapped_column(Text)


async def create_schema(engine: AsyncEngine) -> None:
    # ponytail: create_all at startup; move to Alembic migrations once the schema changes
    # or several replicas start at the same time (create_all is check-then-create).
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
```

- [ ] **Step 5: Implementar `src/caching_service/infrastructure/repositories.py`**

```python
"""SQL adapters for the application ports.

Each method opens a short session of its own: every write commits on its own, so transformer
results, which are expensive to obtain, survive even when the rest of the request fails, and
no connection is held between calls, so none is held while the transformer runs.
"""

import hashlib
from collections.abc import Collection, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from caching_service.infrastructure.database import Base, PayloadRow, TransformationRow


def _cache_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _insert_ignoring_conflicts(
    session: AsyncSession, model: type[Base]
) -> postgresql.Insert | sqlite.Insert:
    # Concurrent requests may write the same row; ON CONFLICT DO NOTHING makes that harmless.
    if session.get_bind().dialect.name == "postgresql":
        return postgresql.insert(model).on_conflict_do_nothing()
    return sqlite.insert(model).on_conflict_do_nothing()


class SqlTransformationCache:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_many(self, values: Collection[str]) -> dict[str, str]:
        value_by_key = {_cache_key(value): value for value in values}
        if not value_by_key:
            return {}
        async with self._sessions() as session:
            rows = await session.execute(
                select(TransformationRow.key, TransformationRow.output).where(
                    TransformationRow.key.in_(list(value_by_key))
                )
            )
            return {value_by_key[key]: output for key, output in rows}

    async def put_many(self, results: Mapping[str, str]) -> None:
        if not results:
            return
        rows = [{"key": _cache_key(value), "output": output} for value, output in results.items()]
        async with self._sessions.begin() as session:
            await session.execute(_insert_ignoring_conflicts(session, TransformationRow), rows)


class SqlPayloadRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get(self, payload_id: UUID) -> str | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(PayloadRow.output).where(PayloadRow.id == payload_id)
            )

    async def add(self, payload_id: UUID, output: str) -> None:
        async with self._sessions.begin() as session:
            statement = _insert_ignoring_conflicts(session, PayloadRow).values(
                id=payload_id, output=output
            )
            await session.execute(statement)
```

Cada método abre a própria sessão: `sessions()` nas leituras, `sessions.begin()` nas escritas (commit ao sair, sem `commit()` explícito), e as linhas são iteradas direto porque `Result.tuples()` está deprecado no SQLAlchemy 2.1. Assim nenhuma conexão fica presa enquanto o transformer roda (D5), o que `test_no_connection_is_held_while_the_transformer_runs` trava.

- [ ] **Step 6: Rodar e ver passar**

Run: `uv run pytest -v`
Expected: 19 passed (12 + 7)

- [ ] **Step 7: Lint, tipos e commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add src/caching_service/infrastructure/__init__.py src/caching_service/infrastructure/database.py src/caching_service/infrastructure/repositories.py tests/integration
git commit -m "feat(infrastructure): add SQL transformation cache and payload repository"
```

O código acima já é a forma final. A primeira versão usava uma sessão por request e foi corrigida na revisão final com `fix(infrastructure): release DB connections while awaiting the transformer` (ver Histórico de execução).

---

### Task 4: Transformer simulado, configuração e API HTTP

**Files:**
- Create: `src/caching_service/infrastructure/transformer.py`, `src/caching_service/config.py`, `src/caching_service/api/__init__.py` (vazio), `src/caching_service/api/schemas.py`, `src/caching_service/api/dependencies.py`, `src/caching_service/api/routes.py`, `src/caching_service/main.py`, `tests/unit/test_transformer.py`, `tests/integration/test_api.py`
- Modify: `tests/integration/conftest.py` (adiciona fixtures `transformer` e `client`)

**Interfaces:**
- Consumes: `PayloadService`, `PayloadNotFoundError`, `TransformerError`, `Transformer` (Task 2); `SqlTransformationCache`, `SqlPayloadRepository`, `create_schema` (Task 3); `CountingTransformer`, `SAMPLE_BODY`, `SAMPLE_OUTPUT` (`tests/support.py`).
- Produces:
  - `UppercaseTransformer(latency_seconds: float = 0.0)`
  - `Settings(database_url: str, transformer_latency_seconds: float)` lidos de `CACHE_DATABASE_URL`, `CACHE_TRANSFORMER_LATENCY_SECONDS`
  - `PayloadRequest(list_1, list_2)`, `PayloadCreated(id, message)`, `PayloadOut(output)`, `MAX_ITEMS`, `MAX_STRING_LENGTH` em `caching_service.api.schemas` (o CLI reusa `PayloadRequest`)
  - `create_app(settings: Settings | None = None, transformer: Transformer | None = None) -> FastAPI`
  - fixtures `transformer` (`CountingTransformer(fail_on={"boom"})`) e `client` (`TestClient`)

- [ ] **Step 1: Teste que falha do transformer — `tests/unit/test_transformer.py`**

```python
from caching_service.infrastructure.transformer import UppercaseTransformer


async def test_uppercase_transformer_uppercases_like_the_task_example() -> None:
    assert await UppercaseTransformer().transform("first string") == "FIRST STRING"
```

Run: `uv run pytest tests/unit/test_transformer.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'caching_service.infrastructure.transformer'`

- [ ] **Step 2: Implementar `src/caching_service/infrastructure/transformer.py`**

```python
"""Stand-in for the external transformation service the task asks us to simulate."""

import asyncio


class UppercaseTransformer:
    """Uppercases after a configurable delay, so the cost of a remote call is visible."""

    def __init__(self, latency_seconds: float = 0.0) -> None:
        self._latency_seconds = latency_seconds

    async def transform(self, value: str) -> str:
        await asyncio.sleep(self._latency_seconds)
        return value.upper()
```

Run: `uv run pytest tests/unit/test_transformer.py -v`
Expected: 1 passed

- [ ] **Step 3: Commit do transformer**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add src/caching_service/infrastructure/transformer.py tests/unit/test_transformer.py
git commit -m "feat(infrastructure): add simulated uppercase transformer service"
```

- [ ] **Step 4: Substituir `tests/integration/conftest.py` pela versão com app**

```python
"""Fixtures for tests that run the real adapters: a throwaway database and the app in-process."""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from caching_service.config import Settings
from caching_service.infrastructure.database import Base, create_schema
from caching_service.main import create_app
from tests.support import CountingTransformer


def sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    # TEST_DATABASE_URL runs the repository tests against PostgreSQL instead (see README).
    return os.environ.get("TEST_DATABASE_URL", sqlite_url(tmp_path / "repositories.db"))


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    await create_schema(engine)
    yield engine
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def transformer() -> CountingTransformer:
    return CountingTransformer(fail_on={"boom"})


@pytest.fixture
def client(tmp_path: Path, transformer: CountingTransformer) -> Iterator[TestClient]:
    # Always a fresh SQLite file: app tests need isolation, not a specific database engine.
    settings = Settings(database_url=sqlite_url(tmp_path / "app.db"))
    with TestClient(create_app(settings, transformer)) as client:
        yield client
```

- [ ] **Step 5: Escrever o teste que falha — `tests/integration/test_api.py`**

```python
from uuid import uuid4

import pytest

from caching_service.api.schemas import MAX_ITEMS, MAX_STRING_LENGTH
from tests.support import SAMPLE_BODY, SAMPLE_OUTPUT


def test_post_creates_payload_and_get_returns_sample_output(client) -> None:
    created = client.post("/payload", json=SAMPLE_BODY)
    assert created.status_code == 201
    assert created.json()["message"] == "Payload created"

    fetched = client.get(f"/payload/{created.json()['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == {"output": SAMPLE_OUTPUT}


def test_repeated_post_returns_same_id_without_transformer_calls(client, transformer) -> None:
    first = client.post("/payload", json=SAMPLE_BODY).json()
    transformer.calls.clear()
    second = client.post("/payload", json=SAMPLE_BODY)
    assert second.status_code == 200
    assert second.json() == {"id": first["id"], "message": "Payload already exists"}
    assert transformer.calls == []


def test_transformations_are_reused_across_different_payloads(client, transformer) -> None:
    client.post("/payload", json={"list_1": ["a"], "list_2": ["b"]})
    transformer.calls.clear()
    created = client.post("/payload", json={"list_1": ["b"], "list_2": ["a"]})
    assert created.status_code == 201  # new payload, yet both strings came from the cache
    assert transformer.calls == []


def test_non_ascii_strings_round_trip(client) -> None:
    created = client.post("/payload", json={"list_1": ["straße"], "list_2": ["café ☕"]}).json()
    assert client.get(f"/payload/{created['id']}").json() == {"output": "STRASSE, CAFÉ ☕"}


def test_get_unknown_id_returns_404(client) -> None:
    assert client.get(f"/payload/{uuid4()}").status_code == 404


def test_get_malformed_id_returns_422(client) -> None:
    assert client.get("/payload/not-a-uuid").status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"list_1": ["a", "b"], "list_2": ["c"]}, id="different-lengths"),
        pytest.param({"list_1": [], "list_2": []}, id="empty-lists"),
        pytest.param({"list_1": ["a"]}, id="missing-list"),
        pytest.param({"list_1": [1], "list_2": ["b"]}, id="non-string-item"),
        pytest.param({"list_1": ["a"], "list_2": ["b"], "list_3": ["c"]}, id="unknown-field"),
        pytest.param(
            {"list_1": ["x" * (MAX_STRING_LENGTH + 1)], "list_2": ["b"]}, id="string-too-long"
        ),
        pytest.param(
            {"list_1": ["a"] * (MAX_ITEMS + 1), "list_2": ["b"] * (MAX_ITEMS + 1)},
            id="too-many-items",
        ),
    ],
)
def test_invalid_body_is_rejected_before_any_transformer_call(client, transformer, body) -> None:
    assert client.post("/payload", json=body).status_code == 422
    assert transformer.calls == []


def test_lone_surrogate_is_rejected_before_any_transformer_call(client, transformer) -> None:
    body = b'{"list_1": ["\\ud800"], "list_2": ["b"]}'
    response = client.post("/payload", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert transformer.calls == []


def test_transformer_failure_returns_502_and_keeps_successful_results(client, transformer) -> None:
    assert client.post("/payload", json={"list_1": ["ok"], "list_2": ["boom"]}).status_code == 502
    transformer.calls.clear()
    assert client.post("/payload", json={"list_1": ["ok"], "list_2": ["fine"]}).status_code == 201
    assert transformer.calls == ["fine"]
```

- [ ] **Step 6: Rodar e ver falhar**

Run: `uv run pytest tests/integration/test_api.py -v`
Expected: FAIL na coleta com `ModuleNotFoundError: No module named 'caching_service.config'`

- [ ] **Step 7: Implementar `src/caching_service/config.py`**

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service configuration, read from CACHE_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="CACHE_")

    database_url: str = "sqlite+aiosqlite:///./cache.db"
    # Simulated latency of the external transformer; makes the effect of caching visible.
    transformer_latency_seconds: float = Field(default=0.1, ge=0)
```

- [ ] **Step 8: Implementar `src/caching_service/api/schemas.py`**

```python
"""HTTP contract. The limits bound how much work one request can trigger."""

from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MAX_ITEMS = 1_000
MAX_STRING_LENGTH = 1_000


Item = Annotated[str, StringConstraints(max_length=MAX_STRING_LENGTH)]


class PayloadRequest(BaseModel):
    # Rejecting unknown fields turns typos such as "list1" into a 422 instead of silent data loss.
    model_config = ConfigDict(extra="forbid")

    list_1: list[Item] = Field(min_length=1, max_length=MAX_ITEMS)
    list_2: list[Item] = Field(min_length=1, max_length=MAX_ITEMS)

    @model_validator(mode="after")
    def _lists_have_same_length(self) -> Self:
        if len(self.list_1) != len(self.list_2):
            raise ValueError("list_1 and list_2 must have the same length")
        return self


class PayloadCreated(BaseModel):
    id: UUID
    message: str


class PayloadOut(BaseModel):
    output: str
```

- [ ] **Step 9: Implementar `src/caching_service/api/dependencies.py`**

```python
"""Wiring: routes receive the application's single PayloadService, built in main.create_app()."""

from typing import Annotated

from fastapi import Depends, Request

from caching_service.application.service import PayloadService


def get_payload_service(request: Request) -> PayloadService:
    # Safe to share across requests: the service holds no state, and its SQL adapters open a
    # short session per call instead of keeping a connection for the whole request.
    service: PayloadService = request.app.state.service
    return service


PayloadServiceDep = Annotated[PayloadService, Depends(get_payload_service)]
```

- [ ] **Step 10: Implementar `src/caching_service/api/routes.py`**

```python
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from caching_service.api.dependencies import PayloadServiceDep
from caching_service.api.schemas import PayloadCreated, PayloadOut, PayloadRequest
from caching_service.application.service import PayloadNotFoundError, TransformerError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payload", tags=["payload"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {"model": PayloadCreated, "description": "Payload already existed"},
        status.HTTP_502_BAD_GATEWAY: {"description": "Transformer service failed"},
    },
)
async def create_payload(
    body: PayloadRequest, response: Response, service: PayloadServiceDep
) -> PayloadCreated:
    try:
        result = await service.create(body.list_1, body.list_2)
    except TransformerError:
        logger.exception("Transformer failed while creating a payload")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Transformer service failed, please retry"
        ) from None
    if not result.created:
        response.status_code = status.HTTP_200_OK
        return PayloadCreated(id=result.id, message="Payload already exists")
    return PayloadCreated(id=result.id, message="Payload created")


@router.get("/{payload_id}", responses={status.HTTP_404_NOT_FOUND: {"description": "Unknown id"}})
async def read_payload(payload_id: UUID, service: PayloadServiceDep) -> PayloadOut:
    try:
        return PayloadOut(output=await service.get(payload_id))
    except PayloadNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payload not found") from None
```

- [ ] **Step 11: Implementar `src/caching_service/main.py`**

```python
"""Composition root: the only place that picks concrete adapters for the ports."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from caching_service.api.routes import router
from caching_service.application.ports import Transformer
from caching_service.application.service import PayloadService
from caching_service.config import Settings
from caching_service.infrastructure.database import create_schema
from caching_service.infrastructure.repositories import (
    SqlPayloadRepository,
    SqlTransformationCache,
)
from caching_service.infrastructure.transformer import UppercaseTransformer


async def _reject_invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    # The default handler echoes each offending value back. JSON allows lone surrogate escapes
    # such as "\ud800"; pydantic rejects them, but they cannot be UTF-8 encoded, so echoing
    # one turns the 422 into a 500.
    errors = [{key: value for key, value in e.items() if key != "input"} for e in exc.errors()]
    return JSONResponse(
        {"detail": jsonable_encoder(errors)}, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT
    )


def create_app(settings: Settings | None = None, transformer: Transformer | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings and transformer instead of patching."""
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(config.database_url)
        await create_schema(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        app.state.service = PayloadService(
            transformer=transformer or UppercaseTransformer(config.transformer_latency_seconds),
            cache=SqlTransformationCache(sessions),
            payloads=SqlPayloadRepository(sessions),
        )
        yield
        await engine.dispose()

    app = FastAPI(title="Caching Service", lifespan=lifespan)
    app.exception_handler(RequestValidationError)(_reject_invalid_request)
    app.include_router(router)
    return app
```

O `main.py` monta o `PayloadService` uma vez no lifespan e registra um handler de 422 que descarta o `input` de cada erro (D17): sem ele, um surrogate solitário no corpo derruba a própria resposta de erro.

- [ ] **Step 12: Rodar e ver passar**

Run: `uv run pytest -v`
Expected: 35 passed (19 + 1 + 15; o teste 502 imprime o traceback logado no stderr capturado — esperado)

- [ ] **Step 13: Smoke manual**

Run (terminal 1): `uv run uvicorn caching_service.main:create_app --factory`
Run (terminal 2): `curl -s -X POST localhost:8000/payload -H "Content-Type: application/json" -d '{"list_1":["first string"],"list_2":["other string"]}'` e depois `curl -s localhost:8000/payload/<id>`
Expected: `{"id":"…","message":"Payload created"}` e `{"output":"FIRST STRING, OTHER STRING"}`; `http://localhost:8000/docs` mostra os dois endpoints. Parar o servidor e apagar `cache.db`.

- [ ] **Step 14: Lint, tipos e commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add src/caching_service/config.py src/caching_service/api src/caching_service/main.py tests/integration/conftest.py tests/integration/test_api.py
git commit -m "feat(api): expose POST /payload and GET /payload/{id}"
```

---

### Task 5: CLI — parsing e sanitização com pydantic-settings

**Files:**
- Create: `src/caching_service/cli.py`, `tests/unit/test_cli_settings.py`

**Interfaces:**
- Consumes: `PayloadRequest` (Task 4).
- Produces: `STDIO = "-"`; `CliSettings` com campos `host: AnyHttpUrl`, `repeat: PositiveInt`, `input_file: str | None`, `json_input: str | None`, `output_file: str` e método `read_payload(stdin: TextIO) -> PayloadRequest`. Construído via `CliApp.run(CliSettings, cli_args=[...])`.

- [ ] **Step 1: Escrever o teste que falha — `tests/unit/test_cli_settings.py`**

```python
import io

import pytest
from pydantic import ValidationError
from pydantic_settings import CliApp

from caching_service.cli import CliSettings

VALID_JSON = '{"list_1": ["a"], "list_2": ["b"]}'


def parse(*args: str) -> CliSettings:
    return CliApp.run(CliSettings, cli_args=list(args))


def test_short_flags_are_parsed() -> None:
    settings = parse("-H", "http://svc:9000", "-r", "3", "-j", VALID_JSON, "-o", "out.jsonl")
    assert str(settings.host) == "http://svc:9000/"
    assert settings.repeat == 3
    assert settings.json_input == VALID_JSON
    assert settings.output_file == "out.jsonl"


def test_long_flags_are_parsed() -> None:
    settings = parse("--host", "http://svc:9000", "--repeat", "2", "--input", "-", "--output", "-")
    assert (str(settings.host), settings.repeat, settings.input_file) == (
        "http://svc:9000/",
        2,
        "-",
    )


def test_defaults_target_the_local_service_once_and_write_to_stdout() -> None:
    settings = parse("-j", VALID_JSON)
    assert (str(settings.host), settings.repeat, settings.output_file) == (
        "http://127.0.0.1:8000/",
        1,
        "-",
    )


def test_payload_is_read_from_inline_json_or_stdin() -> None:
    assert parse("-j", VALID_JSON).read_payload(io.StringIO()).list_1 == ["a"]
    assert parse("-i", "-").read_payload(io.StringIO(VALID_JSON)).list_2 == ["b"]


@pytest.mark.parametrize(
    "args",
    [
        pytest.param([], id="no-input"),
        pytest.param(["-i", "-", "-j", VALID_JSON], id="both-inputs"),
        pytest.param(["-j", "{not json"], id="malformed-json"),
        pytest.param(["-j", '{"list_1": ["a"], "list_2": []}'], id="invalid-payload"),
        pytest.param(["-i", "definitely-missing.json"], id="missing-input-file"),
        pytest.param(["-j", VALID_JSON, "-r", "0"], id="non-positive-repeat"),
        pytest.param(["-j", VALID_JSON, "-H", "not a url"], id="invalid-host"),
    ],
)
def test_invalid_arguments_are_rejected_before_any_request(args: list[str]) -> None:
    with pytest.raises(ValidationError):
        parse(*args)


def test_environment_cannot_redirect_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HOST", "H", "REPEAT"):
        monkeypatch.setenv(name, "http://elsewhere:1")
    assert str(parse("-j", VALID_JSON).host) == "http://127.0.0.1:8000/"


def test_dash_h_shows_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse("-h")
    assert exit_info.value.code == 0
    assert "-H, --host AnyHttpUrl" in capsys.readouterr().out
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/unit/test_cli_settings.py -v`
Expected: FAIL na coleta com `ModuleNotFoundError: No module named 'caching_service.cli'`

- [ ] **Step 3: Implementar `src/caching_service/cli.py` (parte de settings)**

```python
"""cache-cli: drive the caching service from the command line.

Every iteration POSTs the payload and GETs it back, writing one JSON line, so a run with
--repeat shows the cache at work: only the first iteration pays for transformer calls.
"""

from pathlib import Path
from typing import Self, TextIO

from pydantic import AliasChoices, AnyHttpUrl, Field, PositiveInt, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from caching_service.api.schemas import PayloadRequest

STDIO = "-"


class CliSettings(BaseSettings):
    """Send a payload to the caching service and read it back."""

    # case_sensitive keeps "-H" (host) apart from argparse's "-h" (help): the brief lists "-h"
    # for both, which argparse cannot support.
    model_config = SettingsConfigDict(
        cli_prog_name="cache-cli", case_sensitive=True, cli_hide_none_type=True
    )

    # 127.0.0.1, not localhost: uvicorn binds IPv4 by default, and on Windows localhost tries ::1
    # first, costing ~2 s per refused attempt.
    host: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://127.0.0.1:8000"),
        validation_alias=AliasChoices("H", "host"),
        description="Base URL of the caching service",
    )
    repeat: PositiveInt = Field(
        default=1,
        validation_alias=AliasChoices("r", "repeat"),
        description="Number of POST + GET iterations",
    )
    input_file: str | None = Field(
        default=None,
        validation_alias=AliasChoices("i", "input"),
        description='JSON file with list_1 and list_2, "-" for stdin',
    )
    json_input: str | None = Field(
        default=None,
        validation_alias=AliasChoices("j", "json"),
        description="The same JSON, given inline",
    )
    output_file: str = Field(
        default=STDIO,
        validation_alias=AliasChoices("o", "output"),
        description='File for the JSON lines, "-" for stdout',
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Command line only (CliApp adds it): with aliases, env_prefix does not apply, so a
        # stray HOST variable would otherwise silently change where the tool sends data.
        return (init_settings,)

    @field_validator("input_file")
    @classmethod
    def _input_file_exists(cls, value: str | None) -> str | None:
        if value is not None and value != STDIO and not Path(value).is_file():
            raise ValueError(f"input file not found: {value}")
        return value

    @field_validator("json_input")
    @classmethod
    def _json_input_is_a_valid_payload(cls, value: str | None) -> str | None:
        if value is not None:
            PayloadRequest.model_validate_json(value)  # fail before sending any request
        return value

    @model_validator(mode="after")
    def _exactly_one_input(self) -> Self:
        if (self.input_file is None) == (self.json_input is None):
            raise ValueError("provide exactly one of --input or --json")
        return self

    def read_payload(self, stdin: TextIO) -> PayloadRequest:
        if self.json_input is not None:
            return PayloadRequest.model_validate_json(self.json_input)
        path = self.input_file or STDIO  # the validator guarantees one input is set
        raw = stdin.read() if path == STDIO else Path(path).read_text(encoding="utf-8")
        return PayloadRequest.model_validate_json(raw)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest -v`
Expected: 48 passed (35 + 13)

- [ ] **Step 5: Lint, tipos e commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add src/caching_service/cli.py tests/unit/test_cli_settings.py
git commit -m "feat(cli): parse and validate cache-cli arguments with pydantic-settings"
```

---

### Task 6: CLI — execução contra o serviço

**Files:**
- Modify: `src/caching_service/cli.py` (adiciona `run`, `main` e imports)
- Create: `tests/integration/test_cli.py`, `examples/sample.json`

**Interfaces:**
- Consumes: `CliSettings`, `STDIO` (Task 5); fixtures `client`, `transformer` (Task 4); `SAMPLE_BODY`, `SAMPLE_OUTPUT`.
- Produces: `run(settings: CliSettings, client: httpx2.Client, stdin: TextIO, stdout: TextIO) -> None`; `main(argv: list[str] | None = None) -> None` (entry point `cache-cli`, sai com mensagem em vez de traceback, inclusive em `OSError`/`UnicodeDecodeError`).

- [ ] **Step 1: Escrever o teste que falha — `tests/integration/test_cli.py`**

```python
import io
import json
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic_settings import CliApp

from caching_service.cli import CliSettings, main, run
from tests.support import SAMPLE_BODY, SAMPLE_OUTPUT

SAMPLE_JSON = json.dumps(SAMPLE_BODY)


def run_cli(client: TestClient, *args: str, stdin: str = "") -> list[dict[str, object]]:
    """Run the CLI against the in-process app (TestClient is an httpx2.Client)."""
    settings = CliApp.run(CliSettings, cli_args=list(args))
    stdout = io.StringIO()
    run(settings, client, io.StringIO(stdin), stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def test_repeat_reuses_the_payload_and_transforms_each_string_once(client, transformer) -> None:
    records = run_cli(client, "-j", SAMPLE_JSON, "-r", "3")
    assert [record["iteration"] for record in records] == [1, 2, 3]
    assert len({record["id"] for record in records}) == 1
    assert {record["output"] for record in records} == {SAMPLE_OUTPUT}
    assert len(transformer.calls) == 6  # six distinct strings, paid for once


def test_reads_payload_from_stdin(client) -> None:
    [record] = run_cli(client, "-i", "-", stdin=SAMPLE_JSON)
    assert record["output"] == SAMPLE_OUTPUT


def test_reads_input_file_and_writes_output_file(client, tmp_path: Path) -> None:
    source, target = tmp_path / "in.json", tmp_path / "out.jsonl"
    source.write_text(SAMPLE_JSON, encoding="utf-8")
    assert run_cli(client, "-i", str(source), "-o", str(target)) == []
    [line] = target.read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["output"] == SAMPLE_OUTPUT


def test_server_errors_are_raised_as_http_status_errors(client) -> None:
    with pytest.raises(httpx2.HTTPStatusError):
        run_cli(client, "-j", '{"list_1": ["boom"], "list_2": ["x"]}')


def test_main_exits_with_a_message_on_invalid_arguments() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["-j", "{not json"])
    assert "invalid input" in str(exit_info.value.code)


def test_main_exits_with_a_message_when_the_host_is_unreachable() -> None:
    # Port 9 (discard) is closed on a normal machine; on Windows the refusal takes ~2 s.
    with pytest.raises(SystemExit) as exit_info:
        main(["-H", "http://127.0.0.1:9", "-j", SAMPLE_JSON])
    assert "request failed" in str(exit_info.value.code)


def test_main_exits_with_a_message_when_the_output_cannot_be_written(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["-j", SAMPLE_JSON, "-o", str(tmp_path / "missing-dir" / "out.jsonl")])
    assert str(exit_info.value.code).startswith("cache-cli: ")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/integration/test_cli.py -v`
Expected: FAIL na coleta com `ImportError: cannot import name 'main' from 'caching_service.cli'`

- [ ] **Step 3: Adicionar imports no topo de `src/caching_service/cli.py`**

Substituir tudo entre a docstring do módulo e `class CliSettings` (imports + `STDIO = "-"` da Task 5) por:

```python
import json
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Self, TextIO

import httpx2
from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    Field,
    PositiveInt,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, CliApp, PydanticBaseSettingsSource, SettingsConfigDict

from caching_service.api.schemas import PayloadRequest

STDIO = "-"
REQUEST_TIMEOUT_SECONDS = 30.0
```

- [ ] **Step 4: Adicionar `run` e `main` no fim de `src/caching_service/cli.py`**

```python
@contextmanager
def _open_output(path: str, stdout: TextIO) -> Iterator[TextIO]:
    if path == STDIO:
        yield stdout
    else:
        with open(path, "w", encoding="utf-8") as file:
            yield file


def run(settings: CliSettings, client: httpx2.Client, stdin: TextIO, stdout: TextIO) -> None:
    """POST + GET the payload `settings.repeat` times, writing one JSON line per iteration."""
    body = settings.read_payload(stdin).model_dump()
    with _open_output(settings.output_file, stdout) as out:
        for iteration in range(1, settings.repeat + 1):
            started = time.perf_counter()
            payload_id = client.post("/payload", json=body).raise_for_status().json()["id"]
            output = client.get(f"/payload/{payload_id}").raise_for_status().json()["output"]
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            record = {
                "iteration": iteration,
                "id": payload_id,
                "output": output,
                "elapsed_ms": elapsed_ms,
            }
            # ASCII-escaped: a redirected stdout on Windows may use a legacy code page.
            out.write(json.dumps(record) + "\n")


def main(argv: list[str] | None = None) -> None:
    """Entry point of `cache-cli`: expected failures end with a message, not a traceback."""
    try:
        settings = CliApp.run(CliSettings, cli_args=argv)
        with httpx2.Client(base_url=str(settings.host), timeout=REQUEST_TIMEOUT_SECONDS) as client:
            run(settings, client, sys.stdin, sys.stdout)
    except ValidationError as exc:
        sys.exit(f"cache-cli: invalid input\n{exc}")
    except httpx2.HTTPStatusError as exc:
        sys.exit(f"cache-cli: server answered {exc.response.status_code}: {exc.response.text}")
    except httpx2.TransportError as exc:
        sys.exit(f"cache-cli: request failed: {exc!r}")
    except (OSError, UnicodeDecodeError) as exc:
        sys.exit(f"cache-cli: {exc}")
```

- [ ] **Step 5: Rodar e ver passar**

Run: `uv run pytest -v`
Expected: 55 passed (48 + 7; o teste de host inacessível leva ~2 s no Windows)

- [ ] **Step 6: Criar `examples/sample.json`**

```json
{
  "list_1": ["first string", "second string", "third string"],
  "list_2": ["other string", "another string", "last string"]
}
```

- [ ] **Step 7: Smoke manual do entry point**

Run (terminal 1): `uv run uvicorn caching_service.main:create_app --factory`
Run (terminal 2): `uv run cache-cli -i examples/sample.json -r 3` e `uv run cache-cli --help`
Expected: 3 linhas JSON com o mesmo `id` e `output` do enunciado; `elapsed_ms` da 1ª ≥ ~100 (latência simulada), das seguintes bem menor. `--help` lista `-H, --host`. Parar o servidor e apagar `cache.db`.

- [ ] **Step 8: Lint, tipos e commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add src/caching_service/cli.py tests/integration/test_cli.py examples/sample.json
git commit -m "feat(cli): run repeated POST/GET iterations against the service"
```

O código acima já inclui as correções de `fix(cli): exit cleanly on file errors and default to 127.0.0.1` e `fix(cli): emit ASCII-escaped JSON lines so any console encoding works` (ver Histórico de execução).

---

### Task 7: Docker + Compose com PostgreSQL

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `compose.yaml`

**Interfaces:**
- Consumes: `create_app` (via `uvicorn --factory`), `CACHE_DATABASE_URL` (Task 4), fixture `database_url` com `TEST_DATABASE_URL` (Task 3).
- Produces: imagem que serve a API na porta 8000; stack `api` + `db` (Postgres 17) via `docker compose up`.

- [ ] **Step 1: Criar `Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1

FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first: this layer stays cached until the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim
RUN useradd --create-home --uid 1000 app && mkdir /data && chown app /data
COPY --from=build /app/.venv /app/.venv
# SQLite under /data by default so the image runs standalone; Compose points it to PostgreSQL.
ENV PATH="/app/.venv/bin:$PATH" \
    CACHE_DATABASE_URL="sqlite+aiosqlite:////data/cache.db"
USER app
EXPOSE 8000
CMD ["uvicorn", "caching_service.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Criar `.dockerignore`**

```
.git
.venv
**/__pycache__
.pytest_cache
.mypy_cache
.ruff_cache
**/*.db
docs
examples
tests
```

- [ ] **Step 3: Criar `compose.yaml`**

```yaml
services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      CACHE_DATABASE_URL: postgresql+asyncpg://cache:cache@db:5432/cache
    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:17-alpine
    environment:
      # Local development credentials only.
      POSTGRES_USER: cache
      POSTGRES_PASSWORD: cache
      POSTGRES_DB: cache
    ports:
      # 127.0.0.1 keeps the dev database off the network.
      - "127.0.0.1:5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U cache -d cache"]
      interval: 2s
      timeout: 5s
      retries: 15
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

- [ ] **Step 4: Imagem standalone (SQLite)**

Run: `docker build -t caching-service . && docker run --rm -d -p 8000:8000 --name caching-service caching-service`
Run: `uv run cache-cli -i examples/sample.json -r 2`
Expected: 2 linhas, mesmo `id`, output do enunciado. Depois `docker stop caching-service`.

- [ ] **Step 5: Stack com PostgreSQL**

Run: `docker compose up --build -d && uv run cache-cli -i examples/sample.json -r 3`
Expected: 3 linhas, mesmo `id`; `docker compose logs api` sem erros.

- [ ] **Step 6: Testes de repositório contra PostgreSQL (banco separado do da API)**

Run: `docker compose exec db createdb -U cache cache_test`
Antes: pare qualquer PostgreSQL instalado localmente na porta 5432, senão `localhost:5432` aponta para ele e não para o container.
Run (Git Bash): `TEST_DATABASE_URL=postgresql+asyncpg://cache:cache@localhost:5432/cache_test uv run pytest tests/integration/test_repositories.py -v`
Run (PowerShell): `$env:TEST_DATABASE_URL="postgresql+asyncpg://cache:cache@localhost:5432/cache_test"; uv run pytest tests/integration/test_repositories.py -v; Remove-Item Env:TEST_DATABASE_URL`
Expected: 7 passed (inclui string de 10 000 chars — valida a chave SHA-256 da D3 e o `ON CONFLICT` do dialeto postgresql).
Then: `docker compose down`

- [ ] **Step 7: Commit**

```bash
git add Dockerfile .dockerignore compose.yaml
git commit -m "build: dockerize the service and add a compose stack with PostgreSQL"
```

Depois, em commits próprios: `build(compose): publish PostgreSQL on host port 55432`, `build(compose): harden PostgreSQL healthcheck and local-only port` e `build(compose): publish PostgreSQL on the default port 5432` (ver Histórico de execução).

---

### Task 8: README + verificação final

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: tudo acima.
- Produces: documentação para a banca e para o vídeo (fluxo endpoint → banco → volta, CLI, testes).

- [ ] **Step 1: Escrever `README.md`**

````markdown
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
TEST_DATABASE_URL=postgresql+asyncpg://cache:cache@localhost:5432/cache_test \
  uv run pytest tests/integration/test_repositories.py
```

Compose publishes PostgreSQL on `127.0.0.1:5432`; stop any locally installed PostgreSQL on that port first.

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
````

- [ ] **Step 2: Verificação completa**

Run: `uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest -v`
Expected: ruff e mypy sem erros; `55 passed`.

- [ ] **Step 3: Conferir o README contra o código**

Executar os comandos do "Quick start" (Docker e local) e do CLI exatamente como escritos; cada um deve funcionar sem ajuste. Corrigir o README se algo divergir.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add README with architecture, design decisions and shortcuts"
```

O README acima é o final, depois de `docs: clarify that the local quick start needs two terminals` e `docs: clarify architecture, shell-portable quick start and known races`.

---

## Histórico de execução

Execução subagent-driven: um implementador por task, uma revisão de spec e qualidade por task, rodadas de correção quando necessário, uma revisão final do branch inteiro e uma última leva de correções. Os blocos de código acima mostram o estado final; esta tabela registra onde a execução se afastou do plano original e por quê.

| Desvio | Onde | Por quê |
|--------|------|---------|
| `rows.tuples()` → iterar as linhas direto | T3 | `Result.tuples()` está deprecado no SQLAlchemy 2.1. |
| Handler de 422 que descarta o `input`, em vez de um validator nas strings | T4 | O FastAPI ecoava o surrogate solitário e a resposta de erro quebrava (500); o validator seria código morto, pois `StringConstraints` já rejeita. |
| Help com `-H, --host AnyHttpUrl` | T5 | Formato do argparse no Python 3.13. |
| Saída com mensagem em `OSError`/`UnicodeDecodeError` e host padrão `127.0.0.1` | T6 (rodada de correção) | Arquivo de saída não gravável ou entrada não-UTF-8 geravam traceback; `localhost` custava ~2 s por tentativa recusada no Windows. |
| Healthcheck via TCP, bind em loopback e `**/*.db` no `.dockerignore` | T7 (rodada de correção) | O healthcheck pelo socket pode passar antes de o TCP subir; o banco de dev fica fora da rede; `*.db` só ignorava a raiz. |
| Quick start do README em dois terminais | T8 (rodada de correção) | O servidor local fica em primeiro plano, então o CLI precisa de outro terminal. |
| Sessão por operação + teste de regressão (F1 da revisão final) | T3 | A sessão por request segurava uma conexão em transação aberta durante o fan-out do transformer, esgotando o pool sob carga. |
| Linhas JSON ASCII-escapadas (F3) | T6 | O console cp1252 do Windows quebrava com "☕". |
| Quick start portável com `-i examples/sample.json` (F4) | T8 | JSON inline entre aspas simples só funciona em shells POSIX. |
| Porquê dos limites de entrada no README (F5) | T8 | Os limites existem para que uma requisição não dispare trabalho ilimitado. |
| Atalho do 201 concorrente documentado (F6) | T8 | Dois POSTs idênticos simultâneos podem ambos responder 201; os dados seguem corretos. |
| Porta do PostgreSQL: 5432 → 55432 → 5432 | T7 | Um PostgreSQL nativo na máquina ocupava a 5432; depois voltou ao padrão por escolha do autor (o README avisa para parar o local). |

Commits reais, em ordem:

1. `81c5bd2` chore: scaffold project with uv, pytest, ruff and mypy
2. `30f9215` feat(domain): add output interleaving and content-derived payload ids
3. `abd540b` feat(application): add payload service that reuses cached transformer results
4. `2102391` feat(infrastructure): add SQL transformation cache and payload repository
5. `e968c55` feat(infrastructure): add simulated uppercase transformer service
6. `fb930ab` feat(api): expose POST /payload and GET /payload/{id}
7. `6e8fc2d` feat(cli): parse and validate cache-cli arguments with pydantic-settings
8. `99f826e` feat(cli): run repeated POST/GET iterations against the service
9. `ca76396` fix(cli): exit cleanly on file errors and default to 127.0.0.1
10. `682fa06` build: dockerize the service and add a compose stack with PostgreSQL
11. `1c7272a` build(compose): publish PostgreSQL on host port 55432
12. `7f4bc80` build(compose): harden PostgreSQL healthcheck and local-only port
13. `4cc3ec1` docs: add README with architecture, design decisions and shortcuts
14. `bb1ac2b` docs: clarify that the local quick start needs two terminals
15. `3c7577c` fix(infrastructure): release DB connections while awaiting the transformer
16. `03e5b83` fix(cli): emit ASCII-escaped JSON lines so any console encoding works
17. `5e3d72a` docs: clarify architecture, shell-portable quick start and known races
18. `5c50865` build(compose): publish PostgreSQL on the default port 5432

Estado final: 55 testes passando; ruff e mypy strict limpos; stack do Compose e os 7 testes de repositório verificados em PostgreSQL.

---

## Entrega (manual, fora das tasks)

- [x] Repositório público criado (https://github.com/fenilonilo/Caching-Service), histórico mantido, um push por commit.
- [ ] Gravar o vídeo (≤15 min, inglês, câmera ligada): seguir `POST /payload` → schemas → dependencies → `PayloadService.create` → repositórios → banco e volta; depois CLI (`-r 3` mostrando `elapsed_ms`) e testes (unit com fakes vs integration com banco real). Dica: rodar `docker compose down -v` antes de gravar a demo de latência, senão o volume `pgdata` já guarda o payload e a 1ª iteração não paga o transformer.
- [ ] Anotar as horas reais gastas para informar na resposta do e-mail.
