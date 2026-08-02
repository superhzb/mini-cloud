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
- **Thin.** No password auth for real users, no server-side session store — Google is the session
  authority and access tokens are short-lived. The only state we own is *authorization* (the
  ``grants``/``users`` tables), not *authentication*.
- **One deliberate dev exception:** a local-only ``POST /dev/token`` password grant (and a seeded
  ``admin/admin``) that mints the *same* token so tests/CI/`curl` can get a JWT without a browser.
  It **fails closed** — the service refuses to boot with it enabled on anything that doesn't look
  like local dev (see :mod:`mini_cloud_identity.devlogin`).
"""

from __future__ import annotations

__version__ = "0.1.0"
