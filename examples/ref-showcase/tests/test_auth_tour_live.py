"""Live end-to-end identity proof: a real platform JWT clears the protected endpoint.

Run against a real ``mini-cloud-identity``:

    # 1. log in through the identity service in a browser (real Google OAuth), copy the JWT
    export MINI_AUTH_ISSUER=https://identity.brettbot.ca
    export MINI_AUTH_TEST_TOKEN=<the platform JWT>
    uv run --package ref-showcase pytest examples/ref-showcase -k auth_tour_live --run-live

The verifier is the app's real process default (JWKS fetched from ``MINI_AUTH_ISSUER``); this test
does no minting — it verifies a genuine token minted by the identity service after Google verified
the human. The token's holder must have a ``ref-showcase`` grant of ``member`` or higher.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from ref_showcase.app import create_app

pytestmark = pytest.mark.live

_HAS_TOKEN = bool(os.environ.get("MINI_AUTH_TEST_TOKEN") and os.environ.get("MINI_AUTH_ISSUER"))
_skip_no_token = pytest.mark.skipif(
    not _HAS_TOKEN, reason="set MINI_AUTH_ISSUER + MINI_AUTH_TEST_TOKEN for the live auth proof"
)


@_skip_no_token
def test_real_jwt_clears_protected_endpoint() -> None:
    token = os.environ["MINI_AUTH_TEST_TOKEN"]
    with TestClient(create_app()) as client:
        resp = client.get("/auth/whoami", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["sub"]
        assert body["grants"].get("ref-showcase") in {"member", "admin"}


@_skip_no_token
def test_missing_token_is_401_against_real_verifier() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/auth/whoami").status_code == 401
