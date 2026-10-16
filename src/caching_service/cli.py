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

    host: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8000"),
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
