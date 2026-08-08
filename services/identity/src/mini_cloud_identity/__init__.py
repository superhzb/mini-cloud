"""mini-cloud-identity — the platform's login authority (Phase 6).

This is the *minting* half of mini-cloud identity; the verifying half is the `mini-cloud-auth` SDK
(`packages/auth`) that every app links. The split is deliberate and is the whole security model:

- **This service holds the private signing key** and is the only thing that mints tokens. It bounces
  a human through Google OAuth, reads their per-app authorization from the ``grants`` table, folds
  it into a short-lived, asymmetrically-signed platform JWT, and publishes the matching *public*
  keys at ``/.well-known/jwks.json``.
- **Every app holds no secret** — it fetches those public keys and verifies tokens locally
  (`mini_cloud.auth.verify_token`), so there's no call back here on the hot path.

Design decisions live in ``docs/identity-plan.md``. The load-bearing ones:

- **Platform-wide identity, per-app authZ.** One token, a fixed ``aud: "mini-cloud"``; *who may use
  which app* is the ``grants`` claim (``{app: role}``), never ``aud``.
- **Two login methods, one contract.** Google OAuth and LAN-only username/password login both mint
  the same short-lived JWT. There is no server-side session store.
- **Safe developer default:** ``admin/admin`` receives a platform-wide grant in local development;
  public Host headers are rejected and non-dev startup fails closed.
"""

from __future__ import annotations

__version__ = "0.1.0"
