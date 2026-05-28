"""错误码体系。docs/protocol.md 第 3 节锁定。

错误消息不暴露 traceback / SQL / 内部路径。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

ERROR_CODES = {
    "AUTH_FAIL": status.HTTP_401_UNAUTHORIZED,
    "RATE_LIMITED": status.HTTP_429_TOO_MANY_REQUESTS,
    "AUDIO_FORMAT_INVALID": status.HTTP_400_BAD_REQUEST,
    "ENGINE_TIMEOUT": status.HTTP_504_GATEWAY_TIMEOUT,
    "QUOTA_EXCEEDED": status.HTTP_402_PAYMENT_REQUIRED,
    "RESUME_REQUIRED": status.HTTP_409_CONFLICT,
    "INTERNAL": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "VALIDATION_FAILED": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "NOT_FOUND": status.HTTP_404_NOT_FOUND,
}


class APIError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        if code not in ERROR_CODES:
            code = "INTERNAL"
        self.code = code
        self.message = message or code
        super().__init__(self.message)

    @property
    def http_status(self) -> int:
        return ERROR_CODES[self.code]

    def to_payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def _handle_api_error(_req: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_payload())
