"""LM Studio model catalog — single-call discovery, no polling.

Reuses the existing ``httpx`` native-API logic from the original tray app. Per
Constitution V / FR-045, models are fetched on demand (startup or explicit user
action) — never on a background timer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "default.yaml"


@dataclass(frozen=True)
class Connection:
    """Resolved LM Studio connection settings."""

    base_url: str   # native API base, e.g. http://localhost:1234 (no /v1)
    api_key: str

    @property
    def openai_base(self) -> str:
        """OpenAI-compatible base URL (``/v1``) for chat completions."""
        return self.base_url.rstrip("/") + "/v1"


def load_connection(base_url: str | None = None, api_key: str | None = None) -> Connection:
    """Resolve connection from explicit args, else ``config/default.yaml``.

    A ``/v1`` suffix is stripped from the base URL for the native API (matching the
    original app's behaviour).
    """
    resolved_url = "http://localhost:1234"
    resolved_key = "lm-studio"
    if _CONFIG_PATH.exists():
        try:
            cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            lms = cfg.get("lmstudio", {})
            resolved_url = str(lms.get("base_url", resolved_url)).replace("/v1", "").rstrip("/")
            resolved_key = lms.get("api_key", resolved_key)
        except (OSError, yaml.YAMLError):
            pass
    return Connection(
        base_url=(base_url or resolved_url).replace("/v1", "").rstrip("/"),
        api_key=api_key or resolved_key,
    )


def make_client(conn: Connection) -> httpx.Client:
    """Create an HTTP client for the LM Studio native API."""
    return httpx.Client(
        base_url=conn.base_url,
        timeout=600,
        headers={"Authorization": f"Bearer {conn.api_key}"},
    )


def probe_connection(client: httpx.Client) -> dict:
    """Classify the LM Studio connection state in a single call (no polling).

    Returns a dict the onboarding flow uses to decide whether to prompt the user:

    * ``reachable`` — the LM Studio server answered at all (it is running).
    * ``authorized`` — the request succeeded (the current key, if any, is accepted).
    * ``auth_required`` — the server is reachable but rejected us with 401/403, i.e.
      the instance is API-key protected and our key is missing or wrong.

    All three are ``False`` when LM Studio is not running / unreachable, so the caller
    can show "start LM Studio" guidance instead of a key prompt.
    """
    try:
        resp = client.get("/api/v1/models", timeout=8)
    except httpx.HTTPError:
        return {"reachable": False, "authorized": False, "auth_required": False}
    if resp.status_code in (401, 403):
        return {"reachable": True, "authorized": False, "auth_required": True}
    return {
        "reachable": True,
        "authorized": resp.is_success,
        "auth_required": False,
    }


def test_connection(base_url: str | None, api_key: str | None) -> dict:
    """Probe a *candidate* base URL + key without touching the live client.

    Used by the onboarding wizard's "Test connection" button so the user can verify a
    key before saving it. The temporary client is always closed.
    """
    conn = load_connection(base_url=base_url, api_key=api_key)
    client = make_client(conn)
    try:
        return probe_connection(client)
    finally:
        client.close()


@dataclass
class ModelInfo:
    """Discovered model metadata used by the UI and orchestrator."""

    key: str
    display_name: str
    max_context_length: int
    quantization: str
    size_bytes: int
    capabilities: dict
    loaded_instances: list[dict]

    @property
    def is_loaded(self) -> bool:
        """Whether this model currently has a loaded instance."""
        return bool(self.loaded_instances)


def _is_llm(model: dict) -> bool:
    """Return True for non-embedding models (embeddings are filtered everywhere)."""
    return model.get("type", "llm") != "embedding"


def list_models(client: httpx.Client) -> tuple[list[ModelInfo], bool]:
    """Fetch LM Studio models in one call.

    Returns ``(models, connected)``. ``connected`` is False on auth/connection
    failure so the caller can surface a clear message instead of crashing.
    """
    try:
        resp = client.get("/api/v1/models", timeout=8)
        if resp.status_code == 401:
            return [], False
        resp.raise_for_status()
        data = resp.json()
        raw = data.get("models", data.get("data", []))
    except (httpx.HTTPError, ValueError):
        return [], False

    out: list[ModelInfo] = []
    for m in raw:
        if not _is_llm(m):
            continue
        out.append(
            ModelInfo(
                key=m.get("key", ""),
                display_name=m.get("display_name", m.get("key", "")),
                max_context_length=int(m.get("max_context_length", 0) or 0),
                quantization=m.get("quantization", {}).get("name", "?"),
                size_bytes=int(m.get("size_bytes", 0) or 0),
                capabilities=m.get("capabilities", {}),
                loaded_instances=m.get("loaded_instances", []),
            )
        )
    return out, True


def raw_models(client: httpx.Client) -> list[dict]:
    """Fetch raw (non-embedding) model dicts — used where full shape is needed."""
    try:
        resp = client.get("/api/v1/models", timeout=8)
        resp.raise_for_status()
        data = resp.json()
        return [m for m in data.get("models", data.get("data", [])) if _is_llm(m)]
    except (httpx.HTTPError, ValueError):
        return []


def loaded_instances(models: list[dict]) -> list[dict]:
    """Flatten loaded model instances from a raw model list."""
    out: list[dict] = []
    for m in models:
        for inst in m.get("loaded_instances", []):
            out.append(
                {
                    "id": inst.get("id", ""),
                    "key": m.get("key", ""),
                    "display_name": m.get("display_name", ""),
                    "config": inst.get("config", {}),
                }
            )
    return out
