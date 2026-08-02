"""Signing keys and the JWKS trust anchor.

The service signs platform JWTs with an **asymmetric** private key (ES256 by default; RS256 if a
PEM says so) and publishes the matching *public* key as a JWKS the SDK verifiers read. In
production the private key is **mounted, never in the repo** (``MINI_AUTH_SIGNING_KEY[_FILE]``). For
zero-setup local dev we mint an **ephemeral** ES256 key at boot — tokens still verify against the
live JWKS; they just don't survive a restart, which is exactly right for a dev box.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
)
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

_ES256 = "ES256"


@dataclass(frozen=True, slots=True)
class SigningKey:
    """A private signing key plus the metadata needed to sign and to publish its public half."""

    private_pem: bytes
    kid: str
    algorithm: str  # ES256 or RS256 — matches the key type in ``private_pem``
    ephemeral: bool  # True → minted at boot, lost on restart (dev only)

    def public_jwk(self) -> dict[str, Any]:
        """The public key as a JWK, tagged with ``kid``/``use``/``alg`` so a verifier matches it."""
        private = load_pem_private_key(self.private_pem, password=None)
        public: Any = private.public_key()  # type: ignore[union-attr]  # a private key has one
        if self.algorithm.startswith("ES"):
            jwk = json.loads(ECAlgorithm(ECAlgorithm.SHA256).to_jwk(public))
        else:
            jwk = json.loads(RSAAlgorithm(RSAAlgorithm.SHA256).to_jwk(public))
        jwk.update({"kid": self.kid, "use": "sig", "alg": self.algorithm})
        return jwk

    def jwks(self) -> dict[str, Any]:
        """The full JWKS document served at ``/.well-known/jwks.json`` (one key today; ``kid``
        lets us publish old+new during a rotation without a flag day)."""
        return {"keys": [self.public_jwk()]}


def load_signing_key(
    *, pem: str | bytes | None, kid: str | None, algorithm: str = _ES256
) -> SigningKey:
    """Build the service's :class:`SigningKey`.

    With ``pem`` set (a mounted key), honor it and its ``algorithm``. With no ``pem`` — a fresh dev
    clone — mint an ephemeral ES256 key so the service boots and mints with zero setup. Callers
    should log loudly when :attr:`SigningKey.ephemeral` is True.
    """
    if pem:
        material = pem.encode() if isinstance(pem, str) else pem
        # Fail fast on an unreadable/encrypted key rather than at first mint.
        load_pem_private_key(material, password=None)
        return SigningKey(
            private_pem=material,
            kid=kid or "mini-auth-1",
            algorithm=algorithm,
            ephemeral=False,
        )
    private = ec.generate_private_key(ec.SECP256R1())
    material = private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    return SigningKey(
        private_pem=material,
        kid=kid or "dev-ephemeral",
        algorithm=_ES256,
        ephemeral=True,
    )
