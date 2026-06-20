"""Butterbase Storage helper — upload images from the worker, mint download URLs.

The capture frames live on the Mac; the browser can't read them. This uploads
them to Butterbase Storage and returns a stable `object_id`. Persist that id
(not a URL — presigned URLs expire); mint a fresh download URL when displaying.

Flow (per Butterbase Storage docs):
  1. POST /storage/{app_id}/upload {filename, contentType, sizeBytes} -> presigned uploadUrl + objectId
  2. PUT the bytes to uploadUrl (no auth header — the URL is presigned)
  3. store objectId; later GET /storage/{app_id}/download/{objectId} -> presigned downloadUrl

Env: BUTTERBASE_API_KEY, BUTTERBASE_APP_ID (loaded from .env via tools import).
"""

import os

import requests

import tools  # triggers .env load + provides DEFAULT_API_BASE


def _api_key() -> str:
    key = os.environ.get("BUTTERBASE_API_KEY")
    if not key:
        raise RuntimeError("BUTTERBASE_API_KEY not set")
    return key


def _app_id() -> str:
    app_id = os.environ.get("BUTTERBASE_APP_ID")
    if not app_id:
        raise RuntimeError("BUTTERBASE_APP_ID not set")
    return app_id


def _storage_base() -> str:
    # Storage endpoints are at the host root, NOT under /v1/{app_id}.
    host = os.environ.get("BUTTERBASE_API_URL", tools.DEFAULT_API_BASE)
    host = host.split("/v1/")[0].rstrip("/")
    return f"{host}/storage/{_app_id()}"


def _pick(d: dict, *names):
    for n in names:
        if n in d and d[n]:
            return d[n]
    return None


def upload_bytes(
    data: bytes,
    filename: str,
    content_type: str = "image/jpeg",
    public: bool = True,
) -> str:
    """Upload raw bytes to Butterbase Storage; return the stable object_id."""
    headers = {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}
    r = requests.post(
        f"{_storage_base()}/upload",
        headers=headers,
        json={
            "filename": filename,
            "contentType": content_type,
            "sizeBytes": len(data),
            "public": public,
        },
        timeout=30,
    )
    r.raise_for_status()
    info = r.json()
    upload_url = _pick(info, "uploadUrl", "upload_url")
    object_id = _pick(info, "objectId", "object_id")
    if not upload_url or not object_id:
        raise RuntimeError(f"unexpected upload response: {info}")
    put = requests.put(
        upload_url,
        data=data,
        headers={"Content-Type": content_type},
        timeout=60,
    )
    put.raise_for_status()
    return object_id


def upload_image(path: str, public: bool = True) -> str:
    """Upload a JPEG file; return its object_id."""
    with open(path, "rb") as f:
        data = f.read()
    return upload_bytes(data, os.path.basename(path), "image/jpeg", public)


def download_url(object_id: str) -> str:
    """Mint a fresh presigned download URL (valid ~1h) for an object_id."""
    headers = {"Authorization": f"Bearer {_api_key()}"}
    r = requests.get(f"{_storage_base()}/download/{object_id}", headers=headers, timeout=30)
    r.raise_for_status()
    return _pick(r.json(), "downloadUrl", "download_url")


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "captures/latest.jpg"
    oid = upload_image(path)
    print("object_id:", oid)
    print("download_url:", download_url(oid))
