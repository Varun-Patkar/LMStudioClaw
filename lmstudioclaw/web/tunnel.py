"""Public-URL tunnel + QR helper for the "See this on your phone" feature.

Exposes the locally-served web UI through a **VS Code dev tunnel**
(``devtunnel host -p <port> --allow-anonymous``), parses the generated
``https://*.devtunnels.ms`` URL it prints, and renders a scannable QR code for it
(open with a phone camera / Google Lens).

Dev tunnels are the same mechanism VS Code's "port forwarding" uses, so most users
already have the ``devtunnel`` CLI. The tunnel is opt-in (started from Settings) and
best-effort: a missing CLI or a not-logged-in state returns a clear, actionable error
rather than raising. Only one tunnel runs at a time; stopping it terminates the child
process.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading

# Matches the public dev-tunnel URL (``https://<id>-<port>.<region>.devtunnels.ms``).
_URL_RE = re.compile(r"https://[-a-z0-9]+\.[-a-z0-9.]*devtunnels\.ms\S*", re.IGNORECASE)

# Output fragments that indicate the user must authenticate first.
_LOGIN_HINTS = ("login", "authenticate", "not logged in", "sign in", "unauthorized")

# Shown to the user when the CLI is missing.
_INSTALL_HINT = (
    "The dev tunnels CLI ('devtunnel') is not installed. Install it with "
    "`winget install Microsoft.devtunnel` (or see "
    "https://aka.ms/devtunnels/download), then run `devtunnel user login` once."
)


class TunnelError(RuntimeError):
    """Raised when a tunnel cannot be started (missing CLI, not logged in, timeout)."""


class TunnelManager:
    """Owns at most one ``devtunnel host`` child process."""

    def __init__(self) -> None:
        """Initialize with no active tunnel."""
        self._proc: subprocess.Popen | None = None
        self._url: str | None = None
        self._port: int | None = None
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        """Return True while the tunnel child process is alive."""
        return self._proc is not None and self._proc.poll() is None

    def status(self) -> dict:
        """Return ``{running, url, port}`` describing the current tunnel."""
        running = self.is_running()
        return {
            "running": running,
            "url": self._url if running else None,
            "port": self._port if running else None,
        }

    def start(self, port: int, timeout: float = 30.0) -> str:
        """Start (or reuse) a dev tunnel to ``localhost:port`` and return its URL.

        Raises :class:`TunnelError` if ``devtunnel`` is unavailable, the user is not
        logged in, or no URL is produced within ``timeout`` seconds.
        """
        with self._lock:
            if self.is_running() and self._url:
                return self._url

            binary = shutil.which("devtunnel") or shutil.which("devtunnel.exe")
            if binary is None:
                raise TunnelError(_INSTALL_HINT)

            # ``--allow-anonymous`` makes the URL openable without a sign-in so a phone
            # can reach it; the "Connect via browser" URL is printed to stdout.
            proc = subprocess.Popen(  # noqa: S603 - launching a trusted, user-installed CLI
                [binary, "host", "-p", str(port), "--allow-anonymous"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._proc = proc
            self._port = port
            self._url = None

            found = threading.Event()
            needs_login = threading.Event()

            def _reader() -> None:
                """Scan devtunnel output for the public URL (or a login prompt)."""
                assert proc.stdout is not None
                for line in proc.stdout:
                    if self._url is None:
                        match = _URL_RE.search(line)
                        if match:
                            self._url = match.group(0).rstrip(".,)")
                            found.set()
                        elif any(h in line.lower() for h in _LOGIN_HINTS):
                            needs_login.set()
                            found.set()
                # Process ended: if it never produced a URL, unblock the waiter.
                found.set()

            threading.Thread(target=_reader, name="devtunnel-reader", daemon=True).start()

            got = found.wait(timeout)
            if needs_login.is_set():
                self._terminate()
                raise TunnelError(
                    "You need to sign in to dev tunnels first. Run "
                    "`devtunnel user login` in a terminal, then try again."
                )
            if not got or not self._url:
                self._terminate()
                raise TunnelError(
                    "Timed out waiting for the tunnel URL. Make sure you've run "
                    "`devtunnel user login` and have internet access, then try again."
                )
            return self._url

    def stop(self) -> None:
        """Stop the tunnel (terminate the child process), if running."""
        with self._lock:
            self._terminate()

    def _terminate(self) -> None:
        """Terminate the child process and clear state (no locking)."""
        proc, self._proc, self._url, self._port = self._proc, None, None, None
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:  # pragma: no cover - best-effort teardown
            pass


def qr_svg(data: str) -> str:
    """Render ``data`` as a self-contained SVG QR code string.

    Uses the ``qrcode`` library's SVG path factory so no image (PIL) backend is
    required. Raises :class:`TunnelError` with install guidance if ``qrcode`` is
    missing so the caller can surface a clean message.
    """
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise TunnelError(
            "The 'qrcode' package is required for the QR code. Install it with "
            "`pip install qrcode`."
        ) from exc

    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(data, image_factory=factory, box_size=10, border=2)
    return img.to_string(encoding="unicode")
