"""Consistent API error responses for the panel."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

DEFAULT_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}

DEFAULT_SUGGESTIONS = {
    400: "请检查请求参数后重试。",
    401: "请重新登录。",
    403: "请使用 operator/admin 账号，或确认 API token 具备写权限。",
    404: "请刷新页面后重试，目标资源可能已经被删除。",
    409: "请刷新当前数据，确认资源状态后再操作。",
    422: "请按字段提示修正表单内容后重试。",
    500: "请查看服务日志或导出诊断包定位问题。",
    503: "请检查服务初始化、依赖服务和运行配置。",
}


def api_error_payload(
    status_code: int,
    message: str,
    *,
    code: str | None = None,
    suggestion: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the public error envelope returned by API endpoints."""
    clean_message = str(message or "").strip()
    if not clean_message:
        clean_message = HTTPStatus(status_code).phrase if status_code in HTTPStatus._value2member_map_ else "请求失败"
    return {
        "code": code or DEFAULT_CODES.get(status_code, "API_ERROR"),
        "message": clean_message,
        "suggestion": suggestion or DEFAULT_SUGGESTIONS.get(status_code, "请稍后重试；如果问题持续，请查看诊断信息。"),
        "details": dict(details or {}),
    }


def api_error_response(
    status_code: int,
    message: str,
    *,
    code: str | None = None,
    suggestion: str | None = None,
    details: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        api_error_payload(status_code, message, code=code, suggestion=suggestion, details=details),
        status_code=status_code,
        headers=dict(headers or {}),
    )


async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, Mapping):
        message = str(detail.get("message") or detail.get("detail") or "")
        code = str(detail.get("code") or DEFAULT_CODES.get(exc.status_code, "API_ERROR"))
        suggestion = detail.get("suggestion")
        details = detail.get("details") if isinstance(detail.get("details"), Mapping) else {}
    else:
        message = str(detail or "")
        code = DEFAULT_CODES.get(exc.status_code, "API_ERROR")
        suggestion = None
        details = {}
    return api_error_response(
        exc.status_code,
        message,
        code=code,
        suggestion=str(suggestion) if suggestion else None,
        details=details,
        headers=exc.headers,
    )


async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = []
    for error in exc.errors():
        loc = [str(part) for part in error.get("loc", []) if part not in {"body", "query", "path"}]
        fields.append({
            "field": ".".join(loc) or "request",
            "message": error.get("msg", "字段无效"),
            "type": error.get("type", "validation_error"),
        })
    return api_error_response(
        422,
        "请求参数校验失败",
        code="VALIDATION_ERROR",
        suggestion="请检查高亮字段、必填项和输入长度后重试。",
        details={"fields": fields},
    )


async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    return api_error_response(
        500,
        "服务内部错误",
        code="INTERNAL_ERROR",
        suggestion="请查看 panel 服务日志，或导出诊断包后再排查。",
        details={"error_type": exc.__class__.__name__},
    )
