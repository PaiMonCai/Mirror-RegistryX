"""FastAPI application assembly for the Mirror Registry panel."""

from contextlib import asynccontextmanager
import importlib
import sys
import types

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from .auth import ensure_admin_user, require_api_auth, router as auth_router
from .errors import http_exception_handler, unhandled_exception_handler, validation_exception_handler
from .queue import router as queue_router

legacy = importlib.reload(importlib.import_module(".legacy", __package__))

# Rebuild domain routers on every panel.app reload. The test suite reloads
# panel.main between cases after changing environment variables; legacy.py
# reads those variables at import time, so routers must point at the freshly
# reloaded legacy endpoints.
_credentials = importlib.reload(importlib.import_module(".credentials", __package__))
_mirrors = importlib.reload(importlib.import_module(".mirrors", __package__))
_ops = importlib.reload(importlib.import_module(".ops", __package__))
_ops_agent = importlib.reload(importlib.import_module(".ops_agent", __package__))
_storage = importlib.reload(importlib.import_module(".storage", __package__))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    legacy.load_config()
    ensure_admin_user()
    yield


app = FastAPI(title="Mirror Registry Panel", lifespan=lifespan)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)
app.middleware("http")(require_api_auth)

app.include_router(auth_router)
app.include_router(queue_router)
app.include_router(_ops_agent.router)
app.include_router(_ops.router)
app.include_router(_mirrors.router)
app.include_router(_storage.router)
app.include_router(_credentials.router)

app.mount("/", StaticFiles(directory=legacy.STATIC_DIR, html=True), name="static")

# Backward-compatible exports for tests and operational scripts that import
# helpers from panel.main/panel.app directly. Mutating one of these attributes
# also updates panel.legacy, so monkeypatching keeps affecting route handlers.
for _name in dir(legacy):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(legacy, _name)


class _AppModule(types.ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name == "app" or name.startswith("__"):
            return
        if hasattr(legacy, name):
            setattr(legacy, name, value)


sys.modules[__name__].__class__ = _AppModule
