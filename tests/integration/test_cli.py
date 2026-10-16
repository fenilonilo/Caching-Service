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
