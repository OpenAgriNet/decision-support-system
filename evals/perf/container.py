"""The load-mode DSS, in a container pinned to a set CPU and memory.

Pinned so the load figures mean "one pod" rather than "this laptop": on a
machine with many cores the DSS would get more CPU than a pod does, and look
better under load than it is. The runtime is Podman when present, else Docker,
the same choice `scripts/run-local.sh` makes.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

# Where the schema packs are mounted. The image carries only `src/`.
_PACKS = "/packs"
# The settings the DSS and its model and tracing clients read.
_SETTINGS = ("DSS_", "OPENAI_", "AZURE_OPENAI_", "OTEL_")
_LOCAL = re.compile(r"localhost|127\.0\.0\.1")
# One of these must be set, for whichever provider the models are bound to.
_MODEL_KEYS = ("OPENAI_API_KEY", "AZURE_OPENAI_API_KEY")


def runtime() -> str:
    return "podman" if shutil.which("podman") else "docker"


def host_alias(runtime: str) -> str:
    """The name a container reaches the host by: the mock and Langfuse run
    there."""

    return (
        "host.containers.internal"
        if Path(runtime).name == "podman"
        else "host.docker.internal"
    )


def missing_model_key(environ: Mapping[str, str]) -> bool:
    """Whether this shell lacks every model key. The container gets only what
    is exported here, so without one each turn fails on its first model call.
    """

    return not any(environ.get(key) for key in _MODEL_KEYS)


def container_env(
    environ: Mapping[str, str], *, host: str, mock_port: int
) -> tuple[list[str], dict[str, str]]:
    """What the container's environment carries: names to pass through, and
    values to set.

    Settings the DSS reads are passed by name, values unseen. One pointing at
    `localhost` is rewritten to `host`, because inside a container `localhost`
    is the container itself. The mock is always the host's.
    """

    pass_env: list[str] = []
    set_env: dict[str, str] = {}
    for name, value in environ.items():
        if not name.startswith(_SETTINGS) or name == "DSS_SCHEMA_PACK_DIR":
            continue
        if _LOCAL.search(value):
            set_env[name] = _LOCAL.sub(host, value)
        else:
            pass_env.append(name)
    mock = f"http://{host}:{mock_port}"
    set_env["DSS_DISCOVERY_BASE_URL"] = mock
    set_env["DSS_INVOCATION_BASE_URL"] = mock
    return pass_env, set_env


def build(runtime: str, image: str, context: Path) -> None:
    subprocess.run([runtime, "build", "-t", image, str(context)], check=True)


def start(
    runtime: str,
    image: str,
    *,
    cpus: str,
    memory: str,
    port: int,
    pack_dir: Path,
    pass_env: list[str],
    set_env: dict[str, str],
) -> str:
    """Start the DSS and return the container's id.

    `pass_env` names are passed by name only (`-e NAME`), so a secret's value
    never appears on the command line: the runtime copies it from this
    process's environment. `set_env` is for values that must change inside
    the container, such as a URL pointing at the host.
    """

    command = [
        runtime,
        "run",
        "-d",
        "--rm",
        "--cpus",
        cpus,
        "--memory",
        memory,
        "-p",
        f"{port}:8077",
        "-v",
        f"{pack_dir}:{_PACKS}:ro",
        "-e",
        f"DSS_SCHEMA_PACK_DIR={_PACKS}",
    ]
    if Path(runtime).name == "docker":
        # Linux Docker has no host alias unless it is added.
        # TODO(#163): on Linux this alias is the bridge address, not the host's
        # loopback, so a mock or Langfuse bound to 127.0.0.1 is out of reach and
        # every turn fails. Rootless Podman on Linux has the same problem. CI
        # runs on Linux: bind them to 0.0.0.0 there, or use `--network host`.
        command += ["--add-host", "host.docker.internal:host-gateway"]
    for name in pass_env:
        command += ["-e", name]
    for name, value in set_env.items():
        command += ["-e", f"{name}={value}"]
    command.append(image)
    done = subprocess.run(command, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def limits(runtime: str, container: str) -> dict[str, float]:
    """The CPU and memory the running container actually got."""

    done = subprocess.run(
        [
            runtime,
            "inspect",
            "--format",
            "{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}",
            container,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    nano_cpus, memory = done.stdout.split()
    return {"cpus": int(nano_cpus) / 1e9, "memory_gib": int(memory) / 2**30}


def logs(runtime: str, container: str) -> str:
    """The container's log, both streams: uvicorn writes most of it to
    stderr. Read before `stop`, which removes the container and its log."""

    done = subprocess.run(
        [runtime, "logs", container], check=False, capture_output=True, text=True
    )
    return done.stdout + done.stderr


def stop(runtime: str, container: str) -> None:
    subprocess.run([runtime, "stop", container], check=False, capture_output=True)
