from __future__ import annotations

import subprocess
from collections.abc import Iterator

import pytest

from scripts.run_local_server import start_server

SHOWDOWN_TEST_PORT = 8091


@pytest.fixture(scope="session")
def showdown_server() -> Iterator[int]:
    process: subprocess.Popen[str] = start_server(port=SHOWDOWN_TEST_PORT)
    try:
        yield SHOWDOWN_TEST_PORT
    finally:
        process.terminate()
        process.wait(timeout=10)
