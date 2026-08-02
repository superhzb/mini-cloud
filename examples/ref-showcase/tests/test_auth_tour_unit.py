"""Offline proof of the identity tour: the protected endpoint returns 200 / 401 / 403 correctly.

Fully offline — a test keypair mints platform JWTs and an offline verifier (built over that key's
JWKS) is injected as the process default via ``mini_cloud.auth.configure``. No identity service and
no network: exactly the "wire auth in 3 lines, test it with a fixture keypair" story an adopting app
gets. The live end-to-end (real Google → real JWT) lives in ``test_auth_tour_live.py``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jwt
import mini_cloud.auth as auth
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm

from ref_showcase.app import create_app

ISSUER = "https://identity.test.local"
AUDIENCE = "mini-cloud"
KID = "ref-test-key"


def _mint(private_pem: bytes, **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": "google-oauth2|42",
        "email": "dev@example.com",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 900,
    }
    claims.update(overrides)
    return jwt.encode(claims, private_pem, algorithm="ES256", headers={"kid": KID})


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    """A ref-showcase client with an offline verifier installed as the process default."""
    for variable in (
        "DATABASE_URL",
        "STORAGE_ENDPOINT",
        "STORAGE_BUCKET",
        "MINI_INFERENCE_URL",
        "MINI_ANALYTICS_DSN",
        "MINI_AUTH_ISSUER",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)

    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    public_jwk = json.loads(ECAlgorithm(ECAlgorithm.SHA256).to_jwk(private_key.public_key()))
    public_jwk.update({"kid": KID, "use": "sig", "alg": "ES256"})
    verifier = auth.TokenVerifier.from_jwks_set(
        {"keys": [public_jwk]}, issuer=ISSUER, audience=AUDIENCE
    )
    auth.configure(verifier)
    try:
        with TestClient(create_app()) as test_client:
            test_client.mint = lambda **kw: _mint(private_pem, **kw)  # type: ignore[attr-defined]
            yield test_client
    finally:
        auth.configure(None)  # reset the process-global default for other tests


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_auth_config_reports_disabled_without_issuer(client: TestClient) -> None:
    body = client.get("/auth/config").json()
    assert body["configured"] is False
    assert body["guarded"] == {"app": "ref-showcase", "min_role": "member"}
    assert body["roles"] == ["viewer", "member", "admin"]


def test_whoami_401_without_token(client: TestClient) -> None:
    resp = client.get("/auth/whoami")
    assert resp.status_code == 401
    assert "bearer" in resp.headers.get("www-authenticate", "").lower()


def test_whoami_401_with_garbage_token(client: TestClient) -> None:
    assert client.get("/auth/whoami", headers=_bearer("not-a-jwt")).status_code == 401


def test_whoami_403_without_app_grant(client: TestClient) -> None:
    token = client.mint(grants={"other-app": "admin"})  # type: ignore[attr-defined]
    assert client.get("/auth/whoami", headers=_bearer(token)).status_code == 403


def test_whoami_403_when_role_too_low(client: TestClient) -> None:
    token = client.mint(grants={"ref-showcase": "viewer"})  # type: ignore[attr-defined]
    assert client.get("/auth/whoami", headers=_bearer(token)).status_code == 403


def test_whoami_200_with_member_grant(client: TestClient) -> None:
    token = client.mint(grants={"ref-showcase": "member"})  # type: ignore[attr-defined]
    resp = client.get("/auth/whoami", headers=_bearer(token))
    assert resp.status_code == 200
    assert resp.json() == {
        "sub": "google-oauth2|42",
        "email": "dev@example.com",
        "role": "member",
        "grants": {"ref-showcase": "member"},
    }


def test_whoami_200_with_admin_grant_via_hierarchy(client: TestClient) -> None:
    token = client.mint(grants={"ref-showcase": "admin"})  # type: ignore[attr-defined]
    resp = client.get("/auth/whoami", headers=_bearer(token))
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_auth_snapshot_configured_when_issuer_set() -> None:
    from mini_cloud.config import load_settings

    from ref_showcase.auth_tour import auth_snapshot

    settings = load_settings(
        dotenv=None, environ={"MINI_AUTH_ISSUER": "https://identity.example", "APP_NAME": "x"}
    )
    snap = auth_snapshot(settings)
    assert snap["configured"] is True
    assert snap["issuer"] == "https://identity.example"
    assert snap["jwks_url"] == "https://identity.example/.well-known/jwks.json"
    assert snap["audience"] == "mini-cloud"
