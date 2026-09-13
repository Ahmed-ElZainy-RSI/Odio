"""Exception handlers producing the single error envelope."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ai_engine.domain.errors import AiEngineError, ErrorCode
from ai_engine.observability.context import current_context
from ai_engine.observability.logging import get_logger

log = get_logger(__name__)


def _envelope(
    code: ErrorCode, message: str, details: dict | None = None, request_id: str | None = None
) -> dict:
    return {
        "error": {
            "code": code.value,
            "message": message,
            "details": details or {},
            "requestId": request_id,
        }
    }


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or current_context().get("request_id")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AiEngineError)
    async def _domain_error(request: Request, exc: AiEngineError) -> JSONResponse:
        code = exc.reason.code
        log.warning(
            "request.failed",
            error_code=code.value,
            detail=exc.reason.detail,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=code.http_status,
            content=_envelope(
                code, exc.reason.detail or code.safe_message, request_id=_request_id(request)
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Surface which field failed, without echoing the submitted value back -- the body
        # can contain a presigned URL.
        details = {
            "fields": [
                {
                    "loc": [str(part) for part in error.get("loc", [])],
                    "msg": error.get("msg", ""),
                }
                for error in exc.errors()
            ][:10]
        }
        code = ErrorCode.VALIDATION_ERROR
        log.warning("request.invalid", path=request.url.path, field_count=len(exc.errors()))
        return JSONResponse(
            status_code=code.http_status,
            content=_envelope(code, code.safe_message, details, request_id=_request_id(request)),
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        code = ErrorCode.INTERNAL_ERROR
        # The stack trace goes to our logs, keyed by request id -- never to the caller.
        log.error(
            "request.unhandled",
            path=request.url.path,
            exc_type=type(exc).__name__,
            exc_info=exc,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope(code, code.safe_message, request_id=_request_id(request)),
        )
