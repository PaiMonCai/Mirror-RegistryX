"""Credential, registry, and mirror group API routes."""

from . import legacy
from .route_utils import legacy_router, path_in_prefixes

credential_row = legacy.credential_row
mirror_credential_references = legacy.mirror_credential_references
public_credential = legacy.public_credential
require_credentials_secret = legacy.require_credentials_secret
credential_fernet = legacy.credential_fernet
encrypt_secret = legacy.encrypt_secret
decrypt_secret = legacy.decrypt_secret
credential_rows = legacy.credential_rows
credential_allows = legacy.credential_allows

router = legacy_router(
    "credentials",
    lambda path: path_in_prefixes(path, ["/api/credentials", "/api/registries", "/api/mirror-groups"]),
)
