# `mini-cloud-storage`

An S3/MinIO client scoped to **one per-project bucket**. Retires the filesystem-as-object-store
pattern re-invented 5+ times across the workspace. The seam is the plain **S3 API**
(`STORAGE_ENDPOINT` + access keys), so MinIO locally and managed S3 on a VPS are the same code.

```python
from mini_cloud.config import load_settings
from mini_cloud.storage import Storage

store = Storage.from_settings(load_settings())
store.ensure_bucket()
store.put_bytes("reports/2026.json", b"{...}", content_type="application/json")
data = store.get_bytes("reports/2026.json")
url = store.presigned_get_url("reports/2026.json", expires_in=3600)  # direct client download
```

Every method works within the single `STORAGE_BUCKET` the app is configured with — there is no
cross-bucket surface by design (bucket-per-project isolation).

| Method | Purpose |
|---|---|
| `ensure_bucket()` / `bucket_exists()` | idempotent bucket create / check |
| `put_bytes` / `put_stream` | upload bytes or a file-like (multipart for large) |
| `get_bytes` / `exists` / `delete` | fetch (`KeyError` if absent) / check / remove |
| `list(prefix, limit=…)` | paginated listing |
| `presigned_get_url` / `presigned_put_url` | time-limited direct client access |

Uses path-style addressing + SigV4 (MinIO-portable). Live round-trip tests:

```bash
STORAGE_ENDPOINT=http://127.0.0.1:19000 STORAGE_ACCESS_KEY=minioadmin \
STORAGE_SECRET_KEY=minioadmin STORAGE_BUCKET=x pytest --run-live
```
