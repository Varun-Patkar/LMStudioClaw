"""System-tray icon.

A pystray tray icon whose "Open" item launches the browser at the served control-panel
URL and whose "Quit" item triggers a graceful controller shutdown (FR-040/FR-041/FR-043).
Closing the browser does **not** quit the app — only the tray Quit does.

The tray runs on its own thread (pystray's detached mode); shutdown is delegated back
to the asyncio controller via a thread-safe callback.
"""

from __future__ import annotations

import webbrowser
from collections.abc import Callable

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover - import guard for headless environments
    pystray = None
    Image = None
    ImageDraw = None


def _build_icon_image():
    """Create a small in-memory tray icon image (matches the original app's look)."""
    img = Image.new("RGB", (64, 64), color=(30, 35, 42))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill=(34, 132, 230))
    draw.rectangle((18, 20, 46, 44), fill=(235, 240, 246))
    draw.rectangle((22, 24, 42, 40), fill=(34, 132, 230))
    return img


class Tray:
    """Wraps a pystray icon with Open/Quit actions."""

    def __init__(self, open_url: str, on_quit: Callable[[], None]) -> None:
        """Store the URL to open and the quit callback (invoked on the controller)."""
        self._url = open_url
        self._on_quit = on_quit
        self._icon = None

    def run(self) -> bool:
        """Run the tray icon on the CURRENT (main) thread, blocking until Quit.

        Returns ``False`` immediately if pystray/Pillow are unavailable (headless),
        so the caller can fall back to running the server in the foreground. Running
        on the main thread is the reliable pystray pattern on Windows — it installs
        the required message loop and works under ``pythonw`` (no console).
        """
        if pystray is None or Image is None:
            return False
        menu = pystray.Menu(
            pystray.MenuItem("Open Control Panel", self._open, default=True),
            pystray.MenuItem("Quit", self._quit),
        )
        self._icon = pystray.Icon(
            "lmstudioclaw", _build_icon_image(), "LM Studio Manager", menu
        )
        # ``setup`` runs once the icon is visible; we use it to auto-open the UI.
        self._icon.run(setup=self._on_ready)
        return True

    def _on_ready(self, icon) -> None:
        """Make the icon visible and open the control panel on first launch."""
        try:
            icon.visible = True
        except Exception:
            pass
        self._open()

    def stop(self) -> None:
        """Stop the tray icon, which returns control from :meth:`run` (best-effort)."""
        try:
            if self._icon is not None:
                self._icon.stop()
        except Exception:
            pass

    def _open(self, icon=None, item=None) -> None:
        """Open the control panel in the default browser."""
        try:
            webbrowser.open(self._url)
        except Exception:
            pass

    def _quit(self, icon=None, item=None) -> None:
        """Trigger graceful app shutdown, then stop the tray (ends :meth:`run`)."""
        try:
            self._on_quit()
        finally:
            self.stop()
