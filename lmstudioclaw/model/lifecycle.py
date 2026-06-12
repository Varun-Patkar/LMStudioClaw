"""Model lifecycle service: load / unload / warmup / orphan-detect.

Reuses the existing ``httpx`` native-API calls from the original tray app. This is
the **only** module that loads models. The single-loaded-model invariant (FR-002,
FR-006) is enforced by always unloading any existing instance before loading a new
one and by unloading on every session terminal state.

Methods are exposed as ``async`` wrappers (running the blocking ``httpx`` calls in a
thread executor) so the asyncio orchestrator never blocks the event loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
from openai import OpenAI

from .catalog import Connection, loaded_instances, raw_models


@dataclass
class LoadedModel:
    """A currently loaded model instance."""

    instance_id: str
    key: str
    context_length: int


class ModelLifecycle:
    """Load/unload/warmup the LM Studio model for a run."""

    def __init__(self, client: httpx.Client, conn: Connection) -> None:
        """Store the native-API client and connection for OpenAI-compatible calls."""
        self._client = client
        self._conn = conn

    # -- blocking primitives (run in executor) -----------------------------

    def _load_sync(self, model_key: str, context_length: int | None) -> dict:
        """Load a model via ``/api/v1/models/load`` (blocking)."""
        body: dict = {"model": model_key, "echo_load_config": True}
        if context_length is not None:
            body["context_length"] = context_length
        resp = self._client.post("/api/v1/models/load", json=body, timeout=600)
        resp.raise_for_status()
        return resp.json()

    def _unload_sync(self, instance_id: str) -> None:
        """Unload an instance via ``/api/v1/models/unload`` (blocking)."""
        resp = self._client.post(
            "/api/v1/models/unload", json={"instance_id": instance_id}, timeout=180
        )
        resp.raise_for_status()

    def _warmup_sync(self, model_id: str) -> str:
        """Send a tiny chat completion to initialize the KV cache (blocking)."""
        oai = OpenAI(base_url=self._conn.openai_base, api_key=self._conn.api_key, timeout=120)
        r = oai.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=32,
        )
        return r.choices[0].message.content or ""

    # -- async API ----------------------------------------------------------

    async def load(self, model_key: str, context_length: int | None = None) -> LoadedModel:
        """Unload any existing model, then load ``model_key`` (single-model invariant).

        Returns the resulting :class:`LoadedModel`. Raises on load failure so the
        caller can record a ``failed`` session with the failure point.
        """
        existing = await self.current()
        if existing:
            await self.unload(existing.instance_id)
        await asyncio.to_thread(self._load_sync, model_key, context_length)
        loaded = await self.current()
        if loaded is None:
            # The load call returned but no instance is reported — treat as failure.
            raise RuntimeError(f"Model '{model_key}' did not report a loaded instance.")
        return loaded

    async def unload(self, instance_id: str) -> None:
        """Unload a specific model instance (best-effort tolerated by caller)."""
        await asyncio.to_thread(self._unload_sync, instance_id)

    async def warmup(self, model_id: str) -> str:
        """Warm up the loaded model; returns the tiny completion text."""
        return await asyncio.to_thread(self._warmup_sync, model_id)

    async def current(self) -> LoadedModel | None:
        """Return the currently loaded model instance, if any."""
        models = await asyncio.to_thread(raw_models, self._client)
        instances = loaded_instances(models)
        if not instances:
            return None
        inst = instances[0]
        ctx = int(inst.get("config", {}).get("context_length", 0) or 0)
        return LoadedModel(instance_id=inst["id"], key=inst["key"], context_length=ctx)

    async def detect_orphan(self) -> LoadedModel | None:
        """On startup, find a model left loaded from a previous run (FR-006)."""
        return await self.current()

    async def unload_all(self) -> None:
        """Unload every loaded instance (used at startup orphan cleanup / shutdown)."""
        models = await asyncio.to_thread(raw_models, self._client)
        for inst in loaded_instances(models):
            try:
                await self.unload(inst["id"])
            except (httpx.HTTPError, RuntimeError):
                # Best-effort cleanup; keep going.
                continue
