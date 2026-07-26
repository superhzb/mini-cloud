"""Live inference-tour tests — real gateway. Skip unless MINI_INFERENCE_URL is set.

The AI surface is live-required at runtime, so these hit an actual OpenAI-compatible gateway:
``models()``, multi-turn ``chat_messages``, ``embed`` (+ the in-app cosine ranking it feeds), and
streaming through the ``.openai`` passthrough. Marked ``live`` (so `make check` skips them) and
additionally skipped when no gateway URL is configured — under `check-live`, MINI_INFERENCE_URL is
pinned empty, so this whole module skips there. Runs on a full-stack gateway.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


@pytest.fixture
def ai() -> object:
    url = os.environ.get("MINI_INFERENCE_URL")
    if not url:
        pytest.skip("inference tour needs a real gateway (MINI_INFERENCE_URL)")
    from mini_cloud.config import load_settings
    from mini_cloud.inference import InferenceClient

    return InferenceClient.from_settings(
        load_settings(dotenv=None), default_model=os.environ.get("INFERENCE_MODEL")
    )


def test_models_are_advertised(ai: object) -> None:
    models = ai.models()  # type: ignore[attr-defined]
    assert isinstance(models, list)
    assert models  # a real gateway advertises at least one model


def test_chat_messages_multiturn(ai: object) -> None:
    reply = ai.chat_messages(  # type: ignore[attr-defined]
        [
            {"role": "user", "content": "Reply with exactly the word: pong"},
            {"role": "assistant", "content": "pong"},
            {"role": "user", "content": "Say it once more."},
        ],
        max_tokens=16,
    )
    assert isinstance(reply, str)
    assert reply.strip()


def test_embed_feeds_cosine_ranking(ai: object) -> None:
    model = os.environ.get("INFERENCE_EMBED_MODEL")
    if not model:
        pytest.skip("needs INFERENCE_EMBED_MODEL for the gateway embed path")
    from ref_showcase.search import cosine

    a = ai.embed("the cat sat on the mat", model=model)[0]  # type: ignore[attr-defined]
    b = ai.embed("a feline rested on the rug", model=model)[0]  # type: ignore[attr-defined]
    c = ai.embed("quarterly revenue projections", model=model)[0]  # type: ignore[attr-defined]
    # Paraphrases rank closer than an unrelated sentence.
    assert cosine(a, b) > cosine(a, c)


def test_streaming_via_openai_passthrough(ai: object) -> None:
    model = os.environ.get("INFERENCE_MODEL")
    if not model:
        pytest.skip("needs INFERENCE_MODEL for the streaming path")
    stream = ai.openai.chat.completions.create(  # type: ignore[attr-defined]
        model=model,
        messages=[{"role": "user", "content": "Count: one two three"}],
        max_tokens=16,
        stream=True,
    )
    pieces = [
        c.choices[0].delta.content for c in stream if c.choices and c.choices[0].delta.content
    ]
    assert "".join(pieces)  # got at least some streamed content
