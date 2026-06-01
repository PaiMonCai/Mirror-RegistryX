"""Utilities for exposing routes while the monolithic app is being split."""

from collections.abc import Callable, Iterable

from fastapi import APIRouter
from fastapi.routing import APIRoute

from . import legacy

RoutePredicate = Callable[[str], bool]


def legacy_router(name: str, predicate: RoutePredicate) -> APIRouter:
    """Build an APIRouter from routes still implemented in panel.legacy.

    This keeps URL paths and endpoint call signatures unchanged while route
    registration moves out of app.py by domain. Business logic can then be
    migrated from legacy.py into the domain modules incrementally.
    """
    router = APIRouter()
    for route in legacy.app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not predicate(route.path):
            continue
        router.add_api_route(
            route.path,
            route.endpoint,
            response_model=route.response_model,
            status_code=route.status_code,
            tags=route.tags,
            dependencies=route.dependencies,
            summary=route.summary,
            description=route.description,
            response_description=route.response_description,
            responses=route.responses,
            deprecated=route.deprecated,
            methods=route.methods,
            operation_id=route.operation_id,
            response_model_include=route.response_model_include,
            response_model_exclude=route.response_model_exclude,
            response_model_by_alias=route.response_model_by_alias,
            response_model_exclude_unset=route.response_model_exclude_unset,
            response_model_exclude_defaults=route.response_model_exclude_defaults,
            response_model_exclude_none=route.response_model_exclude_none,
            include_in_schema=route.include_in_schema,
            response_class=route.response_class,
            name=route.name,
            callbacks=route.callbacks,
            openapi_extra=route.openapi_extra,
        )
    return router


def path_in_prefixes(path: str, prefixes: Iterable[str]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)
