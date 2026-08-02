"""Test fixtures for mini-cloud-auth.

Unit tests are fully offline: a per-session ES256 keypair mints tokens, and the verifier is built
over that key's JWKS with :meth:`TokenVerifier.from_jwks_set` — no identity service, no network.
The one live test (``--run-live`` + ``MINI_AUTH_ISSUER``) fetches the real JWKS to prove the wire
format parses.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from jwt.algorithms import ECAlgorithm

TEST_ISSUER = "https://identity.test.local"
TEST_AUDIENCE = "mini-cloud"
TEST_KID = "test-key-1"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live", action="store_true", default=False, help="run tests that hit real infra"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    import os

    if config.getoption("--run-live") and os.environ.get("MINI_AUTH_ISSUER"):
        return
    skip = pytest.mark.skip(reason="needs --run-live and MINI_AUTH_ISSUER")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@dataclass
class KeyFixture:
    """A test keypair plus helpers to mint tokens and expose a JWKS the verifier can read."""

    private_pem: bytes
    jwks: dict[str, Any]
    kid: str = TEST_KID

    def mint(
        self,
        *,
        sub: str = "google-oauth2|1234567890",
        email: str | None = "dev@example.com",
        grants: dict[str, str] | None = None,
        issuer: str = TEST_ISSUER,
        audience: str = TEST_AUDIENCE,
        ttl: int = 900,
        expires_at: int | None = None,
        kid: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "sub": sub,
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "nbf": now,
            "exp": expires_at if expires_at is not None else now + ttl,
        }
        if email is not None:
            claims["email"] = email
        if grants is not None:
            claims["grants"] = grants
        if extra:
            claims.update(extra)
        headers = {"kid": kid if kid is not None else self.kid}
        return jwt.encode(claims, self.private_pem, algorithm="ES256", headers=headers)


@pytest.fixture(scope="session")
def keys() -> KeyFixture:
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    public_jwk = json.loads(ECAlgorithm(ECAlgorithm.SHA256).to_jwk(private_key.public_key()))
    public_jwk.update({"kid": TEST_KID, "use": "sig", "alg": "ES256"})
    return KeyFixture(private_pem=private_pem, jwks={"keys": [public_jwk]})
