"""Deploy backend/functions/*.ts to Butterbase. Reads credentials from .env."""

import json
import pathlib
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
FUNCTIONS_DIR = ROOT / "backend" / "functions"


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


def main() -> None:
    env = load_env()
    app_id = env["BUTTERBASE_APP_ID"]
    api_key = env["BUTTERBASE_API_KEY"]
    app_url = env.get("BUTTERBASE_API_URL", f"https://api.butterbase.ai/v1/{app_id}")
    control = app_url.split("/v1/")[0] if "/v1/" in app_url else "https://api.butterbase.ai"

    shared_env = {
        "BUTTERBASE_API_KEY": api_key,
        "CONFIDENCE_STOP_THRESHOLD": env.get("CONFIDENCE_STOP_THRESHOLD", "0.5"),
        "CONFIDENCE_ANALYSIS_MIN": env.get("CONFIDENCE_ANALYSIS_MIN", "0.5"),
        "CONFIDENCE_ANALYSIS_MAX": env.get("CONFIDENCE_ANALYSIS_MAX", "0.7"),
        "BUTTERBASE_VISION_MODEL": env.get(
            "BUTTERBASE_VISION_MODEL", "anthropic/claude-haiku-4.5"
        ),
    }

    for path in sorted(FUNCTIONS_DIR.glob("*.ts")):
        name = path.stem
        code = path.read_text(encoding="utf-8")
        method = "GET" if name == "scan-status" else "POST"
        body = {
            "name": name,
            "code": code,
            "description": f"Rock scan pipeline: {name}",
            "envVars": shared_env,
            "trigger": {"type": "http", "config": {"method": method, "auth": "none"}},
        }
        req = urllib.request.Request(
            f"{control}/v1/{app_id}/functions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = resp.read().decode("utf-8")
                print(f"{name}: OK ({resp.status}) {payload[:160]}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            print(f"{name}: HTTP {exc.code} {detail[:400]}")


if __name__ == "__main__":
    main()
