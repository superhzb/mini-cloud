# `mini-cloud-inference`

One thin OpenAI-compatible client at the **one canonical** `MINI_INFERENCE_URL`. Folds together
the hand-rolled inference clients (`hub-gateway`, `pkg-llm-backend`, every frontend's
`apiClient.ts`) that were pointed at three different URLs (`8933` / `9000` / `5900`).

```python
from mini_cloud.config import load_settings
from mini_cloud.inference import InferenceClient

ai = InferenceClient.from_settings(load_settings(), default_model="mlx-community/…")
text = ai.chat("Summarise this.", system="You are terse.")
vecs = ai.embed(["a", "b"])
ids = ai.models()
```

Because the MLX gateway speaks the OpenAI wire protocol, **graduation to a cloud provider is one
env-var flip** (`MINI_INFERENCE_URL` → the provider's base URL) with no code change.

| Method | Purpose |
|---|---|
| `chat(prompt, *, system, model, temperature, max_tokens)` | single-turn; returns reply text |
| `chat_messages(messages, …)` | full message-list chat completion |
| `embed(text \| [texts], …)` | one vector per input |
| `models()` | list advertised model IDs |
| `.openai` | the underlying `openai.OpenAI` for anything else (streaming, tools, …) |

This is a **thin wrapper, not a framework**: prompts, batching, output parsing, and retry policy
stay in the app (per the ADR ownership split). Live test:

```bash
MINI_INFERENCE_URL=http://127.0.0.1:19207/v1 pytest --run-live
```
