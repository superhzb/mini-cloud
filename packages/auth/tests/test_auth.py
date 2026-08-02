"""Offline unit tests for the verifier + the FastAPI ``require_user`` dependency.

Everything runs against a locally-minted JWT signed by the test keypair (see ``conftest.py``); no
identity service is needed. The lone ``live`` test fetches the real JWKS.
"""

from __future__ import annotations

import os

import httpx
import pytest
from conftest import TEST_AUDIENCE, TEST_ISSUER, KeyFixture

from mini_cloud.auth import (
    AuthConfig,
    JwksUnavailableError,
    Principal,
    TokenInvalidError,
    TokenVerifier,
    check_grant,
)


def make_verifier(keys: KeyFixture) -> TokenVerifier:
    return TokenVerifier.from_jwks_set(keys.jwks, issuer=TEST_ISSUER, audience=TEST_AUDIENCE)


# --- happy path + claim mapping ---------------------------------------------------------
def test_verifies_valid_token_and_maps_principal(keys: KeyFixture) -> None:
    verifier = make_verifier(keys)
    token = keys.mint(sub="u1", email="a@b.com", grants={"ref-showcase": "admin"})
    principal = verifier.verify_token(token)
    assert isinstance(principal, Principal)
    assert principal.sub == "u1"
    assert principal.email == "a@b.com"
    assert principal.grants == {"ref-showcase": "admin"}
    assert principal.role_for("ref-showcase") == "admin"
    assert principal.claims["iss"] == TEST_ISSUER


def test_no_grants_claim_yields_empty_grants(keys: KeyFixture) -> None:
    principal = make_verifier(keys).verify_token(keys.mint(grants=None))
    assert principal.grants == {}
    assert principal.role_for("ref-showcase") is None


def test_non_dict_grants_claim_is_ignored(keys: KeyFixture) -> None:
    principal = make_verifier(keys).verify_token(keys.mint(extra={"grants": ["oops"]}))
    assert principal.grants == {}


# --- signature / claim validation -------------------------------------------------------
def test_rejects_expired_token(keys: KeyFixture) -> None:
    verifier = make_verifier(keys)
    token = keys.mint(expires_at=1)  # 1970
    with pytest.raises(TokenInvalidError):
        verifier.verify_token(token)


def test_rejects_wrong_issuer(keys: KeyFixture) -> None:
    token = keys.mint(issuer="https://evil.example")
    with pytest.raises(TokenInvalidError):
        make_verifier(keys).verify_token(token)


def test_rejects_wrong_audience(keys: KeyFixture) -> None:
    token = keys.mint(audience="some-other-app")
    with pytest.raises(TokenInvalidError):
        make_verifier(keys).verify_token(token)


def test_rejects_bad_signature(keys: KeyFixture) -> None:
    token = keys.mint()
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    with pytest.raises(TokenInvalidError):
        make_verifier(keys).verify_token(tampered)


def test_rejects_unknown_kid(keys: KeyFixture) -> None:
    token = keys.mint(kid="rotated-away")
    with pytest.raises(TokenInvalidError):
        make_verifier(keys).verify_token(token)


def test_rejects_empty_and_garbage(keys: KeyFixture) -> None:
    verifier = make_verifier(keys)
    with pytest.raises(TokenInvalidError):
        verifier.verify_token("")
    with pytest.raises(TokenInvalidError):
        verifier.verify_token("not.a.jwt")


# --- authorization (check_grant / role hierarchy) ---------------------------------------
def test_check_grant_app_and_role() -> None:
    admin = Principal(sub="u", email=None, grants={"app": "admin"})
    member = Principal(sub="u", email=None, grants={"app": "member"})
    none = Principal(sub="u", email=None, grants={})

    assert check_grant(admin, app=None, role=None) is None  # authN only
    assert check_grant(member, app="app", role=None) is None
    assert check_grant(member, app="app", role="member") is None
    assert check_grant(admin, app="app", role="member") is None  # admin >= member
    assert check_grant(member, app="app", role="admin") is not None  # member < admin
    assert check_grant(none, app="app", role=None) is not None  # no grant at all
    assert check_grant(member, app="other", role=None) is not None  # wrong app


def test_wildcard_grant_authorizes_every_app() -> None:
    # A single ("*", "admin") grant — the dev default admin — authorizes on any app, at role.
    admin = Principal(sub="u", email=None, grants={"*": "admin"})
    assert check_grant(admin, app="ref-showcase", role="member") is None
    assert check_grant(admin, app="demo-x", role="admin") is None
    assert admin.is_authorized("anything") is True
    assert admin.role_for("ref-showcase") == "admin"  # introspection resolves the wildcard too

    # A wildcard *member* still can't clear an admin bar.
    member = Principal(sub="u", email=None, grants={"*": "member"})
    assert check_grant(member, app="app", role="admin") is not None


