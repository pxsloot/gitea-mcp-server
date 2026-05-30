import json
import select
import subprocess
import sys
import time
from typing import Any, cast


class TransportError(Exception):
    pass


_STARTUP_TIMEOUT = 30


class Transport:
    def __init__(self, cmd: list[str], env: dict[str, str] | None = None) -> None:
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as err:
            msg = f"Server command not found: {err}"
            raise TransportError(msg) from err

    def wait_ready(self, timeout: float = _STARTUP_TIMEOUT) -> None:
        if self._process.stdout is None or self._process.stderr is None:
            return
        start = time.time()
        while time.time() - start < timeout:
            r, _, _ = select.select([self._process.stderr], [], [], 0.5)
            if r:
                line = self._process.stderr.readline()
                if not line:
                    continue
                text = line.decode("utf-8", errors="replace").strip()
                if "initialized successfully" in text.lower():
                    return
            if self._process.poll() is not None:
                self._drain_stderr()
                msg = "Server process exited during startup"
                raise TransportError(msg)
        msg = f"Server did not become ready within {timeout}s"
        raise TransportError(msg)

    def send(self, msg: dict[str, Any]) -> None:
        data = (json.dumps(msg) + "\n").encode("utf-8")
        stdin = self._process.stdin
        if stdin is None:
            err_msg = "stdin is closed"
            raise TransportError(err_msg)
        stdin.write(data)
        stdin.flush()

    def recv(self) -> dict[str, Any]:
        assert self._process.stdout is not None
        line = self._process.stdout.readline()
        if not line:
            self._drain_stderr()
            msg = "Server closed connection"
            raise TransportError(msg)
        return cast("dict[str, Any]", json.loads(line.decode("utf-8").strip()))

    def _drain_stderr(self) -> None:
        stderr_output = self._process.stderr.read() if self._process.stderr else b""
        if stderr_output:
            print("Server stderr:", stderr_output.decode("utf-8", errors="replace"), file=sys.stderr)

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
