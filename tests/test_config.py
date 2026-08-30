"""Tests de la configuration (§23) : rien ne doit être codé en dur."""

from __future__ import annotations

import pytest

from bldp.config import Config, ConfigError, load_config


def test_defaults_are_loaded():
    config = load_config()
    assert config.get("project.jurisdiction") == "benin"
    assert config.get("ocr.language") == "fra"
    assert config.get("embeddings.enabled") is False, "les embeddings sont optionnels"
    assert config.get("privacy.allow_external_calls") is False, "aucun appel externe par défaut"


def test_missing_key_returns_default():
    config = load_config()
    assert config.get("inexistant.cle", "fallback") == "fallback"
    assert "ocr.enabled" in config
    assert "ocr.inexistant" not in config


def test_require_raises_on_missing_key():
    config = load_config()
    with pytest.raises(ConfigError):
        config.require("section.absente")


def test_cli_overrides_win():
    config = load_config(overrides=["ocr.enabled=false", "quality.minimum_score=0.8"])
    assert config.get("ocr.enabled") is False
    assert config.get("quality.minimum_score") == pytest.approx(0.8)


def test_override_creates_nested_key():
    config = load_config(overrides=["nouveau.sous.cle=42"])
    assert config.get("nouveau.sous.cle") == 42


def test_malformed_override_is_rejected():
    with pytest.raises(ConfigError):
        load_config(overrides=["ocr.enabled"])


def test_file_config_is_merged_recursively(tmp_path):
    extra = tmp_path / "custom.yaml"
    extra.write_text("ocr:\n  language: eng\n", encoding="utf-8")
    config = load_config(path=extra)
    assert config.get("ocr.language") == "eng"
    # La fusion est récursive : les autres clés de la section survivent.
    assert config.get("ocr.dpi") == 300


def test_missing_config_file_is_reported(tmp_path):
    with pytest.raises(ConfigError):
        load_config(path=tmp_path / "absent.yaml")


def test_paths_resolve_against_project_root(tmp_path):
    config = load_config(root=tmp_path)
    assert config.path("exports") == tmp_path / "data" / "exports"
    config.ensure_directories()
    assert (tmp_path / "data" / "exports").is_dir()


def test_root_override_is_honoured(tmp_path):
    """``--set _root=…`` doit être respecté, pas écrasé silencieusement.

    L'écraser ferait écrire le pipeline dans le dépôt alors que l'appelant a
    explicitement demandé un autre dossier de travail.
    """
    config = load_config(overrides=[f"_root={tmp_path}"])
    assert config.root == tmp_path.resolve()
    assert config.path("exports") == tmp_path.resolve() / "data" / "exports"


def test_explicit_root_argument_wins_over_override(tmp_path):
    other = tmp_path / "autre"
    other.mkdir()
    config = load_config(overrides=[f"_root={tmp_path}"], root=other)
    assert config.root == other.resolve()


def test_config_is_immutable_from_outside():
    config = load_config()
    section = config.section("ocr")
    section["language"] = "xxx"
    assert config.get("ocr.language") == "fra", "as_dict/section renvoient des copies"


def test_with_overrides_does_not_mutate_original():
    config = load_config()
    derived = config.with_overrides({"ocr": {"enabled": False}})
    assert derived.get("ocr.enabled") is False
    assert config.get("ocr.enabled") is True
    assert isinstance(derived, Config)