def test_explicit_grant_wins_over_wildcard() -> None:
    # An explicit per-app grant scopes a wildcard admin *down* for that one app.
    p = Principal(sub="u", email=None, grants={"*": "admin", "locked": "viewer"})
    assert p.role_for("locked") == "viewer"
    assert check_grant(p, app="locked", role="member") is not None  # viewer < member here
    assert check_grant(p, app="elsewhere", role="admin") is None  # wildcard still applies elsewhere


def test_unknown_role_requires_exact_match() -> None:
    p = Principal(sub="u", email=None, grants={"app": "editor"})
    assert check_grant(p, app="app", role="editor") is None
    assert check_grant(p, app="app", role="member") is not None  # 'editor' isn't ranked


# --- config defaults --------------------------------------------------------------------
def test_jwks_url_defaults_from_issuer() -> None:
    cfg = AuthConfig(issuer="https://identity.example/")
    assert cfg.resolved_jwks_url() == "https://identity.example/.well-known/jwks.json"
    cfg2 = AuthConfig(issuer="https://identity.example", jwks_url="https://x/keys")
    assert cfg2.resolved_jwks_url() == "https://x/keys"


def test_from_settings_reads_canonical_env() -> None:
    from mini_cloud.config import load_settings

    settings = load_settings(
        dotenv=None,
        environ={"MINI_AUTH_ISSUER": "https://identity.example", "APP_NAME": "x"},
    )
    verifier = TokenVerifier.from_settings(settings)
    assert verifier.config.issuer == "https://identity.example"
    assert verifier.config.audience == "mini-cloud"  # default


# --- network resolver: fetch, cache, refresh-on-unknown-kid -----------------------------
def test_httpx_resolver_fetches_and_caches(keys: KeyFixture) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=keys.jwks)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    verifier = TokenVerifier.from_config(
        AuthConfig(issuer=TEST_ISSUER, jwks_url="https://identity.test.local/.well-known/jwks.json"),
        client=client,
    )
    # Two verifications: JWKS fetched once and reused (kid is known).
    for _ in range(2):
        assert verifier.verify_token(keys.mint(grants={"a": "member"})).grants == {"a": "member"}
    assert calls["n"] == 1


def test_httpx_resolver_raises_jwks_unavailable(keys: KeyFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    verifier = TokenVerifier.from_config(
        AuthConfig(issuer=TEST_ISSUER, jwks_url="https://identity.test.local/.well-known/jwks.json"),
        client=client,
    )
    with pytest.raises(JwksUnavailableError):
        verifier.verify_token(keys.mint())


# --- FastAPI dependency -----------------------------------------------------------------
def build_app(verifier: TokenVerifier):
    from fastapi import Depends, FastAPI

    from mini_cloud.auth.fastapi import require_user

    app = FastAPI()

    dep = require_user(app="ref-showcase", role="member", verifier=verifier)

    @app.get("/protected")
    def protected(user: Principal = Depends(dep)) -> dict[str, object]:  # noqa: B008
        return {"sub": user.sub, "role": user.role_for("ref-showcase")}

    return app


def test_fastapi_require_user_401_403_200(keys: KeyFixture) -> None:
    from fastapi.testclient import TestClient

    client = TestClient(build_app(make_verifier(keys)))

    # 401 — no token
    assert client.get("/protected").status_code == 401
    assert "bearer" in client.get("/protected").headers.get("www-authenticate", "").lower()

    # 401 — malformed token
    bad = client.get("/protected", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401

    # 403 — valid token, but no grant for the app
    no_grant = keys.mint(grants={"other-app": "admin"})
    r = client.get("/protected", headers={"Authorization": f"Bearer {no_grant}"})
    assert r.status_code == 403

    # 403 — has the app grant but role too low (viewer < member)
    low = keys.mint(grants={"ref-showcase": "viewer"})
    assert client.get("/protected", headers={"Authorization": f"Bearer {low}"}).status_code == 403

    # 200 — valid token with a sufficient grant (admin >= member)
    ok = keys.mint(sub="u9", grants={"ref-showcase": "admin"})
    good = client.get("/protected", headers={"Authorization": f"Bearer {ok}"})
    assert good.status_code == 200
    assert good.json() == {"sub": "u9", "role": "admin"}


# --- live: real JWKS parses -------------------------------------------------------------
@pytest.mark.live
def test_live_jwks_is_fetchable(keys: KeyFixture) -> None:
    issuer = os.environ["MINI_AUTH_ISSUER"]
    verifier = TokenVerifier.from_config(AuthConfig(issuer=issuer))
    # A token minted by our test key carries a well-formed header (with a kid) but a kid the real
    # JWKS won't hold: verification forces a real JWKS fetch, then fails at key lookup. Asserting
    # TokenInvalidError (not JwksUnavailableError) proves the real key set fetched + parsed.
    with pytest.raises(TokenInvalidError):
        verifier.verify_token(keys.mint(issuer=issuer))
