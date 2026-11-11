"""Sandboxed command execution, exposed to FROST as the `shell` tool.

The honest framing, because this is the part people oversell:

There are two different jobs here. **Confinement** stops a process from reaching
things it shouldn't. **Gating** stops a command from being issued in the first
place. Only a container or a VM gives you real confinement on the host. This
module provides confinement when Docker is present, and meaningful hardening
when it isn't — with the permission gate in `permissions.py` doing the blocking
work in both cases.

Backends, chosen automatically:

  docker      Real isolation. Only the workspace is mounted, networking is off
              by default, the host filesystem and host credentials do not exist
              inside the container. `rm -rf /` deletes a container that is
              discarded a second later.

  subprocess  Fallback when Docker is not installed. Runs on the host, so it is
              NOT isolation. What it does buy: the environment is scrubbed of
              every credential-shaped variable, HOME/USERPROFILE are redirected
              into the workspace so tools that auto-discover `~/.aws` find
              nothing, the working directory is pinned, and runtime and output
              are capped. Treat it as defence in depth behind the gate, not as
              a wall.

`frost.status` reports which one is live, so this is never ambiguous at runtime.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from .config import (
    SANDBOX_IMAGE,
    SANDBOX_NETWORK,
    SANDBOX_TIMEOUT,
    WORKSPACE,
)

MAX_OUTPUT_CHARS = 20_000

# Environment variables never passed to a child process. Substring match, so
# ANTHROPIC_API_KEY, GH_TOKEN, MY_DB_PASSWORD and friends are all covered.
_SECRET_MARKERS = (
    "KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH",
    "SESSION", "COOKIE", "PRIVATE",
)
# Kept because programs genuinely break without them. Windows is fussier here
# than it looks: drop SYSTEMDRIVE or PROGRAMDATA and `cmd` stops expanding
# %SystemDrive%, then helpfully creates directories with the literal percent
# signs in the name. None of these leak anything — they're machine layout, not
# user data.
_ENV_KEEP = (
    "PATH", "PATHEXT", "COMSPEC", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR",
    "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
    "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "TEMP", "TMP",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER",
    "OS", "LANG", "TZ",
)

# Redirected rather than dropped. These normally point into the real user
# profile, which is where credentials and browser data live — but deleting them
# outright breaks well-behaved programs. Pointing them inside the workspace
# keeps those programs working and still shows them nothing of yours.
_ENV_REDIRECT = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME")

_backend_cache: str | None = None
_docker_path: str = "docker"


def _docker_exe() -> str | None:
    """Find the docker CLI, tolerating a stale PATH.

    A shell opened before Docker Desktop was installed keeps the old PATH for
    its whole life, so `which` says no even though Docker is running. Checking
    the known install locations avoids telling the user their working Docker
    isn't there.
    """
    found = shutil.which("docker")
    if found:
        return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/DockerDesktop/resources/bin/docker.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Docker/Docker/resources/bin/docker.exe",
        Path("/usr/bin/docker"),
        Path("/usr/local/bin/docker"),
    ]
    for path in candidates:
        try:
            if path.is_file():
                return str(path)
        except OSError:
            continue
    return None


def detect_backend() -> str:
    """Return 'docker' if a working daemon is reachable, else 'subprocess'."""
    global _backend_cache, _docker_path
    if _backend_cache is not None:
        return _backend_cache
    _backend_cache = "subprocess"
    exe = _docker_exe()
    if exe:
        try:
            _docker_path = exe
            proc = __import__("subprocess").run(
                [exe, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True, timeout=20, text=True, env=_docker_env(),
            )
            if proc.returncode == 0:
                _docker_path = exe
                _backend_cache = "docker"
        except Exception:
            pass
    return _backend_cache


def scrubbed_env() -> dict[str, str]:
    """A minimal environment with every credential-shaped variable removed."""
    env = {
        k: v for k, v in os.environ.items()
        if k.upper() in _ENV_KEEP and not any(m in k.upper() for m in _SECRET_MARKERS)
    }
    # Point home-directory discovery at the workspace. A tool that looks for
    # ~/.aws/credentials or ~/.ssh finds an empty directory instead of yours.
    home = WORKSPACE / ".home"
    home.mkdir(parents=True, exist_ok=True)
    for name in _ENV_REDIRECT:
        env[name] = str(home)
    return env


def _docker_env() -> dict[str, str]:
    """Environment for the docker CLI itself (not the sandboxed command).

    The CLI shells out to `docker-credential-desktop` to read the registry
    credential store. If Docker's bin directory isn't on PATH — which happens
    in any shell started before Docker was installed — that lookup fails with
    a bare "error getting credentials", and every run dies before the container
    even starts. So put Docker's own directory on the PATH we hand it.
    """
    env = dict(os.environ)
    exe = _docker_exe()
    if exe:
        bindir = str(Path(exe).parent)
        if bindir not in env.get("PATH", ""):
            env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    return env


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(text)} chars total]"


async def _run_docker(command: str, network: bool, timeout: int) -> tuple[int, str, str]:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    args = [
        _docker_path, "run", "--rm", "-i",
        "--network", "bridge" if network else "none",
        "--memory", "2g", "--cpus", "2",
        "--pids-limit", "256",
        "--security-opt", "no-new-privileges",
        "-v", f"{WORKSPACE}:/work",
        "-w", "/work",
        SANDBOX_IMAGE,
        "sh", "-lc", command,
    ]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=_docker_env(),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "", f"timed out after {timeout}s"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def _run_subprocess(command: str, timeout: int) -> tuple[int, str, str]:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(WORKSPACE),
        env=scrubbed_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "", f"timed out after {timeout}s"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def run_command(
    command: str, *, network: bool = SANDBOX_NETWORK, timeout: int = SANDBOX_TIMEOUT
) -> dict[str, Any]:
    backend = detect_backend()
    if backend == "docker":
        code, out, err = await _run_docker(command, network, timeout)
    else:
        code, out, err = await _run_subprocess(command, timeout)
    return {"backend": backend, "exit_code": code, "stdout": out, "stderr": err}


@tool(
    "run",
    "Run a shell command inside FROST's sandbox. This is the preferred way to "
    "execute anything: the working directory is the workspace, credential "
    "environment variables are stripped, and when Docker is available it runs "
    "in a throwaway container with no access to the host filesystem. Use this "
    "instead of the built-in Bash tool unless you specifically need the host.",
    {"command": str, "timeout_seconds": int},
)
async def run(args: dict) -> dict:
    command = (args.get("command") or "").strip()
    if not command:
        return {
            "content": [{"type": "text", "text": "Refused: `command` is required."}],
            "is_error": True,
        }
    timeout = int(args.get("timeout_seconds") or SANDBOX_TIMEOUT)
    result = await run_command(command, timeout=min(timeout, 300))

    body = (
        f"[{result['backend']} sandbox] exit={result['exit_code']}\n"
        f"--- stdout ---\n{_truncate(result['stdout']) or '(empty)'}\n"
        f"--- stderr ---\n{_truncate(result['stderr']) or '(empty)'}"
    )
    return {
        "content": [{"type": "text", "text": body}],
        "is_error": result["exit_code"] != 0,
    }


def build_sandbox_server():
    return create_sdk_mcp_server(name="shell", version="1.0.0", tools=[run])


SANDBOX_TOOL = "mcp__shell__run"
