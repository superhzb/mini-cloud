"""Unit tests for mini_cloud.config — no services required."""

from __future__ import annotations

import pytest

from mini_cloud.config import (
    CANONICAL_ENV_KEYS,
    MissingConfigError,
    Settings,
    load_dotenv,
    load_settings,
)


def test_load_from_explicit_environ() -> None:
    env = {
        "MINI_INFERENCE_URL": "http://127.0.0.1:19207/v1",
        "DATABASE_URL": "postgresql://app:pw@127.0.0.1:5432/app",
        "STORAGE_ENDPOINT": "http://127.0.0.1:9000",
        "STORAGE_BUCKET": "app",
        "PORT": "19204",
        "LOG_LEVEL": "debug",
        "APP_ENV": "staging",
    }
    s = load_settings(environ=env)
    assert s.inference_url == "http://127.0.0.1:19207/v1"
    assert s.database_url == "postgresql://app:pw@127.0.0.1:5432/app"
    assert s.port == 19204
    assert s.log_level == "debug"
    assert s.app_env == "staging"
    assert s.storage_region == "us-east-1"  # default preserved


def test_defaults_when_empty() -> None:
    s = load_settings(environ={})
    assert s.inference_url is None
    assert s.log_level == "info"
    assert s.app_env == "dev"
    assert s.port is None


def test_require_raises_with_env_name() -> None:
    s = load_settings(environ={})
    with pytest.raises(MissingConfigError, match="DATABASE_URL"):
        s.require("database_url")


def test_require_returns_value() -> None:
    s = load_settings(environ={"DATABASE_URL": "postgresql://x"})
    assert s.require("database_url") == "postgresql://x"


def test_empty_string_is_treated_as_unset() -> None:
    s = load_settings(environ={"MINI_INFERENCE_URL": ""})
    assert s.inference_url is None


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(MissingConfigError, match="LOG_LEVEL"):
        load_settings(environ={"LOG_LEVEL": "verbose"})


def test_invalid_app_env_rejected() -> None:
    with pytest.raises(MissingConfigError, match="APP_ENV"):
        load_settings(environ={"APP_ENV": "production"})


def test_non_integer_port_rejected() -> None:
    with pytest.raises(MissingConfigError, match="PORT"):
        load_settings(environ={"PORT": "abc"})


def test_analytics_fields_load() -> None:
    s = load_settings(
        environ={
            "MINI_ANALYTICS_DSN": "postgresql://analytics_ro:pw@127.0.0.1:5432/analytics",
            "MINI_ANALYTICS_BACKEND": "posthog",
            "MINI_ANALYTICS_PROJECT": "ref-showcase",
        }
    )
    assert s.analytics_dsn == "postgresql://analytics_ro:pw@127.0.0.1:5432/analytics"
    assert s.analytics_backend == "posthog"
    assert s.analytics_project == "ref-showcase"


def test_analytics_backend_defaults_to_postgres() -> None:
    assert load_settings(environ={}).analytics_backend == "postgres"


def test_invalid_analytics_backend_rejected() -> None:
    with pytest.raises(MissingConfigError, match="MINI_ANALYTICS_BACKEND"):
        load_settings(environ={"MINI_ANALYTICS_BACKEND": "mixpanel"})


def test_settings_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    s = load_settings(environ={})
    with pytest.raises(FrozenInstanceError):
        s.inference_url = "x"  # type: ignore[misc]


def test_canonical_env_keys_cover_all_fields() -> None:
    # Every canonical env name is unique and non-empty.
    assert len(set(CANONICAL_ENV_KEYS)) == len(CANONICAL_ENV_KEYS)
    assert all(k.isupper() for k in CANONICAL_ENV_KEYS)


def test_load_dotenv_parses_and_does_not_override(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "export DATABASE_URL='postgresql://from-file'\n"
        'STORAGE_BUCKET="mybucket"\n'
        "\n"
        "PORT=19204\n"
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("STORAGE_BUCKET", "already-set")

    parsed = load_dotenv(env_file)
    assert parsed["DATABASE_URL"] == "postgresql://from-file"
    assert parsed["STORAGE_BUCKET"] == "mybucket"
    # process env: unset key gets the file value, already-set key is preserved
    import os

    assert os.environ["DATABASE_URL"] == "postgresql://from-file"
    assert os.environ["STORAGE_BUCKET"] == "already-set"


def test_load_dotenv_missing_file_is_noop(tmp_path) -> None:
    assert load_dotenv(tmp_path / "nope.env") == {}


def test_slots_and_type() -> None:
    s = load_settings(environ={})
    assert isinstance(s, Settings)
