"""meet-transcribe-doctor: 排查脚本。

用法:
    meet-transcribe-doctor              # 人类可读输出
    meet-transcribe-doctor --json       # JSON 输出
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
from typing import Any


def _check_python() -> dict[str, Any]:
    return {
        "name": "python",
        "ok": sys.version_info >= (3, 11) and sys.version_info < (3, 13),
        "detail": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
    }


def _check_os() -> dict[str, Any]:
    return {
        "name": "os",
        "ok": True,
        "detail": {
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
    }


def _check_cuda() -> dict[str, Any]:
    try:
        import torch
    except ImportError:
        return {"name": "cuda", "ok": False, "detail": {"error": "torch not installed"}}
    cuda_available = bool(torch.cuda.is_available())
    detail: dict[str, Any] = {
        "torch_version": torch.__version__,
        "cuda_available": cuda_available,
    }
    if cuda_available:
        props = torch.cuda.get_device_properties(0)
        detail["device_name"] = props.name
        detail["total_mem_mb"] = props.total_memory // (1024 * 1024)
        detail["compute_capability"] = f"{props.major}.{props.minor}"
    return {"name": "cuda", "ok": cuda_available, "detail": detail}


def _check_database() -> dict[str, Any]:
    try:
        from meet_transcribe.config.loader import load_config
    except Exception as exc:
        return {"name": "database", "ok": False, "detail": {"error": f"config: {exc}"}}
    try:
        cfg = load_config()
    except Exception as exc:
        return {"name": "database", "ok": False, "detail": {"error": str(exc)}}

    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        return {"name": "database", "ok": False, "detail": {"error": "sqlalchemy not installed"}}

    pwd = cfg.secrets.db_password.get_secret_value()
    url = cfg.database.url.replace("CHANGE_ME", pwd or "CHANGE_ME")
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            ver = conn.execute(text("SELECT version()")).scalar_one()
            ext = conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname='vector'")
            ).scalar()
        return {
            "name": "database",
            "ok": ext == 1,
            "detail": {"version": ver, "pgvector": ext == 1},
        }
    except Exception as exc:
        return {"name": "database", "ok": False, "detail": {"error": str(exc)}}


def _check_secrets() -> dict[str, Any]:
    required = ["MT_DB_PASSWORD", "MT_SERVER_SECRET", "MT_KMS_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    return {"name": "secrets", "ok": not missing, "detail": {"missing": missing}}


def _check_port(port: int = 8080) -> dict[str, Any]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", port))
        return {"name": "port", "ok": True, "detail": {"port": port}}
    except OSError as exc:
        return {"name": "port", "ok": False, "detail": {"port": port, "error": str(exc)}}
    finally:
        s.close()


def run_all() -> list[dict[str, Any]]:
    return [
        _check_python(),
        _check_os(),
        _check_cuda(),
        _check_secrets(),
        _check_database(),
        _check_port(),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="output JSON")
    args = parser.parse_args()

    results = run_all()
    overall_ok = all(r["ok"] for r in results)

    if args.json:
        print(json.dumps({"ok": overall_ok, "checks": results}, ensure_ascii=False, indent=2))
    else:
        for r in results:
            mark = "OK " if r["ok"] else "FAIL"
            print(f"[{mark}] {r['name']:<10} {r['detail']}")
        print()
        print("OVERALL:", "OK" if overall_ok else "FAIL")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
