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
    """Render the LMStudioClaw brand mark (gradient square + white spark/ring).

    Matches the web favicon and sidebar logo so the tray, browser tab, and in-app
    brand are all the same. Drawn at 4x and downscaled for smooth edges.
    """
    import math

    scale = 4
    size = 64 * scale

    # Diagonal blue → indigo gradient (the --accent / --accent-2 brand colours).
    grad = Image.new("RGB", (size, size))
    px = grad.load()
    a, b = (59, 130, 246), (99, 102, 241)
    span = (size - 1) * 2
    for y in range(size):
        for x in range(size):
            t = (x + y) / span
            px[x, y] = (
                round(a[0] + (b[0] - a[0]) * t),
                round(a[1] + (b[1] - a[1]) * t),
                round(a[2] + (b[2] - a[2]) * t),
            )

    # Rounded-square alpha mask.
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=int(size * 0.22), fill=255
    )
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    # White spark: 8 rays + a centre ring.
    d = ImageDraw.Draw(img)
    cx = cy = size / 2
    inner, outer = size * 0.20, size * 0.36
    w = max(2, int(size * 0.05))
    for k in range(8):
        ang = math.radians(k * 45)
        x1, y1 = cx + inner * math.cos(ang), cy + inner * math.sin(ang)
        x2, y2 = cx + outer * math.cos(ang), cy + outer * math.sin(ang)
        d.line((x1, y1, x2, y2), fill=(255, 255, 255, 255), width=w)
    r = size * 0.135
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(255, 255, 255, 255), width=w)

    return img.resize((64, 64), Image.LANCZOS)


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
            pystray.MenuItem("Open LMStudioClaw", self._open, default=True),
            pystray.MenuItem("Quit", self._quit),
        )
        self._icon = pystray.Icon(
            "lmstudioclaw", _build_icon_image(), "LMStudioClaw", menu
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
