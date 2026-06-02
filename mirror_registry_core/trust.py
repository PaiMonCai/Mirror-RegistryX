"""Release, scan, and trust helpers for mirrored images."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from mirror_registry_core.mirror_rules import image_repo_tag


SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
TRUST_ALLOWED_FOR_PROMOTION = {"trusted", "warning", "bypassed"}
TRUST_BLOCKED_FOR_PROMOTION = {"blocked", "scan_failed", "unknown", "scanning"}


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def release_id() -> str:
    return f"rel-{uuid.uuid4().hex[:20]}"


def image_ref_for_digest(image: str, digest: str) -> str:
    clean_digest = str(digest or "").strip()
    if not clean_digest:
        return image
    if "@" in image:
        return image
    name = image.rsplit(":", 1)[0] if ":" in image.rsplit("/", 1)[-1] else image
    return f"{name}@{clean_digest}"


def target_repo_tag(target_image: str) -> tuple[str, str]:
    return image_repo_tag(target_image)


def safe_release_artifact_dir(root: Path, release_id_value: str) -> Path:
    if not re.fullmatch(r"rel-[A-Za-z0-9_.-]{1,80}", release_id_value):
        raise ValueError("invalid release id")
    return root / "releases" / release_id_value


def build_trivy_scan_command(image_ref: str, output_path: Path) -> list[str]:
    return ["trivy", "image", "--format", "json", "--output", str(output_path), image_ref]


def build_trivy_sbom_command(image_ref: str, output_path: Path) -> list[str]:
    return ["trivy", "image", "--format", "cyclonedx", "--output", str(output_path), image_ref]


def parse_trivy_summary(report: dict[str, Any]) -> dict[str, int]:
    counts = {severity.lower(): 0 for severity in SEVERITIES}
    for result in report.get("Results") or []:
        if not isinstance(result, dict):
            continue
        for finding in result.get("Vulnerabilities") or []:
            if not isinstance(finding, dict):
                continue
            severity = str(finding.get("Severity") or "UNKNOWN").upper()
            if severity not in SEVERITIES:
                severity = "UNKNOWN"
            counts[severity.lower()] += 1
    return counts


def compute_trust_status(scan_status: str, counts: dict[str, int], bypassed: bool = False) -> str:
    if bypassed:
        return "bypassed"
    if scan_status == "failed":
        return "scan_failed"
    if scan_status in {"queued", "running"}:
        return "scanning"
    if scan_status != "succeeded":
        return "unknown"
    if int(counts.get("critical") or 0) > 0:
        return "blocked"
    if int(counts.get("high") or 0) > 0:
        return "warning"
    return "trusted"
