"""Deploy frontend/ to Butterbase static hosting. Reads credentials from .env."""

from __future__ import annotations

import json
import pathlib
import time
import urllib.error
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
ZIP_PATH = ROOT / "frontend.zip"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = ROOT / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _pick(data: dict, *keys: str):
    for k in keys:
        if k in data and data[k]:
            return data[k]
    return None


def build_zip() -> int:
    """Zip frontend/ with forward-slash paths (required by Butterbase / Cloudflare)."""
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    count = 0
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(FRONTEND_DIR.rglob("*")):
            if not path.is_file():
                continue
            arcname = path.relative_to(FRONTEND_DIR).as_posix()
            zf.write(path, arcname)
            count += 1
    return count


def _request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict | None = None,
    timeout: int = 120,
) -> tuple[int, dict | str]:
    hdrs = dict(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return resp.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(detail)
        except json.JSONDecodeError:
            return exc.code, detail


def main() -> None:
    env = load_env()
    app_id = env["BUTTERBASE_APP_ID"]
    api_key = env["BUTTERBASE_API_KEY"]
    app_url = env.get("BUTTERBASE_API_URL", f"https://api.butterbase.ai/v1/{app_id}")
    control = app_url.split("/v1/")[0] if "/v1/" in app_url else "https://api.butterbase.ai"

    n_files = build_zip()
    zip_size = ZIP_PATH.stat().st_size
    print(f"Built {ZIP_PATH.name}: {n_files} file(s), {zip_size} bytes")

    auth = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    status, created = _request(
        f"{control}/v1/{app_id}/frontend/deployments",
        method="POST",
        data=json.dumps({"framework": "static"}).encode("utf-8"),
        headers=auth,
    )
    if status not in (200, 201):
        raise SystemExit(f"create deployment failed ({status}): {created}")

    deployment_id = _pick(created, "id", "deployment_id", "deploymentId")
    upload_url = _pick(created, "uploadUrl", "upload_url")
    if not deployment_id or not upload_url:
        raise SystemExit(f"unexpected create response: {created}")

    print(f"Deployment {deployment_id} — uploading zip…")
    zip_bytes = ZIP_PATH.read_bytes()
    up_status, up_body = _request(
        upload_url,
        method="PUT",
        data=zip_bytes,
        headers={"Content-Type": "application/zip"},
        timeout=180,
    )
    if up_status not in (200, 201, 204):
        raise SystemExit(f"upload failed ({up_status}): {up_body}")

    print("Upload OK — starting build…")
    status, started = _request(
        f"{control}/v1/{app_id}/frontend/deployments/{deployment_id}/start",
        method="POST",
        data=b"{}",
        headers=auth,
    )
    if status not in (200, 201, 202):
        raise SystemExit(f"start failed ({status}): {started}")

    live_url = _pick(started, "url", "deploymentUrl", "deployment_url")
    dep_status = _pick(started, "status") or "BUILDING"
    print(f"Start response: status={dep_status} url={live_url}")

    deadline = time.time() + 300
    while time.time() < deadline:
        time.sleep(5)
        status, detail = _request(
            f"{control}/v1/{app_id}/frontend/deployments/{deployment_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if status != 200:
            print(f"poll HTTP {status}: {detail}")
            continue
        dep_status = _pick(detail, "status") or "unknown"
        live_url = _pick(detail, "url", "deploymentUrl", "deployment_url") or live_url
        print(f"  status={dep_status} url={live_url}")
        if dep_status == "READY":
            break
        if dep_status in ("ERROR", "CANCELED"):
            raise SystemExit(f"deployment {dep_status}: {detail}")
    else:
        raise SystemExit("timed out waiting for READY")

    site_url = live_url or f"https://rock-n-roll.butterbase.dev"
    marker = "Mineral ID agent"
    verify_deadline = time.time() + 180
    while time.time() < verify_deadline:
        try:
            req = urllib.request.Request(site_url, headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            if marker in html and "cameraPanel" in html:
                print(f"Verified live site: {site_url}")
                print("  contains dual-panel UI markers")
                return
            print("  site reachable but old content — waiting for CDN…")
        except Exception as exc:  # noqa: BLE001
            print(f"  verify fetch: {exc}")
        time.sleep(10)

    print(f"Deployed (READY) at {site_url} — CDN may still be propagating.")
    print(f"Hard-refresh and look for '{marker}' in the page source if UI looks old.")


if __name__ == "__main__":
    main()
