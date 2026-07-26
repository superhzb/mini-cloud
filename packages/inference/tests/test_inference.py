"""Tests for mini_cloud.inference.

Unit tests validate config wiring and message assembly without a network (the OpenAI client is
only constructed, never called). Live tests (marked ``live``) hit a real gateway.
"""

from __future__ import annotations

import os

import pytest
from mini_cloud.config import MissingConfigError, load_settings

from mini_cloud.inference import InferenceClient, InferenceError


def test_requires_base_url() -> None:
    with pytest.raises(InferenceError, match="MINI_INFERENCE_URL"):
        InferenceClient("")


def test_from_settings_wires_url_and_key() -> None:
    s = load_settings(
        environ={"MINI_INFERENCE_URL": "http://127.0.0.1:19207/v1", "HF_TOKEN": "hf_x"}
    )
    ai = InferenceClient.from_settings(s, default_model="m")
    assert ai.base_url == "http://127.0.0.1:19207/v1"
    assert ai.default_model == "m"
    # api key falls through to the OpenAI client
    assert ai.openai.api_key == "hf_x"


def test_from_settings_missing_url_fails_fast() -> None:
    s = load_settings(environ={})
    with pytest.raises(MissingConfigError, match="MINI_INFERENCE_URL"):
        InferenceClient.from_settings(s)


def test_model_resolution_requires_a_model() -> None:
    ai = InferenceClient("http://x/v1")
    with pytest.raises(InferenceError, match="no model"):
        ai._model(None)
    assert ai._model("explicit") == "explicit"
    ai2 = InferenceClient("http://x/v1", default_model="d")
    assert ai2._model(None) == "d"


def test_default_api_key_placeholder() -> None:
    ai = InferenceClient("http://x/v1")
    assert ai.openai.api_key == "not-needed"  # gateway ignores it; SDK requires non-empty


def test_no_project_sets_no_header() -> None:
    ai = InferenceClient("http://x/v1")
    assert ai.project is None
    assert "X-MLX-Project" not in ai.openai.default_headers


def test_explicit_project_sets_gateway_header() -> None:
    ai = InferenceClient("http://x/v1", project="proj-a")
    assert ai.project == "proj-a"
    assert ai.openai.default_headers["X-MLX-Project"] == "proj-a"


def test_from_settings_project_defaults_to_app_name() -> None:
    s = load_settings(environ={"MINI_INFERENCE_URL": "http://x/v1", "APP_NAME": "ref-showcase"})
    ai = InferenceClient.from_settings(s)
    assert ai.openai.default_headers["X-MLX-Project"] == "ref-showcase"


def test_from_settings_inference_project_env_overrides_app_name() -> None:
    s = load_settings(
        environ={
            "MINI_INFERENCE_URL": "http://x/v1",
            "APP_NAME": "ref-showcase",
            "MINI_INFERENCE_PROJECT": "explicit-proj",
        }
    )
    ai = InferenceClient.from_settings(s)
    assert ai.openai.default_headers["X-MLX-Project"] == "explicit-proj"


def test_from_settings_project_arg_wins_over_env() -> None:
    s = load_settings(environ={"MINI_INFERENCE_URL": "http://x/v1", "APP_NAME": "app"})
    ai = InferenceClient.from_settings(s, project="override")
    assert ai.openai.default_headers["X-MLX-Project"] == "override"


@pytest.mark.live
def test_chat_round_trip() -> None:
    if not os.environ.get("MINI_INFERENCE_URL"):
        pytest.skip("no MINI_INFERENCE_URL")
    ai = InferenceClient.from_settings(load_settings())
    models = ai.models()
    assert models
    reply = ai.chat("Say the word OK and nothing else.", model=models[0], max_tokens=5)
    assert isinstance(reply, str) and reply
