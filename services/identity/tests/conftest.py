"""Offline test wiring for the identity service.

No Postgres, no Google, no env: an :class:`InMemoryStore` and an ephemeral ES256 signing key are
injected into ``create_app``, and settings are built directly with local-dev defaults. This
exercises the real mint → sign → verify path end-to-end (the dev grant shares it with Google).
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from mini_cloud_identity.app import create_app
from mini_cloud_identity.config import IdentitySettings
from mini_cloud_identity.keys import load_signing_key
from mini_cloud_identity.store import InMemoryStore

LOCAL_SETTINGS = IdentitySettings(
    issuer="http://127.0.0.1:19210",
    audience="mini-cloud",
    port=19210,
    app_env="dev",
    signing_key_pem=None,
    signing_kid="test-key",
    signing_algorithm="ES256",
    access_ttl=900,
    database_url=None,
    google_client_id=None,
    google_client_secret=None,
    google_redirect_uri=None,
    state_secret=None,
    post_login_redirect=None,
    dev_login_enabled=True,
    dev_login_force=False,
    dev_admin_user="admin",
    dev_admin_password="admin",
)


def build_client(*, store: InMemoryStore | None = None, **overrides: object) -> TestClient:
    """A TestClient over an identity app with an in-memory store and a fixed ephemeral key."""
    settings = replace(LOCAL_SETTINGS, **overrides)  # type: ignore[arg-type]
    signing = load_signing_key(pem=None, kid=settings.signing_kid, algorithm="ES256")
    app = create_app(settings, store=store or InMemoryStore(), signing=signing)
    return TestClient(app, base_url="http://127.0.0.1")


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> TestClient:
    return build_client(store=store)
