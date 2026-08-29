"""Python client for js/sim_server.js: the rollout and differential oracle.

The Showdown simulator is the oracle rather than a custom engine, revisited at
M8 by profiling whether marginal win rate comes from search depth or evaluation
quality (DECISIONS.md D6). This is the interface that decision goes through.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import TracebackType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SIM_SERVER_JS = REPO_ROOT / "js" / "sim_server.js"


class SimServerError(RuntimeError):
    pass


class SimServer:
    """One long-lived Node subprocess speaking JSON-RPC over stdio."""

    def __init__(self, script: Path = SIM_SERVER_JS) -> None:
        self._script = script
        self._process: subprocess.Popen[str] | None = None
        self._next_id = 0

    def __enter__(self) -> SimServer:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    def start(self) -> None:
        self._process = subprocess.Popen(
            ["node", str(self._script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=REPO_ROOT,
        )

    def stop(self) -> None:
        if self._process is None:
            return
        if self._process.stdin:
            self._process.stdin.close()
        self._process.terminate()
        self._process.wait(timeout=10)
        self._process = None

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise SimServerError("SimServer is not running; call start() first")

        self._next_id += 1
        request = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()

        line = process.stdout.readline()
        if not line:
            stderr = process.stderr.read() if process.stderr else ""
            raise SimServerError(f"sim server closed the connection. stderr:\n{stderr}")

        response = json.loads(line)
        if "error" in response:
            raise SimServerError(f"{method} failed: {response['error']['message']}")
        result: dict[str, Any] = response["result"]
        return result

    # -- convenience wrappers -------------------------------------------

    def ping(self) -> bool:
        return bool(self.call("ping").get("pong"))

    def create(
        self,
        format_id: str,
        p1_team: str,
        p2_team: str,
        seed: list[int] | None = None,
        p1_name: str = "p1",
        p2_name: str = "p2",
    ) -> dict[str, Any]:
        return self.call(
            "create",
            formatId=format_id,
            seed=seed,
            p1={"name": p1_name, "team": p1_team},
            p2={"name": p2_name, "team": p2_team},
        )

    def step(self, handle: int, p1: str | None = None, p2: str | None = None) -> dict[str, Any]:
        return self.call("step", handle=handle, choices={"p1": p1, "p2": p2})

    def serialize(self, handle: int) -> dict[str, Any]:
        state: dict[str, Any] = self.call("serialize", handle=handle)["state"]
        return state

    def deserialize(self, state: dict[str, Any]) -> dict[str, Any]:
        return self.call("deserialize", state=state)

    def clone(self, handle: int) -> dict[str, Any]:
        return self.call("clone", handle=handle)

    def request(self, handle: int) -> dict[str, Any]:
        return self.call("request", handle=handle)

    def destroy(self, handle: int) -> None:
        self.call("destroy", handle=handle)
