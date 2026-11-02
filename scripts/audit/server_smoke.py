"""Start the reviewed reference on loopback, check HTTP, then terminate it."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    env = {k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH") if k in os.environ}
    for key, directory in {"TEMP": "tmp", "TMP": "tmp", "HOME": "home", "USERPROFILE": "home",
                           "APPDATA": "home/appdata", "LOCALAPPDATA": "home/localappdata", "MPLCONFIGDIR": "mpl"}.items():
        target = ROOT / ".audit" / directory
        target.mkdir(parents=True, exist_ok=True)
        env[key] = str(target)
    env["MPLBACKEND"] = "Agg"
    command = [sys.executable, "-I", "-B", "-m", "streamlit", "run", "app.py",
               "--server.address=127.0.0.1", "--server.port=18765", "--server.headless=true",
               "--browser.gatherUsageStats=false", "--server.fileWatcherType=none"]
    result = {"command": ["<audit-venv-python>", *command[1:]],
              "health_status": None, "root_status": None,
              "scope": "HTTP startup only; not a browser upload or UI rendering test"}
    with (ROOT / ".audit/server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=ROOT / ".audit/runtime", env=env, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            deadline = time.monotonic() + 25
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    result["startup_exit_code"] = process.returncode
                    break
                try:
                    with opener.open("http://127.0.0.1:18765/_stcore/health", timeout=1) as response:
                        result["health_status"] = response.status
                        result["health_body"] = response.read(100).decode()
                    with opener.open("http://127.0.0.1:18765/", timeout=1) as response:
                        result["root_status"] = response.status
                        result["root_has_html"] = b"<html" in response.read(10000).lower()
                    break
                except Exception as exc:
                    result["last_error"] = f"{type(exc).__name__}: {exc}"
                    time.sleep(0.2)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            result["process_stopped"] = True
    result["passed"] = result["health_status"] == 200 and result["root_status"] == 200 and result.get("root_has_html")
    (ROOT / "docs/audit/evidence/server-smoke.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
