"""mini_cloud.inference — one thin OpenAI-compatible client at the one canonical gateway URL.

Folds together the hand-rolled inference clients (`hub-gateway`, `pkg-llm-backend`, every
frontend's `apiClient.ts`) that were pointed at **three different** URLs (`8933` / `9000` / `5900`).
There is now exactly one name — ``MINI_INFERENCE_URL`` — and this client reads it. Because the
gateway speaks the OpenAI wire protocol, graduating to a cloud provider is a one-env-var flip with
no code change (already designed into ``mlx-platform``).

    from mini_cloud.config import load_settings
    from mini_cloud.inference import InferenceClient

    ai = InferenceClient.from_settings(load_settings())
    text = ai.chat("Summarise this.", model="mlx-community/…")
    vec = ai.embed("hello", model="…")

This is a thin wrapper, not a framework: prompts, batching, output parsing, and retry policy stay
in the application (per the ADR's ownership split). ``ai.openai`` exposes the underlying
``openai.OpenAI`` for anything the convenience methods don't cover.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openai import OpenAI

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mini_cloud.config import Settings

__version__ = "0.1.0"

__all__ = ["InferenceClient", "InferenceError"]


class InferenceError(RuntimeError):
    """Raised for inference misconfiguration (e.g. no gateway URL)."""


class InferenceClient:
    """A thin, OpenAI-compatible client bound to ``MINI_INFERENCE_URL``."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        default_model: str | None = None,
        project: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        if not base_url:
            raise InferenceError("MINI_INFERENCE_URL is required")
        # The MLX gateway ignores the key but the OpenAI SDK requires a non-empty string; a cloud
        # provider on graduation supplies a real one via the same field.
        self.base_url = base_url
        self.default_model = default_model
        self.project = project
        # The multi-tenant MLX gateway identifies the calling project via the ``X-MLX-Project``
        # header on every request; setting it as a default header means chat/embed/models *and*
        # the ``.openai`` passthrough all carry it with no per-call plumbing. Harmless against a
        # single-tenant provider that ignores the header.
        default_headers = {"X-MLX-Project": project} if project else None
        self.openai = OpenAI(
            base_url=base_url,
            api_key=api_key or "not-needed",
            timeout=timeout,
            default_headers=default_headers,
        )

    @classmethod
    def from_settings(
        cls, settings: Settings, *, default_model: str | None = None, project: str | None = None
    ) -> InferenceClient:
        """Build from canonical settings. Uses ``HF_TOKEN`` as the API key if present (harmless
        against the local gateway, real against a HF-backed provider).

        The gateway project defaults to ``MINI_INFERENCE_PROJECT`` and, when that's unset, to
        ``APP_NAME`` — so an app that already names itself is identified to the gateway with no
        extra config. Pass ``project=`` to override both."""
        return cls(
            settings.require("inference_url"),
            api_key=settings.hf_token,
            default_model=default_model,
            project=project or settings.inference_project or settings.app_name,
        )

    def _model(self, model: str | None) -> str:
        chosen = model or self.default_model
        if not chosen:
            raise InferenceError("no model given and no default_model set")
        return chosen

    def chat(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,  # noqa: ANN401 — intentional passthrough to the OpenAI SDK
    ) -> str:
        """Single-turn convenience: send one user prompt (+ optional system) and return the reply
        text. For multi-turn or streaming, use :meth:`chat_messages` or ``self.openai`` directly."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat_messages(
            messages, model=model, temperature=temperature, max_tokens=max_tokens, **kwargs
        )

    def chat_messages(
        self,
        messages: Sequence[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,  # noqa: ANN401 — intentional passthrough to the OpenAI SDK
    ) -> str:
        """Full chat-completions call over a message list; returns the first choice's content."""
        resp = self.openai.chat.completions.create(
            model=self._model(model),
            messages=messages,  # type: ignore[arg-type]  # OpenAI SDK typed dicts; plain dicts work
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        return resp.choices[0].message.content or ""

    def embed(
        self,
        text: str | Sequence[str],
        *,
        model: str | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> list[list[float]]:
        """Return an embedding vector per input (always a list of vectors, even for one input)."""
        inputs = [text] if isinstance(text, str) else list(text)
        resp = self.openai.embeddings.create(model=self._model(model), input=inputs, **kwargs)
        return [d.embedding for d in resp.data]

    def models(self) -> list[str]:
        """List model IDs the gateway advertises."""
        return [m.id for m in self.openai.models.list().data]
