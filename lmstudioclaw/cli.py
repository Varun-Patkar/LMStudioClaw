"""Thin entry point for the LM Studio Manager controller.

Starts the resident controller: finds a free web port (with fallback if the preferred
one is taken — FR-055), serves the FastAPI app via uvicorn on a **background thread**,
and runs the system-tray icon on the **main thread** (the reliable pystray pattern on
Windows; works under ``pythonw`` with no console). The tray auto-opens the browser
control panel on launch; its "Quit" item stops the server and the tray, ending the
process (FR-043). Closing the browser does not quit the app.

Any startup failure is written to ``%APPDATA%/LMStudioClaw/startup.log`` so silent
``pythonw`` crashes (no console to print to) are diagnosable.
"""

from __future__ import annotations

import socket
import threading
import traceback
from datetime import datetime

import uvicorn

from .app import Controller
from .tray.icon import Tray
from .web.api import create_app


def _find_free_port(preferred: int, attempts: int = 20) -> int:
    """Return ``preferred`` if free, else the next available port (FR-055)."""
    for offset in range(attempts):
        candidate = preferred + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    # Fall back to an OS-assigned ephemeral port.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _log_startup_error(exc: BaseException) -> None:
    """Append a startup failure to %APPDATA%/LMStudioClaw/startup.log (best-effort)."""
    try:
        from .config.paths import resolve_paths

        log_path = resolve_paths().app_data / "startup.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"\n=== {datetime.now().isoformat()} ===\n")
            handle.write("".join(traceback.format_exception(exc)))
    except Exception:
        # Nothing more we can do if even logging fails.
        pass


def _run() -> None:
    """Boot the controller: server on a background thread, tray on the main thread."""
    controller = Controller()
    port = _find_free_port(controller.settings.web_port)
    controller.served_url = f"http://localhost:{port}"

    app = create_app(controller)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            # Disable uvicorn's logging config: under ``pythonw`` there is no console
            # and its default formatter calls ``sys.stdout.isatty()`` on ``None``,
            # which would crash before the server starts. ``access_log=False`` keeps
            # the windowless process quiet.
            log_config=None,
            access_log=False,
        )
    )

    # Serve in the background so the tray can own the main thread.
    server_thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    server_thread.start()

    # The tray "Quit" item asks uvicorn to exit; lifespan shutdown then unloads the
    # model and stops the queue/scheduler.
    def _quit() -> None:
        server.should_exit = True

    tray = Tray(controller.served_url, _quit)
    ran = tray.run()  # blocks on the main thread until Quit (or returns False headless)

    if not ran:
        # No tray available (headless): open the browser and run in the foreground.
        try:
            import webbrowser

            webbrowser.open(controller.served_url)
        except Exception:
            pass
        server_thread.join()
        return

    # Tray Quit selected: make sure the server stops and the process can exit.
    server.should_exit = True
    server_thread.join(timeout=10)


def cli() -> None:
    """Entry point for the ``lmstudio`` console script."""
    try:
        _run()
    except Exception as exc:  # log silent pythonw crashes, then re-raise for console runs
        _log_startup_error(exc)
        raise


if __name__ == "__main__":
    cli()
