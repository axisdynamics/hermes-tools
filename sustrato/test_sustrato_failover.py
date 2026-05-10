#!/usr/bin/env python3
"""Regression tests for Sustrato quota/rate failover classification and sync."""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent


def _load_cli(home: Path):
    os.environ["HERMES_HOME"] = str(home)
    loader = importlib.machinery.SourceFileLoader("sustrato_cli_under_test", str(ROOT / "sustrato"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _load_plugin(home: Path):
    os.environ["HERMES_HOME"] = str(home)
    spec = importlib.util.spec_from_file_location("sustrato_plugin_under_test", ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_classification_matrix():
    with tempfile.TemporaryDirectory() as td:
        cli = _load_cli(Path(td))
        cases = [
            (429, '{"error":{"type":"insufficient_quota"}}', True, True, "quota_or_rate_limit"),
            (429, '{"error":{"type":"rate_limit_error","message":"tokens per minute"}}', True, True, "quota_or_rate_limit"),
            (402, "insufficient credits / balance too low", True, True, "quota_or_rate_limit"),
            (529, '{"error":{"type":"overloaded_error"}}', True, True, "quota_or_rate_limit"),
            (500, "plain server exploded", False, True, "server_error"),
            (401, "invalid api key", True, False, "auth"),
            (200, "ok", False, False, "ok"),
        ]
        for status, body, terminal, retryable, reason in cases:
            got = cli.classify_provider_failure(status, body)
            assert got["terminal"] is terminal, (status, body, got)
            assert got["retryable"] is retryable, (status, body, got)
            assert got["reason"] == reason, (status, body, got)


def test_sync_writes_active_and_fallbacks_without_secrets():
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / "config.yaml").write_text(yaml.safe_dump({
            "model": {"default": "old", "provider": "openai", "base_url": "https://old.example/v1"},
            "fallback_providers": [],
            "unrelated": "preserve",
        }))
        cfg = {
            "chain": [
                {"provider": "openai", "model": "gpt-4o-mini"},
                {"provider": "anthropic", "model": "claude-sonnet-4"},
                {"provider": "openrouter", "model": "anthropic/claude-sonnet-4", "url": "https://openrouter.ai/api/v1"},
            ],
            "sync_hermes_config": True,
        }
        cli = _load_cli(home)
        assert cli.sync_hermes_config(cfg, {"active_index": 0}, quiet=True) is True
        out = yaml.safe_load((home / "config.yaml").read_text())
        assert out["model"]["provider"] == "openai"
        assert out["model"]["default"] == "gpt-4o-mini"
        assert out["model"].get("base_url", "") == ""
        assert out["fallback_providers"] == [
            {"provider": "anthropic", "model": "claude-sonnet-4"},
            {"provider": "openrouter", "model": "anthropic/claude-sonnet-4", "base_url": "https://openrouter.ai/api/v1"},
        ]
        assert "api_key" not in json.dumps(out).lower()
        assert out["unrelated"] == "preserve"


def test_plugin_failover_syncs_hermes_config():
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        (home / "config.yaml").write_text(yaml.safe_dump({
            "model": {"default": "gpt-4o-mini", "provider": "openai"},
            "fallback_providers": [],
        }))
        (home / "sustrato.yaml").write_text(yaml.safe_dump({
            "chain": [
                {"provider": "openai", "model": "gpt-4o-mini"},
                {"provider": "anthropic", "model": "claude-sonnet-4"},
            ],
            "auto_failover": True,
            "fail_threshold": 1,
            "sync_hermes_config": True,
        }))
        plugin = _load_plugin(home)
        msg = plugin._cmd_sustrato(["failover"])
        assert "Failover" in msg
        out = yaml.safe_load((home / "config.yaml").read_text())
        assert out["model"] == {"default": "claude-sonnet-4", "provider": "anthropic"}
        assert out["fallback_providers"] == []


if __name__ == "__main__":
    test_classification_matrix()
    test_sync_writes_active_and_fallbacks_without_secrets()
    test_plugin_failover_syncs_hermes_config()
    print("sustrato failover regression tests passed")
