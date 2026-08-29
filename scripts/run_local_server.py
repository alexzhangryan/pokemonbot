"""Run the vendored Showdown build as a local server with security disabled.

Usage: python scripts/run_local_server.py [port]
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHOWDOWN_DIR = REPO_ROOT / "vendor" / "showdown"
READY_MARKER = "Test your server at"
DEFAULT_PORT = 8090


def _pump_lines(stream: object, sink: queue.Queue[str | None]) -> None:
    for line in iter(stream.readline, ""):  # type: ignore[attr-defined]
        sink.put(line)
    sink.put(None)


def start_server(port: int = DEFAULT_PORT, timeout: float = 20.0) -> subprocess.Popen[str]:
    """Start `pokemon-showdown start --no-security` and block until it's ready.

    --no-security sets noguestsecurity, nothrottle, and noipchecks, which lets
    a client log in with a bare `/trn USERNAME` instead of a signed assertion
    from the real login server.
    """
    process = subprocess.Popen(
        ["node", "pokemon-showdown", "start", "--no-security", "--skip-build", str(port)],
        cwd=SHOWDOWN_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None

    lines: queue.Queue[str | None] = queue.Queue()
    reader = threading.Thread(target=_pump_lines, args=(process.stdout, lines), daemon=True)
    reader.start()

    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.terminate()
            raise TimeoutError(f"Showdown server did not become ready within {timeout}s")
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            continue
        if line is None:
            process.terminate()
            raise RuntimeError("Showdown server process exited before becoming ready")
        if READY_MARKER in line:
            return process


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    process = start_server(port)
    print(f"Showdown server ready on port {port} (pid {process.pid}); Ctrl+C to stop")
    try:
        process.wait()
    except KeyboardInterrupt:
        process.terminate()


if __name__ == "__main__":
    main()
