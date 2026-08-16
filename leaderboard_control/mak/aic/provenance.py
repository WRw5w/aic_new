from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any


REQUIRED_BACKBONE = "CLIP ViT-B/32"
REQUIRED_MODEL_ID = "vit_base_patch32_clip_224.openai"
PROVENANCE_SCHEMA_VERSION = 2
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ARTIFACT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TEXT_EVIDENCE_SUFFIXES = {".json", ".log", ".md", ".py", ".txt", ".yaml", ".yml"}
PROVENANCE_MANIFEST_MAX_BYTES = 1024 * 1024
PROVENANCE_EVIDENCE_TEXT_MAX_BYTES = 8 * 1024 * 1024


class ProvenanceError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_string(doc: dict[str, Any], key: str) -> str:
    value = doc.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProvenanceError(f"PROVENANCE_FIELD_REQUIRED: {key}")
    return value.strip()


def _is_link_or_reparse(result: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(result, "st_file_attributes", 0)
    return stat.S_ISLNK(result.st_mode) or bool(attributes & reparse_flag)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _resolve_authorized_roots(authorized_roots: tuple[str | Path, ...] | list[str | Path] | None) -> tuple[Path, ...]:
    if not authorized_roots:
        raise ProvenanceError("PROVENANCE_AUTHORIZED_ROOTS_REQUIRED")
    roots: list[Path] = []
    for value in authorized_roots:
        raw = Path(value).expanduser().absolute()
        try:
            root_stat = os.lstat(raw)
        except OSError as exc:
            raise ProvenanceError(f"PROVENANCE_AUTHORIZED_ROOT_UNAVAILABLE: {raw}") from exc
        if _is_link_or_reparse(root_stat):
            raise ProvenanceError(f"PROVENANCE_REPARSE_POINT_FORBIDDEN: authorized_root={raw}")
        if not stat.S_ISDIR(root_stat.st_mode):
            raise ProvenanceError(f"PROVENANCE_AUTHORIZED_ROOT_NOT_DIRECTORY: {raw}")
        roots.append(raw.resolve(strict=True))
    return tuple(roots)


def _resolve_file(
    raw: str,
    base_dir: Path,
    field: str,
    authorized_roots: tuple[Path, ...],
) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    path = path.absolute()
    if not any(_is_within(path, root) for root in authorized_roots):
        raise ProvenanceError(f"PROVENANCE_PATH_OUTSIDE_AUTHORIZED_ROOTS: {field}={path}")
    for component in (path, *path.parents):
        if component in authorized_roots:
            break
        try:
            component_stat = os.lstat(component)
        except OSError as exc:
            raise ProvenanceError(f"PROVENANCE_FILE_NOT_FOUND: {field}={path}") from exc
        if _is_link_or_reparse(component_stat):
            raise ProvenanceError(f"PROVENANCE_REPARSE_POINT_FORBIDDEN: {field}={component}")
    try:
        file_stat = os.lstat(path)
    except OSError as exc:
        raise ProvenanceError(f"PROVENANCE_FILE_NOT_FOUND: {field}={path}") from exc
    if _is_link_or_reparse(file_stat):
        raise ProvenanceError(f"PROVENANCE_REPARSE_POINT_FORBIDDEN: {field}={path}")
    if not stat.S_ISREG(file_stat.st_mode):
        raise ProvenanceError(f"PROVENANCE_FILE_NOT_FOUND: {field}={path}")
    resolved = path.resolve(strict=True)
    if not any(_is_within(resolved, root) for root in authorized_roots):
        raise ProvenanceError(f"PROVENANCE_PATH_OUTSIDE_AUTHORIZED_ROOTS: {field}={path}")
    return resolved


def _artifact_id(binding: dict[str, Any], field: str) -> str:
    value = _required_string(binding, "artifact_id")
    if not ARTIFACT_ID_PATTERN.fullmatch(value):
        raise ProvenanceError(f"PROVENANCE_ARTIFACT_ID_INVALID: {field}={value}")
    return value


def _bound_file(
    binding: object,
    base_dir: Path,
    field: str,
    authorized_roots: tuple[Path, ...],
) -> tuple[str, Path, str]:
    if not isinstance(binding, dict):
        raise ProvenanceError(f"PROVENANCE_BINDING_INVALID: {field}")
    artifact_id = _artifact_id(binding, field)
    file_path = _resolve_file(
        _required_string(binding, "path"),
        base_dir,
        f"{field}.path",
        authorized_roots,
    )
    expected_sha256 = _required_string(binding, "sha256").lower()
    if not SHA256_PATTERN.fullmatch(expected_sha256):
        raise ProvenanceError(f"PROVENANCE_ARTIFACT_SHA256_INVALID: {field}")
    actual_sha256 = sha256_file(file_path)
    if actual_sha256 != expected_sha256:
        raise ProvenanceError(
            f"PROVENANCE_ARTIFACT_SHA256_MISMATCH: {field} "
            f"expected={expected_sha256} actual={actual_sha256}"
        )
    return artifact_id, file_path, actual_sha256


def validate_aic_provenance_manifest(
    manifest_path: str | Path,
    *,
    authorized_roots: tuple[str | Path, ...] | list[str | Path] | None = None,
) -> dict[str, Any]:
    if authorized_roots is None:
        authorized_roots = (Path(manifest_path).expanduser().absolute().parent,)
    roots = _resolve_authorized_roots(authorized_roots)
    path = _resolve_file(str(manifest_path), Path.cwd(), "manifest", roots)
    if path.stat().st_size > PROVENANCE_MANIFEST_MAX_BYTES:
        raise ProvenanceError("PROVENANCE_MANIFEST_TOO_LARGE")

    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"PROVENANCE_MANIFEST_INVALID: {exc}") from exc
    if not isinstance(doc, dict):
        raise ProvenanceError("PROVENANCE_MANIFEST_MUST_BE_OBJECT")
    if doc.get("schema_version") != PROVENANCE_SCHEMA_VERSION:
        raise ProvenanceError("PROVENANCE_SCHEMA_VERSION_UNSUPPORTED")

    experiment_id = _required_string(doc, "experiment_id")
    backbone = _required_string(doc, "backbone")
    model_id = _required_string(doc, "model_id")
    if backbone != REQUIRED_BACKBONE:
        raise ProvenanceError(f"PROVENANCE_BACKBONE_NOT_ALLOWED: {backbone}")
    if model_id != REQUIRED_MODEL_ID:
        raise ProvenanceError(f"PROVENANCE_MODEL_ID_NOT_ALLOWED: {model_id}")

    candidate = _resolve_file(
        _required_string(doc, "candidate_zip"),
        path.parent,
        "candidate_zip",
        roots,
    )
    if candidate.suffix.lower() != ".zip":
        raise ProvenanceError(f"PROVENANCE_CANDIDATE_NOT_ZIP: {candidate}")
    expected_sha256 = _required_string(doc, "candidate_sha256").lower()
    if not SHA256_PATTERN.fullmatch(expected_sha256):
        raise ProvenanceError("PROVENANCE_CANDIDATE_SHA256_INVALID")
    actual_sha256 = sha256_file(candidate)
    if actual_sha256 != expected_sha256:
        raise ProvenanceError(
            f"PROVENANCE_CANDIDATE_SHA256_MISMATCH: expected={expected_sha256} actual={actual_sha256}"
        )
    candidate_artifact_id = _required_string(doc, "candidate_artifact_id")
    if not ARTIFACT_ID_PATTERN.fullmatch(candidate_artifact_id):
        raise ProvenanceError("PROVENANCE_CANDIDATE_ARTIFACT_ID_INVALID")

    evidence_values = doc.get("evidence_files")
    if not isinstance(evidence_values, list) or not evidence_values:
        raise ProvenanceError("PROVENANCE_EVIDENCE_REQUIRED")
    evidence_files: list[Path] = []
    artifact_bindings = {candidate_artifact_id: (candidate, actual_sha256)}
    model_id_evidence = False
    for index, value in enumerate(evidence_values):
        artifact_id, evidence, _digest_value = _bound_file(
            value,
            path.parent,
            f"evidence_files[{index}]",
            roots,
        )
        if artifact_id in artifact_bindings:
            raise ProvenanceError(f"PROVENANCE_ARTIFACT_ID_DUPLICATE: {artifact_id}")
        artifact_bindings[artifact_id] = (evidence, _digest_value)
        if evidence == path:
            raise ProvenanceError("PROVENANCE_MANIFEST_CANNOT_SELF_ATTEST")
        evidence_files.append(evidence)
        if evidence.suffix.lower() in TEXT_EVIDENCE_SUFFIXES:
            if evidence.stat().st_size > PROVENANCE_EVIDENCE_TEXT_MAX_BYTES:
                raise ProvenanceError(
                    f"PROVENANCE_EVIDENCE_TEXT_TOO_LARGE: evidence_files[{index}]"
                )
            text = evidence.read_text(encoding="utf-8", errors="replace")
            model_id_evidence = model_id_evidence or REQUIRED_MODEL_ID in text
    if not model_id_evidence:
        raise ProvenanceError("PROVENANCE_MODEL_ID_NOT_FOUND_IN_EVIDENCE")

    parents = doc.get("parent_artifacts", [])
    if not isinstance(parents, list):
        raise ProvenanceError("PROVENANCE_PARENT_ARTIFACTS_MUST_BE_ARRAY")
    for index, parent in enumerate(parents):
        if not isinstance(parent, dict):
            raise ProvenanceError(f"PROVENANCE_PARENT_INVALID: index={index}")
        artifact_id, parent_path, _parent_digest = _bound_file(
            parent,
            path.parent,
            f"parent_artifacts[{index}]",
            roots,
        )
        existing_binding = artifact_bindings.get(artifact_id)
        if existing_binding is not None and existing_binding != (
            parent_path,
            _parent_digest,
        ):
            raise ProvenanceError(f"PROVENANCE_ARTIFACT_ID_CONFLICT: {artifact_id}")
        artifact_bindings[artifact_id] = (parent_path, _parent_digest)
        if parent_path == path:
            raise ProvenanceError("PROVENANCE_MANIFEST_CANNOT_SELF_ATTEST")
        parent_model_id = _required_string(parent, "model_id")
        if parent_model_id != REQUIRED_MODEL_ID:
            raise ProvenanceError(
                f"PROVENANCE_PARENT_MODEL_ID_NOT_ALLOWED: index={index} model_id={parent_model_id}"
            )

    return {
        "ok": True,
        "manifest": str(path),
        "experiment_id": experiment_id,
        "candidate_zip": str(candidate),
        "candidate_artifact_id": candidate_artifact_id,
        "candidate_sha256": actual_sha256,
        "backbone": backbone,
        "model_id": model_id,
        "evidence_files": [str(item) for item in evidence_files],
        "parent_artifacts": len(parents),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate AIC ViT-B/32 provenance evidence.")
    parser.add_argument("manifest", help="Path to a provenance manifest JSON file.")
    args = parser.parse_args()
    try:
        result = validate_aic_provenance_manifest(args.manifest)
    except ProvenanceError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
