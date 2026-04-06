import argparse
import atexit
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT_DIR / "frontend"
BACKEND_APP = "backend.app.main:app"

BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8000
FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT = 5173


def _venv_python() -> str:
    windows_python = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
    if windows_python.exists():
        return str(windows_python)
    return sys.executable


def _http_ready(url: str, timeout_seconds: float) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return True
        except urllib.error.URLError:
            time.sleep(0.4)
        except Exception:
            time.sleep(0.4)
    return False


def _stop_process(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None:
        return

    # On Windows, force-kill process tree to avoid leftover child processes (npm/node).
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return

    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start TouristAgent web app (backend + frontend).")
    parser.add_argument("--backend-port", type=int, default=BACKEND_PORT)
    parser.add_argument("--frontend-port", type=int, default=FRONTEND_PORT)
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open browser.")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Start services, verify health pages, then exit automatically.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    python_bin = _venv_python()
    npm_cmd = shutil.which("npm")
    node_cmd = shutil.which("node")
    vite_bin = FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"

    if npm_cmd is None and node_cmd is None:
        raise RuntimeError("Node.js/npm not found. Please install Node.js and ensure PATH is configured.")
    if not FRONTEND_DIR.exists():
        raise RuntimeError("frontend directory not found. Please check project structure.")

    backend_url = f"http://{BACKEND_HOST}:{args.backend_port}"
    frontend_url = f"http://{FRONTEND_HOST}:{args.frontend_port}"

    backend_proc: subprocess.Popen[bytes] | None = None
    frontend_proc: subprocess.Popen[bytes] | None = None

    try:
        print("[1/4] Starting backend API service...")
        backend_proc = subprocess.Popen(
            [
                python_bin,
                "-m",
                "uvicorn",
                BACKEND_APP,
                "--host",
                BACKEND_HOST,
                "--port",
                str(args.backend_port),
            ],
            cwd=str(ROOT_DIR),
        )

        print("[2/4] Waiting for backend health endpoint...")
        if not _http_ready(f"{backend_url}/health", timeout_seconds=30):
            raise RuntimeError("Backend failed to start in time.")

        print("[3/4] Starting frontend dev server...")
        if vite_bin.exists() and node_cmd is not None:
            frontend_cmd = [
                node_cmd,
                str(vite_bin),
                "--host",
                FRONTEND_HOST,
                "--port",
                str(args.frontend_port),
            ]
        else:
            if npm_cmd is None:
                raise RuntimeError("npm not found and local vite binary is missing.")
            frontend_cmd = [
                npm_cmd,
                "run",
                "dev",
                "--",
                "--host",
                FRONTEND_HOST,
                "--port",
                str(args.frontend_port),
            ]
        frontend_proc = subprocess.Popen(frontend_cmd, cwd=str(FRONTEND_DIR))

        print("[4/4] Waiting for frontend page...")
        if not _http_ready(frontend_url, timeout_seconds=45):
            raise RuntimeError("Frontend failed to start in time.")

        print("\nTouristAgent is running.")
        print(f"Frontend: {frontend_url}")
        print(f"Backend : {backend_url}/docs")

        if not args.no_browser:
            webbrowser.open(frontend_url)

        if args.smoke_test:
            print("Smoke test passed: backend and frontend are reachable.")
            return

        print("Press Ctrl+C to stop services.")
        while True:
            if backend_proc.poll() is not None:
                raise RuntimeError("Backend process exited unexpectedly.")
            if frontend_proc.poll() is not None:
                raise RuntimeError("Frontend process exited unexpectedly.")
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping services...")
    finally:
        _stop_process(frontend_proc)
        _stop_process(backend_proc)


if __name__ == "__main__":
    # Ensure cleanup on interpreter shutdown.
    atexit.register(lambda: None)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    main()
