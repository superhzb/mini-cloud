"""Google OAuth (Authorization Code + PKCE), kept stateless so we honor "no session store".

The flow:

1. ``/login`` builds the Google consent URL with a PKCE ``code_challenge`` and a **signed**
   ``state`` (a short-lived JWT carrying the PKCE ``code_verifier`` + return URL). Signing it means
   we need no server-side session to remember the verifier across the two legs — the browser
   carries it, and we reject any tampering.
2. ``/callback`` verifies the state JWT, exchanges the ``code`` (+ ``code_verifier``) for Google's
   ``id_token``, and verifies that against Google's JWKS. The result is a :class:`GoogleIdentity`.

This module does the wire work only; folding grants and minting the platform token is the caller's
job. It's exercised end-to-end only under ``--run-live`` (real Google creds); the mint path it feeds
is covered offline via the dev grant, which shares the exact same downstream.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass

import httpx
import jwt

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
_STATE_TTL = 600  # 10 min to complete the round-trip
_STATE_ALG = "HS256"  # state is signed with the service's own secret, never leaves us


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    """The verified human behind a completed Google login."""

    sub: str
    email: str | None
    name: str | None
    picture: str | None


@dataclass(frozen=True, slots=True)
class GoogleOAuth:
    """Google OAuth client config + the two-leg flow. ``state_secret`` signs the PKCE state JWT."""

    client_id: str
    client_secret: str
    redirect_uri: str
    state_secret: str

    def authorization_url(self, *, return_to: str | None = None) -> str:
        """The Google consent URL to redirect the browser to, with PKCE + a signed state."""
        verifier = secrets.token_urlsafe(64)
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        now = int(time.time())
        state = jwt.encode(
            {"v": verifier, "r": return_to, "iat": now, "exp": now + _STATE_TTL},
            self.state_secret,
            algorithm=_STATE_ALG,
        )
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
        return _AUTH_ENDPOINT + "?" + _urlencode(params)

    def exchange(
        self, *, code: str, state: str, client: httpx.Client | None = None
    ) -> GoogleIdentity:
        """Verify ``state``, swap ``code`` for an ``id_token``, and return the verified identity."""
        try:
            decoded = jwt.decode(state, self.state_secret, algorithms=[_STATE_ALG])
        except jwt.PyJWTError as exc:
            raise GoogleAuthError(f"invalid or expired OAuth state: {exc}") from exc
        verifier = decoded.get("v")
        if not verifier:
            raise GoogleAuthError("OAuth state missing PKCE verifier")

        owns_client = client is None
        http = client or httpx.Client(timeout=10.0)
        try:
            resp = http.post(
                _TOKEN_ENDPOINT,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code_verifier": verifier,
                },
            )
            if resp.status_code != 200:
                raise GoogleAuthError(
                    f"Google token exchange failed ({resp.status_code}): {resp.text}"
                )
            id_token = resp.json().get("id_token")
            if not id_token:
                raise GoogleAuthError("Google token response had no id_token")
            return self._verify_id_token(id_token, http)
        finally:
            if owns_client:
                http.close()

    def _verify_id_token(self, id_token: str, http: httpx.Client) -> GoogleIdentity:
        signing_key = jwt.PyJWKClient(_JWKS_URI).get_signing_key_from_jwt(id_token)
        try:
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.client_id,
                options={"require": ["sub", "iss", "aud", "exp"]},
            )
        except jwt.PyJWTError as exc:
            raise GoogleAuthError(f"invalid Google id_token: {exc}") from exc
        if claims.get("iss") not in _ISSUERS:
            raise GoogleAuthError(f"unexpected id_token issuer {claims.get('iss')!r}")
        if claims.get("email_verified") is not True:
            raise GoogleAuthError("Google account email is not verified")
        return GoogleIdentity(
            sub=str(claims["sub"]),
            email=claims.get("email"),
            name=claims.get("name"),
            picture=claims.get("picture"),
        )


class GoogleAuthError(RuntimeError):
    """A Google OAuth leg failed (bad state, exchange error, or an id_token that won't verify)."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _urlencode(params: dict[str, str]) -> str:
    from urllib.parse import urlencode

    return urlencode(params)
