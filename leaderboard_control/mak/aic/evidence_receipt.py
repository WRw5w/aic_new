"""Fail-closed atomic JSON receipts with post-close SHA-256 sidecars."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SELF_HASH_KEYS = {"receipt_sha256", "terminal_receipt_sha256", "self_sha256"}


class EvidenceReceiptError(ValueError):
    """Raised when an evidence receipt or its bound files is invalid."""


@dataclass(frozen=True)
class EvidenceValidationSummary:
    receipt_path: str
    sidecar_path: str
    receipt_sha256: str
    hash_field_count: int
    bound_file_count: int
    sidecar_valid: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "receipt_path": self.receipt_path,
            "sidecar_path": self.sidecar_path,
            "receipt_sha256": self.receipt_sha256,
            "hash_field_count": self.hash_field_count,
            "bound_file_count": self.bound_file_count,
            "sidecar_valid": self.sidecar_valid,
        }


def sidecar_path_for(receipt_path: str | Path) -> Path:
    """Return ``foo/receipt.sha256`` for ``foo/receipt.json``."""

    path = Path(receipt_path)
    return path.with_suffix(".sha256") if path.suffix else Path(str(path) + ".sha256")


def write_evidence_receipt(
    receipt_path: str | Path,
    payload: dict[str, Any],
    *,
    root: str | Path | None = None,
    verify_bound_files: bool = True,
) -> EvidenceValidationSummary:
    """Atomically write JSON, then write and validate its post-close sidecar."""

    path = Path(receipt_path).resolve()
    root_path = _resolved_root(root)
    _require_within_root(path, root_path, "receipt path")
    if not isinstance(payload, dict):
        raise EvidenceReceiptError("receipt payload must be a JSON object")
    _validate_payload(payload, path, root_path, verify_bound_files=verify_bound_files)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    _atomic_text_write(path, encoded, encoding="utf-8")
    digest = _sha256(path)
    sidecar = sidecar_path_for(path)
    _atomic_text_write(sidecar, digest + "\n", encoding="ascii")
    summary = _validate_written(path, sidecar, root_path, verify_bound_files=verify_bound_files)
    return summary


def read_evidence_receipt(
    receipt_path: str | Path,
    *,
    root: str | Path | None = None,
    require_sidecar: bool = True,
    verify_bound_files: bool = True,
) -> tuple[dict[str, Any], EvidenceValidationSummary]:
    """Read and validate an evidence receipt and, by default, its sidecar."""

    path = Path(receipt_path).resolve()
    root_path = _resolved_root(root)
    _require_within_root(path, root_path, "receipt path")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceReceiptError(f"cannot read JSON receipt: {path}") from exc
    if not isinstance(payload, dict):
        raise EvidenceReceiptError("receipt JSON must be an object")
    _validate_payload(payload, path, root_path, verify_bound_files=verify_bound_files)
    sidecar = sidecar_path_for(path)
    if require_sidecar:
        return payload, _validate_written(path, sidecar, root_path, verify_bound_files=verify_bound_files)
    digest = _sha256(path)
    return payload, EvidenceValidationSummary(
        str(path), str(sidecar), digest, *_counts(payload, path, root_path, verify_bound_files=verify_bound_files), False
    )


def validate_evidence_receipt(
    receipt_path: str | Path,
    *,
    root: str | Path | None = None,
    require_sidecar: bool = True,
    verify_bound_files: bool = True,
) -> EvidenceValidationSummary:
    """Validate one receipt and return a compact typed summary."""

    _, summary = read_evidence_receipt(
        receipt_path, root=root, require_sidecar=require_sidecar, verify_bound_files=verify_bound_files
    )
    return summary


def _validate_written(
    path: Path, sidecar: Path, root: str | Path | None, *, verify_bound_files: bool
) -> EvidenceValidationSummary:
    if not sidecar.is_file():
        raise EvidenceReceiptError(f"receipt sidecar is missing: {sidecar}")
    try:
        sidecar_text = sidecar.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise EvidenceReceiptError(f"cannot read receipt sidecar: {sidecar}") from exc
    digest = _sha256(path)
    if sidecar_text != digest + "\n":
        raise EvidenceReceiptError(f"stale or malformed sidecar: {sidecar}")
    payload = _load_json(path)
    _validate_payload(payload, path, root, verify_bound_files=verify_bound_files)
    hash_count, bound_count = _counts(payload, path, root, verify_bound_files=verify_bound_files)
    return EvidenceValidationSummary(str(path), str(sidecar), digest, hash_count, bound_count, True)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceReceiptError(f"cannot parse receipt JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise EvidenceReceiptError("receipt JSON must be an object")
    return payload


def _validate_payload(
    payload: dict[str, Any], receipt_path: Path, root: str | Path | None, *, verify_bound_files: bool
) -> None:
    _counts(payload, receipt_path, root, verify_bound_files=verify_bound_files)


def _counts(
    payload: dict[str, Any],
    receipt_path: Path,
    root: str | Path | None,
    *,
    verify_bound_files: bool,
) -> tuple[int, int]:
    root_path = _resolved_root(root)
    hash_count = 0
    bound_files: set[Path] = set()
    digest_cache: dict[Path, str] = {}

    def visit(value: Any) -> None:
        nonlocal hash_count
        if isinstance(value, dict):
            hash_items = {key: item for key, item in value.items() if _is_hash_key(key)}
            for key, digest in hash_items.items():
                _validate_digest(digest, key)

            bindings: list[tuple[str, str]] = []
            if "path" in value and "sha256" in hash_items:
                bindings.append(("path", "sha256"))
            for path_key in value:
                if isinstance(path_key, str) and path_key.endswith("_path"):
                    hash_key = path_key[:-5] + "_sha256"
                    if hash_key in hash_items:
                        bindings.append((path_key, hash_key))

            paired_hash_keys = {hash_key for _, hash_key in bindings}
            for path_key, hash_key in bindings:
                bound_path = _resolve_bound_path(value[path_key], root_path)
                if bound_path == receipt_path:
                    raise EvidenceReceiptError("receipt cannot bind its own path/hash")
                if verify_bound_files:
                    actual = digest_cache.get(bound_path)
                    if actual is None:
                        actual = _sha256(bound_path)
                        digest_cache[bound_path] = actual
                    if actual != hash_items[hash_key]:
                        raise EvidenceReceiptError(f"bound file hash mismatch: {bound_path}")
                bound_files.add(bound_path)

            unpaired_self_hashes = SELF_HASH_KEYS.intersection(hash_items).difference(paired_hash_keys)
            if unpaired_self_hashes:
                raise EvidenceReceiptError("unpaired self-hash field inside receipt is forbidden")

            hash_count += len(hash_items)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return hash_count, len(bound_files)


def _is_hash_key(key: Any) -> bool:
    return isinstance(key, str) and (key == "sha256" or key.endswith("_sha256"))


def _validate_digest(value: Any, key: str) -> None:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise EvidenceReceiptError(f"{key} must be lowercase 64-hex SHA-256")


def _resolve_bound_path(raw: Any, root: Path | None) -> Path:
    if not isinstance(raw, str) or not raw:
        raise EvidenceReceiptError("bound path must be a non-empty string")
    candidate = Path(raw)
    if not candidate.is_absolute():
        if root is None:
            raise EvidenceReceiptError("relative bound path requires an explicit root")
        candidate = (root / candidate).resolve()
    else:
        candidate = candidate.resolve()
    _require_within_root(candidate, root, "bound path")
    if not candidate.is_file():
        raise EvidenceReceiptError(f"bound path is missing or not a file: {candidate}")
    return candidate


def _resolved_root(root: str | Path | None) -> Path | None:
    return Path(root).resolve() if root is not None else None


def _require_within_root(path: Path, root: Path | None, label: str) -> None:
    if root is not None and path != root and root not in path.parents:
        raise EvidenceReceiptError(f"{label} escapes explicit root")


def _atomic_text_write(path: Path, text: str, *, encoding: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding=encoding, dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise EvidenceReceiptError(f"atomic write failed: {path}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise EvidenceReceiptError(f"cannot hash file: {path}") from exc
