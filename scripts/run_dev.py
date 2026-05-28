"""开发环境启动脚本。

用法:
    python scripts/run_dev.py              # HTTP (127.0.0.1:18080)
    python scripts/run_dev.py --https       # HTTPS (0.0.0.0:18080)，首次自动生成证书
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _ensure_cert() -> tuple[Path, Path]:
    cert_dir = Path(__file__).resolve().parent.parent / ".scratch"
    cert_dir.mkdir(exist_ok=True)
    key_file = cert_dir / "dev-key.pem"
    cert_file = cert_dir / "dev-cert.pem"
    if not key_file.exists() or not cert_file.exists():
        print("[run_dev] generating self-signed cert for HTTPS ...")
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(key_file), "-out", str(cert_file),
                "-days", "365", "-nodes",
                "-subj", "/CN=meet-transcribe-dev",
            ],
            check=True, capture_output=True,
        )
        print(f"[run_dev] cert: {cert_file}")
    return key_file, cert_file


def main() -> int:
    https_mode = "--https" in sys.argv

    if https_mode:
        key_file, cert_file = _ensure_cert()
        os.environ["MT_DEV_HTTPS_KEY"] = str(key_file)
        os.environ["MT_DEV_HTTPS_CERT"] = str(cert_file)
        os.environ.setdefault("MT_SERVER_HOST", "0.0.0.0")

    from meet_transcribe.api.app import run_cli

    return run_cli()


if __name__ == "__main__":
    sys.exit(main())
