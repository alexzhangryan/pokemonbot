"""Open the bot's-eye view: what the agent sees, what it decided, and the
controls to start what it is watching.

Usage:
    python scripts/viewer.py                # traces/, opens an app window
    python scripts/viewer.py runs           # watch a different directory
    python scripts/viewer.py --no-open      # just serve it
    python scripts/viewer.py --no-server    # do not start the simulator
    python scripts/viewer.py --port 8123

This is the only command a session needs. The viewer starts the Showdown
simulator on its own, and its control panel launches self-play runs and puts a
bot up to be challenged, writing every trace into the directory it is already
watching. That last part is the point: when the runs were started by hand it was
easy to have the viewer watching `traces/` while the battle wrote to
`runs/human`, and nothing ever appeared.

The window updates live while a battle is running and replays finished ones from
the same files, because the server tails the trace rather than talking to the
agent (see champions/viewer/server.py). Nothing here can influence play.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from champions.viewer.server import create_app

# Browsers that support --app, which opens a chromeless window that behaves like
# a separate desktop application rather than another tab lost in a browser.
# Ordered by what a Windows development machine is most likely to have.
APP_MODE_BROWSERS = (
    "msedge",
    "chrome",
    "chromium",
    "brave",
    "vivaldi",
)


def open_window(url: str, app_mode: bool) -> None:
    """Open the viewer, preferring a standalone app window over a browser tab."""
    if app_mode:
        for browser in APP_MODE_BROWSERS:
            path = shutil.which(browser)
            if path:
                # Detached: the viewer's lifetime is the server's, not this
                # window's, so closing the window must not kill the server and
                # exiting the server must not wait on the window.
                subprocess.Popen(  # noqa: S603
                    [path, f"--app={url}", "--window-size=1500,980"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
    webbrowser.open(url)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_dir", nargs="?", default="traces")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument(
        "--showdown-port",
        type=int,
        default=8090,
        help="port for the Showdown simulator the viewer starts and drives",
    )
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="do not start the Showdown simulator automatically",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-open", action="store_true", help="do not open a window")
    parser.add_argument(
        "--tab",
        action="store_true",
        help="open in a normal browser tab instead of a standalone app window",
    )
    args = parser.parse_args()

    root = Path(args.trace_dir)
    if not root.exists():
        # Not fatal: the directory appears the moment the first battle starts,
        # and starting the viewer before the run is the normal way to use it.
        print(f"note: {root}/ does not exist yet; it will appear when a battle writes to it")
    root.mkdir(parents=True, exist_ok=True)

    url = f"http://{args.host}:{args.port}/"
    print(f"viewer  {url}")
    print(f"traces  {root.resolve()}")
    if args.no_server:
        print("sim     not started (--no-server)")
    else:
        print(f"sim     starting on port {args.showdown_port}")
    print()

    if not args.no_open:
        # After a beat, so the window does not race the server to the port.
        threading.Timer(0.8, open_window, args=(url, not args.tab)).start()

    try:
        uvicorn.run(
            create_app(
                root,
                showdown_port=args.showdown_port,
                autostart_showdown=not args.no_server,
            ),
            host=args.host,
            port=args.port,
            log_level="warning",
        )
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
