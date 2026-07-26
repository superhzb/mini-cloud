# examples — in-repo reference apps

Reference apps that live **inside** mini-cloud, so the SDK and templates are proven without ever
editing a downstream repo. They are the SDK proof, the template seeds, and the scorecard regression
guards.

| App | Type | Role |
|---|---|---|
| [`ref-fastapi`](ref-fastapi) | fastapi | Exercises every Python SDK package end-to-end (config · db + queue · storage · obs · inference). Seeds the `fastapi` template. Must hold **7/7** (`mini score examples/ref-fastapi`). |
| `ref-vite` | vite | Arrives with the Phase-5 TS SDK. |

Run `ref-fastapi`:

```bash
make -C ../infra up && make -C ../infra project NAME=ref-fastapi
cd ref-fastapi && make setup && make run     # + `make worker` in another shell
```
