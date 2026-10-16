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
