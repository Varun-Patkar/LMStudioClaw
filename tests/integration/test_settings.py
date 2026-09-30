"""Integration test for settings persistence and model context prefs (SC-010).

Verifies theme persists across reloads, the default model is stored and applied, and
a per-model context preference is clamped, saved, and reapplied on load.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from lmstudioclaw.config.settings import Settings, load_settings, save_settings
from lmstudioclaw.model.context_prefs import preferred_context, set_context_pref


def test_theme_and_default_model_persist(temp_app_paths):
    settings = load_settings(temp_app_paths.settings_path)
    settings.theme = "dark"
    settings.default_model = "model-x"
    save_settings(temp_app_paths.settings_path, settings)

    reloaded = load_settings(temp_app_paths.settings_path)
    assert reloaded.theme == "dark"
    assert reloaded.default_model == "model-x"


def test_model_labels_persist(temp_app_paths):
    settings = load_settings(temp_app_paths.settings_path)
    settings.model_labels = {"model-x": "Fast - Model X"}
    save_settings(temp_app_paths.settings_path, settings)

    assert load_settings(temp_app_paths.settings_path).model_labels == {"model-x": "Fast - Model X"}


def test_model_list_uses_saved_label_without_changing_key(monkeypatch):
    from lmstudioclaw.web import routes_settings

    model = SimpleNamespace(
        key="model-x", display_name="Model X", max_context_length=8192,
        quantization="Q4", size_bytes=123, capabilities=[], is_loaded=False,
    )
    monkeypatch.setattr(routes_settings, "list_models", lambda _http: ([model], True))
    controller = SimpleNamespace(http=None, settings=SimpleNamespace(model_labels={"model-x": "Fast - Model X"}))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(controller=controller)))

    result = asyncio.run(routes_settings.get_models(request))
    assert result["models"][0]["key"] == "model-x"
    assert result["models"][0]["display_name"] == "Fast - Model X"


def test_compression_threshold_clamped():
    s = Settings.from_dict({"compression_threshold": 5.0})
    assert s.compression_threshold <= 0.99
    s2 = Settings.from_dict({"compression_threshold": 0.1})
    assert s2.compression_threshold >= 0.5


def test_per_model_context_pref_clamped_and_applied(monkeypatch, tmp_path):
    # Redirect the prefs file into a temp location so the test is isolated.
    import lmstudioclaw.model.context_prefs as cp

    prefs_path = tmp_path / "context_prefs.json"
    monkeypatch.setattr(cp, "_PREFS_PATH", prefs_path)

    model = {"key": "m1", "max_context_length": 8192}
    # Request beyond max -> clamped to max.
    applied = set_context_pref(model, 999999)
    assert applied == 8192
    # Request below min -> clamped up to 1024.
    applied2 = set_context_pref(model, 10)
    assert applied2 == cp.MIN_CONTEXT
    # Reapplied on (re)load.
    assert preferred_context(model) == cp.MIN_CONTEXT


def test_model_load_uses_single_parallel_prediction():
    from lmstudioclaw.model.lifecycle import ModelLifecycle

    requests = []

    class Client:
        def post(self, path, json, timeout):
            requests.append((path, json))
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {})

    lifecycle = ModelLifecycle(Client(), None)
    lifecycle._load_sync("qwen/qwen3.5-9b", 131072)

    assert requests[0][0] == "/api/v1/models/load"
    assert requests[0][1]["context_length"] == 131072
    assert requests[0][1]["parallel"] == 1
