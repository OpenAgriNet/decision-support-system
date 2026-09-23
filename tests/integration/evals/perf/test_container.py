"""Tier 2 — starting the load-mode DSS in a container pinned to 1 CPU and 1 GiB.

Against a fake runtime: a shell script standing in for `podman`/`docker` that
records its arguments and prints what the real one would. No container runs.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from evals.perf.container import (
    container_env,
    limits,
    logs,
    missing_model_key,
    start,
)


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    """A fake `podman`: logs each call, answers `run` and `inspect`."""

    script = tmp_path / "podman"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> {tmp_path}/calls\n'
        'case "$1" in\n'
        "  run) echo c0ffee ;;\n"
        "  inspect) echo '1000000000 1073741824' ;;\n"
        "  logs) echo 'INFO: started'; echo 'ERROR: no model key' >&2 ;;\n"
        "esac\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _calls(runtime: Path) -> list[str]:
    return (runtime.parent / "calls").read_text().splitlines()


def test_the_dss_starts_pinned_with_secrets_passed_by_name_only(runtime: Path):
    """The limits are what make the load figures mean "one pod". A secret's
    value never goes on the command line, where `ps` would show it: the
    runtime copies it from this process's environment by name."""

    container = start(
        str(runtime),
        "dss:bench",
        cpus="1",
        memory="1g",
        port=8087,
        pack_dir=Path("/repo/var/schema-packs"),
        pass_env=["AZURE_OPENAI_API_KEY"],
        set_env={"DSS_DISCOVERY_BASE_URL": "http://host.containers.internal:8078"},
    )

    (call,) = _calls(runtime)
    assert container == "c0ffee"
    assert "--cpus 1 --memory 1g" in call
    assert "-e AZURE_OPENAI_API_KEY " in call
    assert "-e DSS_DISCOVERY_BASE_URL=http://host.containers.internal:8078" in call
    assert "-v /repo/var/schema-packs:/packs:ro" in call
    assert "-e DSS_SCHEMA_PACK_DIR=/packs" in call
    assert call.endswith("dss:bench")


def test_the_container_gets_the_dss_settings_with_host_urls_rewritten():
    """Inside a container, `localhost` is the container itself. A setting
    pointing at the host — Langfuse, a local model server — must name the
    host instead. Everything else is passed by name, values unseen."""

    environ = {
        "AZURE_OPENAI_API_KEY": "secret",
        "DSS_PLANNER_MODEL": "openai:gemma-4-31b-it",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:3000/api/public/otel",
        "DSS_DISCOVERY_BASE_URL": "http://127.0.0.1:8078",
        "DSS_SCHEMA_PACK_DIR": "/repo/var/schema-packs",
        "HOME": "/Users/someone",
    }

    pass_env, set_env = container_env(environ, host="host.test", mock_port=8078)

    assert sorted(pass_env) == ["AZURE_OPENAI_API_KEY", "DSS_PLANNER_MODEL"]
    assert set_env == {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://host.test:3000/api/public/otel",
        "DSS_DISCOVERY_BASE_URL": "http://host.test:8078",
        "DSS_INVOCATION_BASE_URL": "http://host.test:8078",
    }


def test_a_shell_with_no_model_key_is_caught_before_anything_starts():
    """The container gets only what this shell exports. With no model key,
    every turn fails on its first model call — better said up front than
    after building an image and sending turns."""

    assert missing_model_key({"DSS_PLANNER_MODEL": "openai:x"}) is True
    assert missing_model_key({"OPENAI_API_KEY": "k"}) is False
    assert missing_model_key({"AZURE_OPENAI_API_KEY": "k"}) is False


def test_the_limits_are_read_back_from_the_running_container(runtime: Path):
    """Read back, not echoed from the flags: the report states what the
    container actually got."""

    assert limits(str(runtime), "c0ffee") == {"cpus": 1.0, "memory_gib": 1.0}


def test_the_containers_log_is_kept_both_streams(runtime: Path):
    """The container is removed when it stops, taking its log with it. The
    log is how a failing load run explains itself, and uvicorn writes most
    of it to stderr."""

    text = logs(str(runtime), "c0ffee")

    assert "INFO: started" in text and "ERROR: no model key" in text
