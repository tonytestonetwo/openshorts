"""Run the API, Remotion renderer, and nginx in one Runpod container."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
RENDERER_READY_URL = "http://127.0.0.1:3100/health"
API_READY_URL = "http://127.0.0.1:8000/health/ready"
STARTUP_TIMEOUT_SECONDS = 300
SHUTDOWN_TIMEOUT_SECONDS = 15
AUTH_FILE = Path("/tmp/nginx/.htpasswd")

children: list[subprocess.Popen] = []
shutdown_requested = False


def request_shutdown(signum: int, _frame: object) -> None:
    global shutdown_requested
    shutdown_requested = True
    print(f"Received signal {signum}; stopping services.", flush=True)


def wait_until_ready(process: subprocess.Popen, url: str, name: str) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if shutdown_requested:
            raise RuntimeError("Startup interrupted by shutdown.")
        if process.poll() is not None:
            raise RuntimeError(f"{name} exited during startup with code {process.returncode}.")
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, URLError, TimeoutError):
            time.sleep(1)
    raise TimeoutError(f"{name} did not become ready within {STARTUP_TIMEOUT_SECONDS} seconds.")


def stop_children() -> None:
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
    for child in reversed(children):
        if child.poll() is None:
            try:
                child.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def configure_basic_auth() -> None:
    username = os.environ.get("OPENSHORTS_AUTH_USER", "").strip()
    password = os.environ.get("OPENSHORTS_AUTH_PASSWORD", "")
    if (
        not username
        or username.startswith("-")
        or any(char in username for char in ":\r\n")
        or not password
        or any(char in password for char in "\r\n")
    ):
        raise RuntimeError(
            "Set OPENSHORTS_AUTH_USER and OPENSHORTS_AUTH_PASSWORD to protect the public dashboard."
        )

    result = subprocess.run(
        ["htpasswd", "-cB", "-C", "12", "-i", str(AUTH_FILE), username],
        input=f"{password}\n",
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Could not create the dashboard access credentials.")
    AUTH_FILE.chmod(0o600)


def main() -> int:
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    try:
        configure_basic_auth()
    except (OSError, RuntimeError) as error:
        print(f"Service startup failed: {error}", file=sys.stderr, flush=True)
        return 1

    node = os.environ.get("NODE", "node")
    services = [
        ("renderer", [node, "dist/server.js"], ROOT / "render-service", RENDERER_READY_URL),
        (
            "API",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
                "--proxy-headers",
                "--forwarded-allow-ips",
                "127.0.0.1",
                "--timeout-graceful-shutdown",
                "15",
            ],
            ROOT,
            API_READY_URL,
        ),
        ("nginx", ["nginx", "-g", "daemon off;"], ROOT, None),
    ]

    exit_code = 1
    try:
        for name, command, cwd, ready_url in services:
            if shutdown_requested:
                break
            print(f"Starting {name}.", flush=True)
            process = subprocess.Popen(command, cwd=cwd)
            children.append(process)
            if ready_url:
                wait_until_ready(process, ready_url, name)
                print(f"{name} is ready.", flush=True)

        while not shutdown_requested:
            for process in children:
                status = process.poll()
                if status is not None:
                    exit_code = status or 1
                    print(f"A service exited with code {status}; stopping the container.", flush=True)
                    return exit_code
            time.sleep(1)
        exit_code = 0
    except (OSError, RuntimeError, TimeoutError) as error:
        print(f"Service startup failed: {error}", file=sys.stderr, flush=True)
        if shutdown_requested:
            exit_code = 0
    finally:
        stop_children()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
