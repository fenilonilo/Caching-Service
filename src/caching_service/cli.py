"""cache-cli: drive the caching service from the command line.

Every iteration POSTs the payload and GETs it back, writing one JSON line, so a run with
--repeat shows the cache at work: only the first iteration pays for transformer calls.
"""

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
            out.write(json.dumps(record, ensure_ascii=False) + "\n")


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
