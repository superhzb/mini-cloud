"""Offline proof of the identity service: the dev grant mints a real, verifiable platform JWT, the
fail-closed guard holds, and a wildcard-admin token authorizes an app through the SDK verifier.
"""

from __future__ import annotations

import pytest
from conftest import build_client
from fastapi.testclient import TestClient
from mini_cloud.auth import TokenVerifier, check_grant

from mini_cloud_identity.config import IdentitySettings
from mini_cloud_identity.devlogin import IdentityConfigError, _looks_local, resolve_dev_login
from mini_cloud_identity.keys import load_signing_key
from mini_cloud_identity.passwords import hash_password, verify_password
from mini_cloud_identity.store import InMemoryStore


# --- passwords --------------------------------------------------------------------------
def test_password_roundtrip_and_rejects_wrong() -> None:
    encoded = hash_password("s3cret")
    assert encoded.startswith("pbkdf2_sha256$")
    assert verify_password("s3cret", encoded) is True
    assert verify_password("nope", encoded) is False
    assert verify_password("s3cret", "garbage") is False  # malformed → False, never raises


# --- fail-closed dev-login guard --------------------------------------------------------
def test_resolve_dev_login_local_is_allowed() -> None:
    assert resolve_dev_login(enabled=True, issuer="http://127.0.0.1:19210", app_env="dev") is True
    assert resolve_dev_login(enabled=True, issuer=None, app_env="dev") is True
    assert resolve_dev_login(enabled=False, issuer="https://identity.brettbot.ca", app_env="prod") is False


def test_resolve_dev_login_refuses_to_boot_when_remote() -> None:
    with pytest.raises(IdentityConfigError):
        resolve_dev_login(enabled=True, issuer="https://identity.brettbot.ca", app_env="dev")
    with pytest.raises(IdentityConfigError):
        resolve_dev_login(enabled=True, issuer="http://127.0.0.1:19210", app_env="prod")
    # …unless deliberately forced.
    assert resolve_dev_login(
        enabled=True, issuer="https://identity.brettbot.ca", app_env="prod", force=True
    ) is True


def test_looks_local() -> None:
    assert _looks_local(None) is True
    assert _looks_local("http://localhost:19210") is True
    assert _looks_local("https://box.local") is True
    assert _looks_local("https://identity.brettbot.ca") is False


def test_app_refuses_to_boot_with_remote_dev_login() -> None:
    with pytest.raises(IdentityConfigError):
        build_client(issuer="https://identity.brettbot.ca", app_env="prod")


# --- JWKS + dev grant + mint path -------------------------------------------------------
def test_jwks_publishes_one_signing_key(client: TestClient) -> None:
    jwks = client.get("/.well-known/jwks.json").json()
    assert len(jwks["keys"]) == 1
    key = jwks["keys"][0]
    assert key["kid"] == "test-key"
    assert key["use"] == "sig"
    assert key["alg"] == "ES256"


def test_dev_token_mints_admin_and_userinfo_reads_it(client: TestClient) -> None:
    resp = client.post("/dev/token", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    token = body["access_token"]

    info = client.get("/userinfo", headers={"Authorization": f"Bearer {token}"}).json()
    assert info["sub"] == "dev|admin"
    assert info["email"] == "admin@local"
    assert info["grants"] == {"*": "admin"}  # seeded platform-wide grant


def test_dev_token_rejects_wrong_password(client: TestClient) -> None:
    assert client.post("/dev/token", json={"username": "admin", "password": "x"}).status_code == 401
    assert client.post("/dev/token", json={"username": "ghost", "password": "admin"}).status_code == 401


def test_dev_token_404_when_disabled() -> None:
    disabled = build_client(dev_login_enabled=False)
    assert disabled.post("/dev/token", json={"username": "admin", "password": "admin"}).status_code == 404
    # …and with dev login off, nothing is seeded, so there is no admin to log in as anyway.


def test_userinfo_401_without_token(client: TestClient) -> None:
    assert client.get("/userinfo").status_code == 401
    assert client.get("/userinfo", headers={"Authorization": "Bearer nope"}).status_code == 401


# --- the whole point: a seeded admin token authorizes an app via the SDK's "*" fallback --
def test_wildcard_admin_token_authorizes_ref_showcase_via_sdk(client: TestClient) -> None:
    token = client.post("/dev/token", json={"username": "admin", "password": "admin"}).json()[
        "access_token"
    ]
    # Verify with a *fresh* SDK verifier built from the live JWKS — exactly what an app does.
    jwks = client.get("/.well-known/jwks.json").json()
    verifier = TokenVerifier.from_jwks_set(
        jwks, issuer="http://127.0.0.1:19210", audience="mini-cloud"
    )
    principal = verifier.verify_token(token)
    # One "*" grant, yet authorized on a concrete app at role — the plan's default-admin story.
    assert check_grant(principal, app="ref-showcase", role="member") is None
    assert principal.role_for("ref-showcase") == "admin"


def test_added_dev_tester_gets_only_their_grants() -> None:
    store = InMemoryStore()
    store.upsert_dev_user(
        username="tester", email="tester@local", password_hash=hash_password("pw")
    )
    store.set_grant(email="tester@local", app="ref-showcase", role="member")
    client = build_client(store=store)

    token = client.post("/dev/token", json={"username": "tester", "password": "pw"}).json()[
        "access_token"
    ]
    info = client.get("/userinfo", headers={"Authorization": f"Bearer {token}"}).json()
    assert info["grants"] == {"ref-showcase": "member"}  # scoped, not platform-wide


# --- probes + config edges --------------------------------------------------------------
def test_readyz_reports_dev_and_signing_state(client: TestClient) -> None:
    body = client.get("/readyz").json()
    assert body["ready"] is True
    assert body["dev_login"] is True
    assert body["signing_ephemeral"] is True
    assert body["google_configured"] is False


def test_login_503_without_google(client: TestClient) -> None:
    assert client.get("/login", follow_redirects=False).status_code == 503


def test_settings_from_env_defaults() -> None:
    cfg = IdentitySettings.from_env({"APP_NAME": "x"})
    assert cfg.port == 19210
    assert cfg.audience == "mini-cloud"
    assert cfg.issuer == "http://127.0.0.1:19210"
    assert cfg.dev_login_enabled is True  # on by default in local dev


def test_mounted_key_is_not_ephemeral() -> None:
    ephemeral = load_signing_key(pem=None, kid="k", algorithm="ES256")
    mounted = load_signing_key(pem=ephemeral.private_pem.decode(), kid="k", algorithm="ES256")
    assert mounted.ephemeral is False
    assert ephemeral.ephemeral is True
