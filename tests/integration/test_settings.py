"""Integration test for settings persistence and model context prefs (SC-010).

Verifies theme persists across reloads, the default model is stored and applied, and
a per-model context preference is clamped, saved, and reapplied on load.
"""

from __future__ import annotations

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
