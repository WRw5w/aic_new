import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import threading
import zipfile

import pytest

import mak.mcp.jinyinsai_submit as submit_mcp
from mak.mcp.jinyinsai_submit import (
    JinyinsaiSubmitServer,
    QUEUE_SCHEMA_VERSION,
)


SUBMISSION_CONTRACT_VERSION = "aicomp-submit-agent-contract/v4"


def _server(root: Path) -> JinyinsaiSubmitServer:
    (root / "data" / "test").mkdir(parents=True, exist_ok=True)
    (root / "data" / "test" / "a.jpg").write_text("fake", encoding="utf-8")
    (root / "data" / "train" / "0000").mkdir(parents=True, exist_ok=True)
    (root / "data" / "train" / "0001").mkdir(parents=True, exist_ok=True)
    server = JinyinsaiSubmitServer(
        io.StringIO(),
        io.StringIO(),
        io.StringIO(),
        root=root,
        cdp_probe=lambda: {"ready": True, "reason": "TEST_CDP_READY"},
    )
    server.runner_command = lambda: ["trusted-node", "trusted-runner", "run"]
    server.leaderboard_command = lambda: [
        "trusted-node",
        "trusted-cdp",
        "leaderboard",
    ]
    return server


def _zip(path: Path, body: str = "a.jpg,0000\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("pred_results.csv", body)
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(root: Path, archive: Path, name: str) -> Path:
    evidence = root / f"{name}.log"
    evidence.write_text("loaded model_id=vit_base_patch32_clip_224.openai\n", encoding="utf-8")
    manifest = root / f"{name}.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "experiment_id": name,
                "candidate_zip": str(archive),
                "candidate_artifact_id": "candidate",
                "candidate_sha256": _sha256(archive),
                "backbone": "CLIP ViT-B/32",
                "model_id": "vit_base_patch32_clip_224.openai",
                "evidence_files": [
                    {
                        "artifact_id": "model-evidence",
                        "path": str(evidence),
                        "sha256": _sha256(evidence),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _push(server: JinyinsaiSubmitServer, archive: Path, manifest: Path, **extra):
    return server.queue_push(
        {
            "files": [str(archive)],
            "provenance_manifests": [str(manifest)],
            **extra,
        },
        front=True,
    )


def _seed_legacy_no_dispatch_attempt(
    server: JinyinsaiSubmitServer,
    tmp_path: Path,
    *,
    bound: bool = True,
):
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    manifest = _manifest(tmp_path, archive, "candidate")
    _push(server, archive, manifest)
    doc = json.loads(server.queue_path.read_text(encoding="utf-8"))
    item = doc["queue"][0]
    item.update(
        {
            "status": "outcome_unknown",
            "attemptId": "attempt-no-dispatch-1",
            "submitStartedAt": "2026-07-10T14:33:57.592Z",
            "submittedAt": "",
            "acceptedAt": "",
            "exitCode": 5,
        }
    )
    stdout = server.submissions / "aicomp_mcp_runner_stdout_no_dispatch.log"
    stdout.write_bytes(
        "\n".join(
            [
                'upload ready: {"ready":true,"state":[]}',
                "upload state after settle: []",
                'click: {"clicked":false}',
                'click: {"clicked":false}',
                'click: {"clicked":false}',
                "",
            ]
        ).encode("utf-8"),
    )
    runner_id = "runner-no-dispatch"
    if bound:
        item["runnerId"] = runner_id
    server.queue_path.write_text(json.dumps(doc), encoding="utf-8")
    runner_record = {
        "runner_id": runner_id,
        "pid": 999_999_999,
        "stdout": str(stdout),
        "stderr": str(server.submissions / "runner-no-dispatch.stderr.log"),
        "startedAt": "2026-07-10T14:33:56Z",
        "intent": {
            "action": "submit_candidate",
            "queueIndex": item["index"],
            "candidateSha256": item["provenanceCandidateSha256"],
            "logicalSubmissionId": item["logicalSubmissionId"],
            "queueRevision": "sha256:old-attempt",
        },
        "mode": "submit-once",
    }
    if bound:
        runner_record["attemptId"] = item["attemptId"]
        runner_record["intent"]["attemptId"] = item["attemptId"]
        runner_record["intent"]["runnerId"] = runner_id
    server.runner_state_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "runners": [runner_record],
            }
        ),
        encoding="utf-8",
    )
    return item, stdout, runner_id


def _seed_cdp_connection_refused_attempt(server: JinyinsaiSubmitServer, tmp_path: Path):
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    manifest = _manifest(tmp_path, archive, "candidate")
    _push(server, archive, manifest)
    doc = json.loads(server.queue_path.read_text(encoding="utf-8"))
    item = doc["queue"][0]
    runner_id = "runner-cdp-refused"
    attempt_id = "attempt-cdp-refused-1"
    item.update(
        {
            "status": "outcome_unknown",
            "attemptId": attempt_id,
            "runnerId": runner_id,
            "submitStartedAt": "2026-07-13T06:15:22.231Z",
            "submittedAt": "",
            "acceptedAt": "",
            "exitCode": 1,
        }
    )
    stdout = server.submissions / "aicomp_mcp_runner_stdout_cdp_refused.log"
    stderr = server.submissions / "aicomp_mcp_runner_stderr_cdp_refused.log"
    stdout.write_text(
        "\n".join(
            [
                "Submitting exactly one bound candidate: candidate.zip",
                "selected by priority desc, createdAt asc: priority=1000000",
                "",
            ]
        ),
        encoding="utf-8",
    )
    stderr.write_text(
        "\n".join(
            [
                "Error: connect ECONNREFUSED 127.0.0.1:9222",
                "Error: SUBMIT_OUTCOME_UNKNOWN_BLOCKING",
                "",
            ]
        ),
        encoding="utf-8",
    )
    server.queue_path.write_text(json.dumps(doc), encoding="utf-8")
    server.runner_state_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "runners": [
                    {
                        "runner_id": runner_id,
                        "pid": 999_999_998,
                        "stdout": str(stdout),
                        "stderr": str(stderr),
                        "startedAt": "2026-07-13T06:15:21.923Z",
                        "attemptId": attempt_id,
                        "intent": {
                            "action": "submit_candidate",
                            "queueIndex": item["index"],
                            "candidateSha256": item["provenanceCandidateSha256"],
                            "logicalSubmissionId": item["logicalSubmissionId"],
                            "queueRevision": "sha256:old-attempt",
                            "attemptId": attempt_id,
                            "runnerId": runner_id,
                        },
                        "mode": "submit-once",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    events = [
        {
            "time": "2026-07-13T06:15:21.923Z",
            "event": "runner.started",
            "queue_index": item["index"],
            "logical_submission_id": item["logicalSubmissionId"],
            "candidate_sha256": item["provenanceCandidateSha256"],
        },
        {
            "time": "2026-07-13T06:15:22.231Z",
            "event": "submit.start",
            "queue_index": item["index"],
            "attempt_id": attempt_id,
            "logical_submission_id": item["logicalSubmissionId"],
            "candidate_sha256": item["provenanceCandidateSha256"],
        },
        {
            "time": "2026-07-13T06:15:22.810Z",
            "event": "submit.outcome_unknown_blocking",
            "queue_index": item["index"],
            "attempt_id": attempt_id,
            "exit_code": 1,
        },
    ]
    server.events_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    return item, stdout, stderr, runner_id


def _seed_upload_ready_process_lost_attempt(server: JinyinsaiSubmitServer, tmp_path: Path):
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    manifest = _manifest(tmp_path, archive, "candidate")
    _push(server, archive, manifest)
    doc = json.loads(server.queue_path.read_text(encoding="utf-8"))
    item = doc["queue"][0]
    runner_id = "runner-upload-lost"
    attempt_id = "attempt-upload-lost-1"
    item.update(
        {
            "status": "uploading",
            "attemptId": attempt_id,
            "runnerId": runner_id,
            "submitStartedAt": "2026-07-13T06:50:03.049Z",
            "submittedAt": "",
            "acceptedAt": "",
            "exitCode": "",
            "submitDispatchState": "pending",
        }
    )
    stdout = server.submissions / "aicomp_mcp_runner_stdout_upload_lost.log"
    stderr = server.submissions / "aicomp_mcp_runner_stderr_upload_lost.log"
    stdout.write_text(
        "\n".join(
            [
                "Submitting exactly one bound candidate: candidate.zip",
                "selected by priority desc, createdAt asc: priority=1000000",
                "open submit form: {\"clicked\":true,\"target\":\"提交作品\"}",
                "remove old file: {\"removed\":true}",
                f"set file input: true {archive}",
                "upload ready: {\"ready\":true,\"state\":[{\"visible\":true,\"text\":\"candidate.zip 下载\",\"ready\":true}]}",
                "waiting 45000ms for platform form state to settle...",
                "",
            ]
        ),
        encoding="utf-8",
    )
    stderr.write_text("", encoding="utf-8")
    server.queue_path.write_text(json.dumps(doc), encoding="utf-8")
    runner_record = {
        "runner_id": runner_id,
        "pid": 999_999_997,
        "stdout": str(stdout),
        "stderr": str(stderr),
        "startedAt": "2026-07-13T06:50:02Z",
        "attemptId": attempt_id,
        "intent": {
            "action": "submit_candidate",
            "queueIndex": item["index"],
            "candidateSha256": item["provenanceCandidateSha256"],
            "logicalSubmissionId": item["logicalSubmissionId"],
            "queueRevision": "sha256:old-attempt",
            "attemptId": attempt_id,
            "runnerId": runner_id,
        },
        "mode": "submit-once",
    }
    server.runner_state_path.write_text(
        json.dumps({"schemaVersion": 1, "runners": [runner_record]}),
        encoding="utf-8",
    )
    server.runner_lease_path.mkdir()
    (server.runner_lease_path / "owner.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "token": "lease-token-upload-lost",
                "pid": 999_999_997,
                "phase": "running",
                "intent": runner_record["intent"],
                "acquiredAt": "2026-07-13T06:50:02Z",
                "runner_id": runner_id,
                "startedAt": "2026-07-13T06:50:02Z",
            }
        ),
        encoding="utf-8",
    )
    return item, stdout, stderr, runner_id


def _seed_v1_legacy_binding(server: JinyinsaiSubmitServer, tmp_path: Path):
    item, stdout, runner_id = _seed_legacy_no_dispatch_attempt(server, tmp_path, bound=True)
    evidence_sha256 = _sha256(stdout)
    binding = {
        "bindingVersion": "legacy-attempt-binding-v1",
        "attemptId": item["attemptId"],
        "runnerId": runner_id,
        "evidenceSha256": evidence_sha256,
        "proofVersion": "legacy-three-false-clicks-v1",
    }
    doc = json.loads(server.queue_path.read_text(encoding="utf-8"))
    doc["queue"][0]["note"] = stdout.read_text(encoding="utf-8")[-1000:]
    doc["queue"][0]["submitDispatchState"] = "not_dispatched"
    doc["queue"][0]["legacyNoDispatchBinding"] = binding
    server.queue_path.write_text(json.dumps(doc), encoding="utf-8")
    runner_doc = json.loads(server.runner_state_path.read_text(encoding="utf-8"))
    runner_doc["runners"][0]["legacyNoDispatchBinding"] = binding
    server.runner_state_path.write_text(json.dumps(runner_doc), encoding="utf-8")
    events = [
        {
            "time": "2026-07-10T14:33:57.586Z",
            "event": "runner.started",
            "queue_index": item["index"],
            "logical_submission_id": item["logicalSubmissionId"],
            "candidate_sha256": item["provenanceCandidateSha256"],
        },
        {
            "time": "2026-07-10T14:33:57.812Z",
            "event": "submit.start",
            "queue_index": item["index"],
            "attempt_id": item["attemptId"],
            "logical_submission_id": item["logicalSubmissionId"],
            "candidate_sha256": item["provenanceCandidateSha256"],
        },
        {
            "time": "2026-07-10T14:35:42.802Z",
            "event": "submit.outcome_unknown_blocking",
            "queue_index": item["index"],
            "attempt_id": item["attemptId"],
            "exit_code": 5,
        },
    ]
    server.events_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    return item, stdout, runner_id


def test_mcp_exposes_versioned_caller_contract(tmp_path):
    server = _server(tmp_path)

    initialized = server.initialize({})
    status = server.queue_status()
    queue_status_tool = next(tool for tool in server.tools() if tool["name"] == "queue_status")

    assert getattr(submit_mcp, "SUBMISSION_CONTRACT_VERSION", None) == SUBMISSION_CONTRACT_VERSION
    assert initialized["serverInfo"]["version"] == "0.3.0"
    assert SUBMISSION_CONTRACT_VERSION in initialized["instructions"]
    assert "MCP_CHANGE_REQUEST" in initialized["instructions"]
    assert status["contractVersion"] == SUBMISSION_CONTRACT_VERSION
    assert status["queueSchemaVersion"] == QUEUE_SCHEMA_VERSION
    assert status["requiredCallerSandboxMode"] == "read-only"
    assert "read-only" in initialized["instructions"]
    assert "contract" in queue_status_tool["description"].lower()


def test_server_rejects_explicit_empty_authorized_roots(tmp_path):
    with pytest.raises(ValueError, match="AUTHORIZED_ARTIFACT_ROOTS_REQUIRED"):
        JinyinsaiSubmitServer(
            io.StringIO(),
            io.StringIO(),
            io.StringIO(),
            root=tmp_path,
            authorized_artifact_roots=[],
        )


def test_server_rejects_reparse_authorized_root_before_resolve(monkeypatch, tmp_path):
    authorized_root = tmp_path / "junction-root"
    authorized_root.mkdir()
    real_lstat = os.lstat
    real_resolve = Path.resolve
    resolve_called = False

    class ReparseStat:
        st_mode = real_lstat(authorized_root).st_mode
        st_file_attributes = 0x400

    def fake_lstat(path):
        if Path(path) == authorized_root:
            return ReparseStat()
        return real_lstat(path)

    def guarded_resolve(path, *args, **kwargs):
        nonlocal resolve_called
        if path == authorized_root:
            resolve_called = True
            pytest.fail("reparse authorized root must be rejected before resolve")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", fake_lstat)
    monkeypatch.setattr(Path, "resolve", guarded_resolve)

    with pytest.raises(
        ValueError, match="AUTHORIZED_ARTIFACT_ROOT_REPARSE_POINT_FORBIDDEN"
    ):
        JinyinsaiSubmitServer(
            io.StringIO(),
            io.StringIO(),
            io.StringIO(),
            root=tmp_path,
            authorized_artifact_roots=[authorized_root],
        )
    assert resolve_called is False


def test_queue_push_rejects_candidate_outside_authorized_roots_before_hash(
    monkeypatch, tmp_path
):
    server = _server(tmp_path / "root")
    outside = tmp_path / "outside"
    archive = _zip(outside / "candidate.zip")
    manifest = _manifest(outside, archive, "candidate")
    monkeypatch.setattr(
        submit_mcp,
        "sha256_file",
        lambda *_args, **_kwargs: pytest.fail("outside candidate must not be hashed"),
    )

    with pytest.raises(ValueError, match="SUBMISSION_PATH_OUTSIDE_AUTHORIZED_ROOTS"):
        _push(server, archive, manifest)


def test_queue_push_rejects_manifest_outside_authorized_roots_before_read(
    monkeypatch, tmp_path
):
    server = _server(tmp_path / "root")
    archive = _zip(server.submissions / "candidate.zip")
    outside = tmp_path / "outside"
    outside.mkdir()
    manifest = _manifest(outside, archive, "candidate")
    original_read_text = Path.read_text

    def guarded_read_text(path, *args, **kwargs):
        if path == manifest:
            pytest.fail("outside manifest must not be read")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    with pytest.raises(
        ValueError, match="PROVENANCE_MANIFEST_PATH_OUTSIDE_AUTHORIZED_ROOTS"
    ):
        _push(server, archive, manifest)


def test_queue_push_rejects_evidence_outside_authorized_roots_before_hash(
    monkeypatch, tmp_path
):
    server = _server(tmp_path / "root")
    archive = _zip(server.submissions / "candidate.zip")
    outside = tmp_path / "outside"
    outside.mkdir()
    evidence = outside / "evidence.log"
    evidence.write_text(
        "loaded model_id=vit_base_patch32_clip_224.openai\n", encoding="utf-8"
    )
    manifest = server.root / "candidate.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "experiment_id": "candidate",
                "candidate_zip": str(archive),
                "candidate_artifact_id": "candidate",
                "candidate_sha256": _sha256(archive),
                "backbone": "CLIP ViT-B/32",
                "model_id": "vit_base_patch32_clip_224.openai",
                "evidence_files": [
                    {
                        "artifact_id": "evidence",
                        "path": str(evidence),
                        "sha256": "0" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="PROVENANCE_PATH_OUTSIDE_AUTHORIZED_ROOTS"):
        _push(server, archive, manifest)


def test_queue_push_rejects_duplicate_override(tmp_path):
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    manifest = _manifest(tmp_path, archive, "candidate")

    with pytest.raises(ValueError, match="DUPLICATE_OVERRIDE_FORBIDDEN"):
        _push(server, archive, manifest, allow_duplicate=True)


def test_queue_push_rejects_same_path_twice_in_one_batch_atomically(tmp_path):
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    manifest = _manifest(tmp_path, archive, "candidate")

    with pytest.raises(ValueError, match="DUPLICATE_SUBMISSION_IDENTITY"):
        server.queue_push(
            {
                "files": [str(archive), str(archive)],
                "provenance_manifests": [str(manifest), str(manifest)],
            },
            front=True,
        )

    if server.queue_path.exists():
        assert json.loads(server.queue_path.read_text(encoding="utf-8"))["queue"] == []


def test_queue_push_rejects_same_sha_at_different_paths(tmp_path):
    server = _server(tmp_path)
    first = _zip(tmp_path / "submissions" / "first.zip")
    second = tmp_path / "submissions" / "renamed.zip"
    second.write_bytes(first.read_bytes())
    first_manifest = _manifest(tmp_path, first, "first")
    second_manifest = _manifest(tmp_path, second, "second")

    _push(server, first, first_manifest)

    with pytest.raises(ValueError, match="DUPLICATE_SUBMISSION_IDENTITY"):
        _push(server, second, second_manifest)

    queue = json.loads(server.queue_path.read_text(encoding="utf-8"))["queue"]
    assert [item["name"] for item in queue] == ["first.zip"]


def test_queue_push_respects_terminal_artifact_sha_tombstone(tmp_path):
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "renamed.zip")
    manifest = _manifest(tmp_path, archive, "renamed")
    server.queue_path.write_text(
        json.dumps(
            {
                "schemaVersion": 3,
                "queue": [
                    {
                        "index": 144,
                        "name": "historical.zip",
                        "path": str(tmp_path / "missing" / "historical.zip"),
                        "status": "skipped",
                        "artifactSha256": _sha256(archive),
                        "logicalSubmissionId": "legacy-index-144",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="candidate_sha256"):
        _push(server, archive, manifest)

    queue = json.loads(server.queue_path.read_text(encoding="utf-8"))["queue"]
    assert [item["index"] for item in queue] == [144]


def test_queue_remove_retains_identity_tombstone_and_prevents_replay(tmp_path):
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    manifest = _manifest(tmp_path, archive, "candidate")
    _push(server, archive, manifest)

    result = server.queue_remove({"items": [archive.name], "reason": "candidate withdrawn"})

    item = json.loads(server.queue_path.read_text(encoding="utf-8"))["queue"][0]
    assert result["removed"] == [archive.name]
    assert item["status"] == "dropped"
    assert item["removeReason"] == "candidate withdrawn"
    assert item["logicalSubmissionId"]
    assert item["provenanceCandidateSha256"] == _sha256(archive)

    with pytest.raises(ValueError, match="DUPLICATE_SUBMISSION_IDENTITY"):
        _push(server, archive, manifest)


def test_queue_status_returns_candidate_bound_runner_intent(tmp_path):
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    manifest = _manifest(tmp_path, archive, "candidate")
    _push(server, archive, manifest)

    status = server.queue_status()
    intent = status["runnerStartIntent"]

    assert intent["action"] == "submit_candidate"
    assert intent["queueIndex"] == 1
    assert intent["candidateSha256"] == _sha256(archive)
    assert intent["logicalSubmissionId"].endswith(":r0")
    assert intent["queueRevision"].startswith("sha256:")


def test_legacy_no_click_attempt_requires_exact_reconciliation_before_retry(tmp_path):
    server = _server(tmp_path)
    original, stdout, runner_id = _seed_legacy_no_dispatch_attempt(server, tmp_path)

    status = server.queue_status()
    intent = status["reconciliationIntent"]

    assert status["state"] == "submit_not_dispatched_reconcile_required"
    assert status["runnerStartIntent"] is None
    assert status["identityIssues"] == []
    assert intent["action"] == "reconcile_not_dispatched"
    assert intent["queueIndex"] == original["index"]
    assert intent["candidateSha256"] == original["provenanceCandidateSha256"]
    assert intent["logicalSubmissionId"] == original["logicalSubmissionId"]
    assert intent["attemptId"] == original["attemptId"]
    assert intent["runnerId"] == runner_id
    assert intent["evidenceSha256"] == _sha256(stdout)

    with pytest.raises(ValueError, match="CONFIRM_NOT_DISPATCHED_RETRY_REQUIRED"):
        server.queue_reconcile_not_dispatched({"confirm_retry_same_identity": False, "intent": intent})

    result = server.queue_reconcile_not_dispatched(
        {"confirm_retry_same_identity": True, "intent": intent}
    )

    queue = json.loads(server.queue_path.read_text(encoding="utf-8"))["queue"]
    assert len(queue) == 1
    item = queue[0]
    assert item["status"] == "queued"
    assert item["logicalSubmissionId"] == original["logicalSubmissionId"]
    assert item["provenanceCandidateSha256"] == original["provenanceCandidateSha256"]
    assert item["lastNoDispatchAttempt"]["attemptId"] == original["attemptId"]
    assert item["lastNoDispatchAttempt"]["evidenceSha256"] == _sha256(stdout)
    assert result["status"] == "queued"
    assert result["sameIdentity"] is True
    assert list((server.submissions / "reconciliation_backups").glob("*.json"))
    event = json.loads(server.events_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["event"] == "submit.not_dispatched_reconciled"
    assert event["logical_submission_id"] == original["logicalSubmissionId"]

    refreshed = server.queue_status()
    assert refreshed["state"] == "queued"
    assert refreshed["reconciliationIntent"] is None
    assert refreshed["runnerStartIntent"]["action"] == "submit_candidate"
    assert refreshed["runnerStartIntent"]["logicalSubmissionId"] == original["logicalSubmissionId"]


def test_cdp_connection_refused_before_browser_submit_is_reconcilable(tmp_path):
    server = _server(tmp_path)
    original, _stdout, _stderr, runner_id = _seed_cdp_connection_refused_attempt(server, tmp_path)

    status = server.queue_status()
    intent = status["reconciliationIntent"]

    assert status["state"] == "submit_not_dispatched_reconcile_required"
    assert status["runnerStartIntent"] is None
    assert status["identityIssues"] == []
    assert intent["action"] == "reconcile_not_dispatched"
    assert intent["queueIndex"] == original["index"]
    assert intent["candidateSha256"] == original["provenanceCandidateSha256"]
    assert intent["logicalSubmissionId"] == original["logicalSubmissionId"]
    assert intent["attemptId"] == original["attemptId"]
    assert intent["runnerId"] == runner_id
    assert intent["proofVersion"] == "cdp-connection-refused-before-submit-v1"


def test_cdp_connection_refused_selects_current_repeated_identity_attempt(tmp_path):
    server = _server(tmp_path)
    original, _stdout, _stderr, runner_id = _seed_cdp_connection_refused_attempt(
        server, tmp_path
    )
    current_events = server.events_path.read_text(encoding="utf-8")
    previous_attempt_id = "attempt-cdp-refused-previous"
    previous_runner_id = "runner-cdp-refused-previous"
    previous_events = [
        {
            "time": "2026-07-13T03:15:21.923Z",
            "event": "runner.started",
            "queue_index": original["index"],
            "logical_submission_id": original["logicalSubmissionId"],
            "candidate_sha256": original["provenanceCandidateSha256"],
        },
        {
            "time": "2026-07-13T03:15:22.231Z",
            "event": "submit.start",
            "queue_index": original["index"],
            "attempt_id": previous_attempt_id,
            "logical_submission_id": original["logicalSubmissionId"],
            "candidate_sha256": original["provenanceCandidateSha256"],
        },
        {
            "time": "2026-07-13T03:15:22.810Z",
            "event": "submit.outcome_unknown_blocking",
            "queue_index": original["index"],
            "attempt_id": previous_attempt_id,
            "exit_code": 1,
        },
    ]
    server.events_path.write_text(
        "".join(json.dumps(event) + "\n" for event in previous_events)
        + current_events,
        encoding="utf-8",
    )
    runner_doc = json.loads(server.runner_state_path.read_text(encoding="utf-8"))
    previous_runner = json.loads(json.dumps(runner_doc["runners"][0]))
    previous_runner.update(
        {
            "runner_id": previous_runner_id,
            "attemptId": previous_attempt_id,
            "startedAt": "2026-07-13T03:15:21.923Z",
        }
    )
    previous_runner["intent"]["attemptId"] = previous_attempt_id
    previous_runner["intent"]["runnerId"] = previous_runner_id
    runner_doc["runners"].insert(0, previous_runner)
    server.runner_state_path.write_text(json.dumps(runner_doc), encoding="utf-8")

    status = server.queue_status()

    assert status["state"] == "submit_not_dispatched_reconcile_required"
    assert status["identityIssues"] == []
    assert status["reconciliationIntent"]["attemptId"] == original["attemptId"]
    assert status["reconciliationIntent"]["runnerId"] == runner_id


def test_cdp_connection_refused_rejects_ambiguous_runner_start_events(tmp_path):
    server = _server(tmp_path)
    original, _stdout, _stderr, _runner_id = _seed_cdp_connection_refused_attempt(
        server, tmp_path
    )
    events = server.events_path.read_text(encoding="utf-8").splitlines()
    events.insert(
        1,
        json.dumps(
            {
                "time": "2026-07-13T06:15:22.100Z",
                "event": "runner.started",
                "queue_index": original["index"],
                "logical_submission_id": original["logicalSubmissionId"],
                "candidate_sha256": original["provenanceCandidateSha256"],
            }
        ),
    )
    server.events_path.write_text("\n".join(events) + "\n", encoding="utf-8")

    status = server.queue_status()

    assert status["state"] == "blocked_identity_corruption"
    assert status["reconciliationIntent"] is None
    assert "BLOCKING_QUEUE_ITEM_WITHOUT_ACTIVE_LOCK" in status["identityIssues"]


def test_cdp_connection_refused_proof_requires_matching_event_chain(tmp_path):
    server = _server(tmp_path)
    _original, _stdout, _stderr, _runner_id = _seed_cdp_connection_refused_attempt(server, tmp_path)
    server.events_path.write_text("", encoding="utf-8")

    status = server.queue_status()

    assert status["state"] == "blocked_identity_corruption"
    assert status["reconciliationIntent"] is None
    assert "BLOCKING_QUEUE_ITEM_WITHOUT_ACTIVE_LOCK" in status["identityIssues"]


def test_cdp_connection_refused_proof_rejects_any_clicked_marker(tmp_path):
    server = _server(tmp_path)
    _original, stdout, _stderr, _runner_id = _seed_cdp_connection_refused_attempt(server, tmp_path)
    stdout.write_text(
        stdout.read_text(encoding="utf-8") + "SUBMIT_CLICKED_AT=2026-07-13T06:15:22.500Z\n",
        encoding="utf-8",
    )

    status = server.queue_status()

    assert status["state"] == "blocked_identity_corruption"
    assert status["reconciliationIntent"] is None
    assert "BLOCKING_QUEUE_ITEM_WITHOUT_ACTIVE_LOCK" in status["identityIssues"]


def test_upload_ready_process_lost_before_click_reconciles_matching_stale_lease(tmp_path):
    server = _server(tmp_path)
    original, _stdout, _stderr, runner_id = _seed_upload_ready_process_lost_attempt(server, tmp_path)

    status = server.queue_status()
    intent = status["reconciliationIntent"]

    assert status["state"] == "submit_not_dispatched_reconcile_required"
    assert status["identityIssues"] == []
    assert intent["proofVersion"] == "upload-ready-process-lost-before-submit-v1"
    assert intent["queueIndex"] == original["index"]
    assert intent["runnerId"] == runner_id

    result = server.queue_reconcile_not_dispatched(
        {"confirm_retry_same_identity": True, "intent": intent}
    )

    assert result["ok"] is True
    assert result["sameIdentity"] is True
    assert not server.runner_lease_path.exists()
    item = json.loads(server.queue_path.read_text(encoding="utf-8"))["queue"][0]
    assert item["status"] == "queued"
    assert item["lastNoDispatchAttempt"]["proofVersion"] == "upload-ready-process-lost-before-submit-v1"


def test_upload_ready_process_lost_rejects_submit_click_marker(tmp_path):
    server = _server(tmp_path)
    _original, stdout, _stderr, _runner_id = _seed_upload_ready_process_lost_attempt(server, tmp_path)
    stdout.write_text(
        stdout.read_text(encoding="utf-8")
        + 'click: {"clicked":true,"text":"提交"}\nSUBMIT_CLICKED_AT=2026-07-13T06:50:49Z\n',
        encoding="utf-8",
    )

    status = server.queue_status()

    assert status["state"] == "blocked_identity_corruption"
    assert status["reconciliationIntent"] is None
    assert "BLOCKING_QUEUE_ITEM_WITHOUT_ACTIVE_LOCK" in status["identityIssues"]


def test_upload_ready_process_lost_rejects_missing_stderr_log(tmp_path):
    server = _server(tmp_path)
    _original, _stdout, stderr, _runner_id = _seed_upload_ready_process_lost_attempt(
        server, tmp_path
    )
    stderr.unlink()

    status = server.queue_status()

    assert status["state"] == "blocked_identity_corruption"
    assert status["reconciliationIntent"] is None
    assert "BLOCKING_QUEUE_ITEM_WITHOUT_ACTIVE_LOCK" in status["identityIssues"]


def test_upload_ready_process_lost_rejects_mismatched_stale_lease(tmp_path):
    server = _server(tmp_path)
    _original, _stdout, _stderr, _runner_id = _seed_upload_ready_process_lost_attempt(server, tmp_path)
    owner = json.loads((server.runner_lease_path / "owner.json").read_text(encoding="utf-8"))
    owner["intent"]["runnerId"] = "runner-other"
    (server.runner_lease_path / "owner.json").write_text(json.dumps(owner), encoding="utf-8")

    status = server.queue_status()

    assert status["state"] == "blocked_identity_corruption"
    assert status["reconciliationIntent"] is None
    assert "BLOCKING_QUEUE_ITEM_WITHOUT_ACTIVE_LOCK" in status["identityIssues"]


def test_unbound_legacy_attempt_stays_blocked_without_durable_runner_bridge(tmp_path):
    server = _server(tmp_path)
    _item, _stdout, _runner_id = _seed_legacy_no_dispatch_attempt(server, tmp_path, bound=False)

    status = server.queue_status()

    assert status["state"] == "blocked_identity_corruption"
    assert status["reconciliationIntent"] is None
    assert server.legacy_no_dispatch_binding_plan() is None


def test_v1_legacy_binding_upgrades_from_one_exact_snapshot_before_reconciliation(tmp_path):
    server = _server(tmp_path)
    item, stdout, runner_id = _seed_v1_legacy_binding(server, tmp_path)
    before = server.queue_path.read_bytes()

    status = server.queue_status()
    plan = server.legacy_no_dispatch_binding_plan()

    assert status["state"] == "blocked_identity_corruption"
    assert status["reconciliationIntent"] is None
    assert plan["action"] == "bind_legacy_no_dispatch_attempt_v2"
    assert plan["queueIndex"] == item["index"]
    assert plan["attemptId"] == item["attemptId"]
    assert plan["runnerId"] == runner_id
    assert plan["evidenceSha256"] == _sha256(stdout)
    assert plan["queueNoteTailSha256"]
    assert plan["eventChainSha256"]
    assert plan["eventsSha256"]

    with pytest.raises(ValueError, match="CONFIRM_LEGACY_NO_DISPATCH_BINDING_REQUIRED"):
        server.bind_legacy_no_dispatch_attempt({"confirm_binding": False, "intent": plan})
    assert server.queue_path.read_bytes() == before

    result = server.bind_legacy_no_dispatch_attempt({"confirm_binding": True, "intent": plan})

    assert result["bound"] is True
    queue_item = json.loads(server.queue_path.read_text(encoding="utf-8"))["queue"][0]
    runner = json.loads(server.runner_state_path.read_text(encoding="utf-8"))["runners"][0]
    assert queue_item["runnerId"] == runner_id
    assert queue_item["legacyNoDispatchBinding"]["bindingVersion"] == "legacy-attempt-binding-v2"
    assert runner["attemptId"] == item["attemptId"]
    assert runner["intent"]["attemptId"] == item["attemptId"]
    assert runner["intent"]["runnerId"] == runner_id
    assert runner["legacyNoDispatchBinding"]["bindingVersion"] == "legacy-attempt-binding-v2"
    assert server.queue_status()["state"] == "submit_not_dispatched_reconcile_required"
    backups = list((server.submissions / "reconciliation_backups").glob("*legacy-binding-v2*.json"))
    assert len(backups) == 2


def test_legacy_binding_is_evidence_hash_bound_and_not_exposed_to_operator(tmp_path):
    server = _server(tmp_path)
    _item, stdout, _runner_id = _seed_v1_legacy_binding(server, tmp_path)
    plan = server.legacy_no_dispatch_binding_plan()
    before_queue = server.queue_path.read_bytes()
    before_runners = server.runner_state_path.read_bytes()
    stdout.write_text(stdout.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="STALE_LEGACY_NO_DISPATCH_BINDING_CONFIRMATION"):
        server.bind_legacy_no_dispatch_attempt({"confirm_binding": True, "intent": plan})

    assert server.queue_path.read_bytes() == before_queue
    assert server.runner_state_path.read_bytes() == before_runners
    assert "bind_legacy_no_dispatch_attempt" not in {tool["name"] for tool in server.tools()}
    assert not (server.submissions / "reconciliation_backups").exists()


def test_legacy_binding_rejects_second_identity_runner_before_log_proof_filter(tmp_path):
    server = _server(tmp_path)
    item, _stdout, _runner_id = _seed_v1_legacy_binding(server, tmp_path)
    runner_doc = json.loads(server.runner_state_path.read_text(encoding="utf-8"))
    second_log = server.submissions / "aicomp_mcp_runner_stdout_uncertain.log"
    second_log.write_text("upload started but outcome unavailable\n", encoding="utf-8")
    second = json.loads(json.dumps(runner_doc["runners"][0]))
    second["runner_id"] = "runner-current-uncertain"
    second["stdout"] = str(second_log)
    second["attemptId"] = item["attemptId"]
    second["intent"]["runnerId"] = second["runner_id"]
    second.pop("legacyNoDispatchBinding", None)
    runner_doc["runners"].append(second)
    server.runner_state_path.write_text(json.dumps(runner_doc), encoding="utf-8")
    queue_doc = json.loads(server.queue_path.read_text(encoding="utf-8"))
    queue_doc["queue"][0]["runnerId"] = second["runner_id"]
    queue_doc["queue"][0].pop("legacyNoDispatchBinding", None)
    server.queue_path.write_text(json.dumps(queue_doc), encoding="utf-8")

    assert server.legacy_no_dispatch_binding_plan() is None
    assert server.queue_status()["state"] == "blocked_identity_corruption"


def test_legacy_binding_runner_revision_is_exact_cas(tmp_path):
    server = _server(tmp_path)
    _seed_v1_legacy_binding(server, tmp_path)
    plan = server.legacy_no_dispatch_binding_plan()
    before_queue = server.queue_path.read_bytes()
    runner_doc = json.loads(server.runner_state_path.read_text(encoding="utf-8"))
    runner_doc["diagnosticMutation"] = True
    server.runner_state_path.write_text(json.dumps(runner_doc), encoding="utf-8")
    mutated_runners = server.runner_state_path.read_bytes()

    with pytest.raises(ValueError, match="STALE_LEGACY_NO_DISPATCH_BINDING_CONFIRMATION"):
        server.bind_legacy_no_dispatch_attempt({"confirm_binding": True, "intent": plan})

    assert server.queue_path.read_bytes() == before_queue
    assert server.runner_state_path.read_bytes() == mutated_runners
    assert not (server.submissions / "reconciliation_backups").exists()


def test_explicit_node_no_dispatch_marker_is_reconcilable_with_runner_id_binding(tmp_path):
    server = _server(tmp_path)
    item, stdout, runner_id = _seed_legacy_no_dispatch_attempt(server, tmp_path)
    doc = json.loads(server.queue_path.read_text(encoding="utf-8"))
    doc["queue"][0]["status"] = "submit_not_dispatched"
    doc["queue"][0]["runnerId"] = runner_id
    doc["queue"][0]["submitDispatchState"] = "not_dispatched"
    server.queue_path.write_text(json.dumps(doc), encoding="utf-8")
    stdout.write_text(
        stdout.read_text(encoding="utf-8")
        + "SUBMIT_NOT_DISPATCHED=NO_SUBMIT_BUTTON_CLICKED\n",
        encoding="utf-8",
    )

    status = server.queue_status()

    assert status["state"] == "submit_not_dispatched_reconcile_required"
    assert status["reconciliationIntent"]["proofVersion"] == "explicit-marker-v1"
    assert status["reconciliationIntent"]["runnerId"] == runner_id


def test_not_dispatched_reconciliation_intent_is_evidence_hash_bound(tmp_path):
    server = _server(tmp_path)
    _item, stdout, _runner_id = _seed_legacy_no_dispatch_attempt(server, tmp_path)
    intent = server.queue_status()["reconciliationIntent"]
    before = server.queue_path.read_bytes()

    stdout.write_text(stdout.read_text(encoding="utf-8") + "diagnostic changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="STALE_RECONCILIATION_CONFIRMATION"):
        server.queue_reconcile_not_dispatched(
            {"confirm_retry_same_identity": True, "intent": intent}
        )

    assert server.queue_path.read_bytes() == before


def test_clicked_attempt_never_gets_no_dispatch_reconciliation_intent(tmp_path):
    server = _server(tmp_path)
    _item, stdout, _runner_id = _seed_legacy_no_dispatch_attempt(server, tmp_path)
    stdout.write_text(
        stdout.read_text(encoding="utf-8")
        + "SUBMIT_CLICKED_AT=2026-07-10T14:34:59.000Z\n",
        encoding="utf-8",
    )

    status = server.queue_status()

    assert status["state"] == "blocked_identity_corruption"
    assert status["reconciliationIntent"] is None
    assert "BLOCKING_QUEUE_ITEM_WITHOUT_ACTIVE_LOCK" in status["identityIssues"]


def test_no_dispatch_reconciliation_requires_exactly_one_matching_runner(tmp_path):
    server = _server(tmp_path)
    _item, _stdout, _runner_id = _seed_legacy_no_dispatch_attempt(server, tmp_path)
    runner_doc = json.loads(server.runner_state_path.read_text(encoding="utf-8"))
    duplicate = dict(runner_doc["runners"][0])
    runner_doc["runners"].append(duplicate)
    server.runner_state_path.write_text(json.dumps(runner_doc), encoding="utf-8")

    status = server.queue_status()

    assert status["state"] == "blocked_identity_corruption"
    assert status["reconciliationIntent"] is None


def test_no_dispatch_reconciliation_is_withheld_while_any_runner_lease_exists(tmp_path):
    server = _server(tmp_path)
    _seed_legacy_no_dispatch_attempt(server, tmp_path)
    server.runner_lease_path.mkdir()
    (server.runner_lease_path / "owner.json").write_text(
        json.dumps({"token": "stale", "pid": 999_999_999}),
        encoding="utf-8",
    )

    status = server.queue_status()

    assert status["state"] == "blocked_identity_corruption"
    assert status["reconciliationIntent"] is None


def test_no_dispatch_reconciliation_revalidates_candidate_bytes_before_mutation(tmp_path):
    server = _server(tmp_path)
    item, _stdout, _runner_id = _seed_legacy_no_dispatch_attempt(server, tmp_path)
    intent = server.queue_status()["reconciliationIntent"]
    before = server.queue_path.read_bytes()
    Path(item["path"]).write_bytes(b"changed after intent")

    with pytest.raises(ValueError, match="PROVENANCE_CANDIDATE_SHA256_MISMATCH"):
        server.queue_reconcile_not_dispatched(
            {"confirm_retry_same_identity": True, "intent": intent}
        )

    assert server.queue_path.read_bytes() == before
    assert not (server.submissions / "reconciliation_backups").exists()


def test_mcp_exposes_only_exact_not_dispatched_reconciliation(tmp_path):
    server = _server(tmp_path)
    tool = next(tool for tool in server.tools() if tool["name"] == "queue_reconcile_not_dispatched")

    assert tool["inputSchema"]["required"] == ["confirm_retry_same_identity", "intent"]
    assert tool["inputSchema"]["additionalProperties"] is False


def test_queue_mutation_invalidates_old_runner_intent(monkeypatch, tmp_path):
    server = _server(tmp_path)
    first = _zip(tmp_path / "submissions" / "first.zip", "a.jpg,0000\n")
    first_manifest = _manifest(tmp_path, first, "first")
    _push(server, first, first_manifest)
    old_intent = server.queue_status()["runnerStartIntent"]

    second = _zip(tmp_path / "submissions" / "second.zip", "a.jpg,0001\n")
    second_manifest = _manifest(tmp_path, second, "second")
    _push(server, second, second_manifest)

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale intent must fail before Popen")),
    )

    with pytest.raises(ValueError, match="STALE_RUNNER_CONFIRMATION"):
        server.queue_runner_start({"confirm_real_submit": True, "intent": old_intent})


def test_two_server_instances_spawn_only_one_runner(monkeypatch, tmp_path):
    monkeypatch.setattr(
        submit_mcp,
        "process_start_identity",
        lambda _pid: "process-current",
    )
    first_server = _server(tmp_path)
    second_server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    manifest = _manifest(tmp_path, archive, "candidate")
    _push(first_server, archive, manifest)
    intent = first_server.queue_status()["runnerStartIntent"]
    calls = []

    class FakeProcess:
        pid = os.getpid()

        def poll(self):
            return None

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    first = first_server.queue_runner_start({"confirm_real_submit": True, "intent": intent})
    second = second_server.queue_runner_start({"confirm_real_submit": True, "intent": intent})

    assert first["started"] is True
    assert second["started"] is False
    assert second["reason"] == "RUNNER_ALREADY_RUNNING"
    assert len(calls) == 1
    env = calls[0][1]["env"]
    assert env["AICOMP_RUN_MODE"] == "submit-once"
    assert env["AICOMP_EXPECTED_QUEUE_INDEX"] == "1"
    assert env["AICOMP_EXPECTED_CANDIDATE_SHA256"] == _sha256(archive)
    assert env["AICOMP_EXPECTED_LOGICAL_SUBMISSION_ID"].endswith(":r0")
    assert env["AICOMP_RUNNER_LEASE_TOKEN"]
    assert env["AICOMP_SUBMIT_ATTEMPT_ID"]
    runner_doc = json.loads(first_server.runner_state_path.read_text(encoding="utf-8"))
    record = runner_doc["runners"][0]
    assert record["attemptId"] == env["AICOMP_SUBMIT_ATTEMPT_ID"]
    assert record["intent"]["attemptId"] == env["AICOMP_SUBMIT_ATTEMPT_ID"]
    assert record["intent"]["runnerId"] == first["runner_id"]
    assert record["processIdentity"] == "process-current"


def test_runner_registry_updates_do_not_lose_concurrent_records(monkeypatch, tmp_path):
    server = _server(tmp_path)
    server.submissions.mkdir(parents=True)
    original_read = server.read_runner_doc
    first_read = threading.Event()
    release_first = threading.Event()

    def delayed_read():
        document = original_read()
        if threading.current_thread().name == "first-runner-writer":
            first_read.set()
            release_first.wait(timeout=2)
        return document

    monkeypatch.setattr(server, "read_runner_doc", delayed_read)
    first = threading.Thread(
        name="first-runner-writer",
        target=server.persist_runner_record,
        args=({"runner_id": "runner-first", "phase": "running"},),
    )
    second = threading.Thread(
        name="second-runner-writer",
        target=server.persist_runner_record,
        args=({"runner_id": "runner-second", "phase": "running"},),
    )

    first.start()
    assert first_read.wait(timeout=1)
    second.start()
    second.join(timeout=0.25)
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    runner_ids = {
        row["runner_id"]
        for row in json.loads(server.runner_state_path.read_text(encoding="utf-8"))[
            "runners"
        ]
    }
    assert runner_ids == {"runner-first", "runner-second"}


def test_runner_registry_updates_are_serialized_across_server_instances(
    monkeypatch, tmp_path
):
    first_server = _server(tmp_path)
    second_server = _server(tmp_path)
    first_server.submissions.mkdir(parents=True)
    original_read = first_server.read_runner_doc
    first_read = threading.Event()
    release_first = threading.Event()

    def delayed_read():
        document = original_read()
        first_read.set()
        release_first.wait(timeout=2)
        return document

    monkeypatch.setattr(first_server, "read_runner_doc", delayed_read)
    first = threading.Thread(
        target=first_server.persist_runner_record,
        args=({"runner_id": "runner-first", "phase": "running"},),
    )
    second = threading.Thread(
        target=second_server.persist_runner_record,
        args=({"runner_id": "runner-second", "phase": "running"},),
    )

    first.start()
    assert first_read.wait(timeout=1)
    second.start()
    second.join(timeout=0.1)
    assert second.is_alive()
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    runner_ids = {
        row["runner_id"]
        for row in json.loads(
            first_server.runner_state_path.read_text(encoding="utf-8")
        )["runners"]
    }
    assert runner_ids == {"runner-first", "runner-second"}
    assert not first_server.runner_registry_lock_path.exists()


def test_popen_to_running_receipt_failure_requires_reconciliation(monkeypatch, tmp_path):
    first_server = _server(tmp_path)
    second_server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    manifest = _manifest(tmp_path, archive, "candidate")
    _push(first_server, archive, manifest)
    intent = first_server.queue_status()["runnerStartIntent"]
    popen_calls = []

    class FakeProcess:
        pid = 999_999_991

        def poll(self):
            return None

    def fake_popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        return FakeProcess()

    original_persist = first_server.persist_runner_record
    persist_calls = 0

    def fail_after_popen(record):
        nonlocal persist_calls
        persist_calls += 1
        if persist_calls == 2:
            raise OSError("fault injection after Popen")
        original_persist(record)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(first_server, "persist_runner_record", fail_after_popen)

    with pytest.raises(OSError, match="fault injection after Popen"):
        first_server.queue_runner_start({"confirm_real_submit": True, "intent": intent})

    launching = json.loads(first_server.runner_state_path.read_text(encoding="utf-8"))["runners"][0]
    assert launching["phase"] == "launching"
    assert len(popen_calls) == 1

    with pytest.raises(ValueError, match="RUNNER_START_RECONCILIATION_REQUIRED"):
        second_server.queue_runner_start({"confirm_real_submit": True, "intent": intent})

    assert len(popen_calls) == 1


def test_phantom_active_without_queue_receipt_blocks_runner_intent(tmp_path):
    server = _server(tmp_path)
    server.submissions.mkdir(parents=True)
    item_path = server.submissions / "old.zip"
    server.queue_path.write_text(
        json.dumps(
            {
                "schemaVersion": 3,
                "queue": [
                    {
                        "index": 144,
                        "name": "old.zip",
                        "path": str(item_path),
                        "status": "awaiting_score",
                        "submittedAt": "",
                        "acceptedAt": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    server.active_path.write_text(
        json.dumps(
            {
                "status": "awaiting_score",
                "queue_index": 144,
                "file": "old.zip",
                "path": str(item_path),
                "accepted_at": "2026-07-09T02:46:27.070Z",
            }
        ),
        encoding="utf-8",
    )

    status = server.queue_status()

    assert status["state"] == "blocked_identity_corruption"
    assert status["runnerStartIntent"] is None
    assert "ACTIVE_ACCEPTANCE_NOT_IN_QUEUE" in status["identityIssues"]


def test_live_runner_state_takes_precedence_over_transient_ledger_anomaly(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        submit_mcp,
        "process_start_identity",
        lambda _pid: "process-current",
    )
    server = _server(tmp_path)
    server.submissions.mkdir(parents=True)
    server.queue_path.write_text(
        json.dumps(
            {
                "schemaVersion": QUEUE_SCHEMA_VERSION,
                "queue": [
                    {
                        "index": 1,
                        "name": "candidate.zip",
                        "path": str(server.submissions / "candidate.zip"),
                        "status": "uploading",
                        "artifactSha256": "d" * 64,
                        "logicalSubmissionId": "logical-1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    server.runner_state_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "runners": [
                    {
                        "runner_id": "runner-live",
                        "pid": os.getpid(),
                        "stdout": str(server.submissions / "live.stdout.log"),
                        "stderr": str(server.submissions / "live.stderr.log"),
                        "startedAt": "2026-07-15T00:00:00Z",
                        "processIdentity": "process-current",
                        "phase": "running",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    status = server.queue_status()

    assert status["state"] == "running"
    assert status["runnerStartIntent"] is None
    assert status["runners"][0]["running"] is True


def test_real_node_child_adopts_parent_lease_without_external_submit(monkeypatch, tmp_path):
    server = _server(tmp_path)
    server.submissions.mkdir(parents=True)
    item_path = server.submissions / "legacy-active.zip"
    server.queue_path.write_text(
        json.dumps(
            {
                "schemaVersion": 3,
                "queue": [
                    {
                        "index": 164,
                        "name": item_path.name,
                        "path": str(item_path),
                        "status": "awaiting_score",
                        "submittedAt": "2020-01-01T00:00:00.000Z",
                        "acceptedAt": "2020-01-01T00:00:01.000Z",
                        "logicalSubmissionId": "legacy-164",
                        "artifactSha256": "a" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    server.active_path.write_text(
        json.dumps(
            {
                "status": "awaiting_score",
                "queue_index": 164,
                "logical_submission_id": "legacy-164",
                "candidate_sha256": "a" * 64,
                "file": item_path.name,
                "path": str(item_path),
                "submitted_at": "2020-01-01T00:00:00.000Z",
                "accepted_at": "2020-01-01T00:00:01.000Z",
                "capture_start_at": "2020-01-01T01:05:00.000Z",
            }
        ),
        encoding="utf-8",
    )
    queue_script = Path(r"D:\02_Projects\ML\jinyinsai\tools\aicomp_submit_queue.mjs")
    node_executable = Path(r"C:\Program Files\nodejs\node.exe")
    if not queue_script.is_file() or not node_executable.is_file():
        pytest.skip("requires the sibling jinyinsai Node runner checkout")
    assert (
        hashlib.sha256(queue_script.read_bytes()).hexdigest()
        == submit_mcp.AICOMP_RUNNER_HELPER_SHA256
    )
    server.runner_command = lambda: [
        str(node_executable),
        str(queue_script),
        "run",
    ]

    intent = server.queue_status()["runnerStartIntent"]
    result = server.queue_runner_start({"confirm_real_submit": False, "intent": intent})
    process = server.runners[result["runner_id"]]["process"]
    exit_code = process.wait(timeout=15)
    stderr = Path(result["stderr"]).read_text(encoding="utf-8", errors="replace")

    assert exit_code != 0
    assert "SUBMISSION_CONTROL_BUSY" not in stderr
    assert "CAPTURE_NOT_COMPLETED" in stderr
    assert not server.runner_lease_path.exists()
    assert not any(event.get("event") == "submit.start" for event in (
        json.loads(line)
        for line in (server.submissions / "aicomp_events.jsonl").read_text(encoding="utf-8").splitlines()
    ))
    claim_files = list((server.submissions / "score_capture_claims").glob("*.json"))
    assert len(claim_files) == 1

    queue_doc = json.loads(server.queue_path.read_text(encoding="utf-8"))
    queue_item = queue_doc["queue"][0]
    queue_item["status"] = "awaiting_score"
    queue_item.pop("scoreCaptureWindowKey", None)
    queue_item.pop("scoreCaptureClaimedAt", None)
    server.queue_path.write_text(json.dumps(queue_doc), encoding="utf-8")
    active = json.loads(server.active_path.read_text(encoding="utf-8"))
    active["status"] = "awaiting_score"
    active.pop("score_capture_window_key", None)
    active.pop("score_capture_claim_path", None)
    active.pop("last_snapshot_at", None)
    server.active_path.write_text(json.dumps(active), encoding="utf-8")

    retry_intent = server.queue_status()["runnerStartIntent"]
    retry = server.queue_runner_start({"confirm_real_submit": False, "intent": retry_intent})
    retry_process = server.runners[retry["runner_id"]]["process"]
    assert retry_process.wait(timeout=15) != 0
    retry_stderr = Path(retry["stderr"]).read_text(encoding="utf-8", errors="replace")
    events = [
        json.loads(line)
        for line in (server.submissions / "aicomp_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert "SCORE_CAPTURE_WINDOW_ALREADY_USED" in retry_stderr
    assert sum(event.get("event") == "leaderboard.snapshot_start" for event in events) == 1


def test_capture_start_releases_matching_stale_submit_lease(monkeypatch, tmp_path):
    server = _server(tmp_path)
    server.submissions.mkdir(parents=True)
    item_path = server.submissions / "accepted.zip"
    queue_item = {
        "index": 167,
        "name": item_path.name,
        "path": str(item_path),
        "status": "awaiting_score",
        "submittedAt": "2026-07-13T07:03:52.864Z",
        "acceptedAt": "2026-07-13T07:03:54.430Z",
        "logicalSubmissionId": "logical-167",
        "artifactSha256": "d" * 64,
    }
    server.queue_path.write_text(json.dumps({"schemaVersion": 3, "queue": [queue_item]}), encoding="utf-8")
    server.active_path.write_text(
        json.dumps(
            {
                "status": "awaiting_score",
                "queue_index": 167,
                "logical_submission_id": "logical-167",
                "candidate_sha256": "d" * 64,
                "file": item_path.name,
                "path": str(item_path),
                "submitted_at": "2026-07-13T07:03:52.864Z",
                "accepted_at": "2026-07-13T07:03:54.430Z",
                "capture_start_at": "2026-07-13T08:05:00.000Z",
            }
        ),
        encoding="utf-8",
    )
    server.runner_lease_path.mkdir()
    (server.runner_lease_path / "owner.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "token": "stale-submit-token",
                "pid": 999_999_991,
                "phase": "running",
                "runner_id": "runner-stale-submit",
                "intent": {
                    "action": "submit_candidate",
                    "queueIndex": 167,
                    "candidateSha256": "d" * 64,
                    "logicalSubmissionId": "logical-167",
                    "queueRevision": "sha256:old",
                    "runnerId": "runner-stale-submit",
                    "attemptId": "attempt-stale-submit",
                },
            }
        ),
        encoding="utf-8",
    )
    server.runner_state_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "runners": [
                    {
                        "runner_id": "runner-stale-submit",
                        "pid": 999_999_991,
                        "running": False,
                        "exitCode": None,
                        "attemptId": "attempt-stale-submit",
                        "stdout": str(server.submissions / "stale.stdout.log"),
                        "stderr": str(server.submissions / "stale.stderr.log"),
                        "startedAt": "2026-07-13T07:02:53Z",
                        "mode": "submit-once",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    class FakeProcess:
        pid = 123456

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    intent = server.queue_status()["runnerStartIntent"]
    assert intent["action"] == "capture_score"
    result = server.queue_runner_start({"confirm_real_submit": False, "intent": intent})

    assert result["started"] is True
    assert result["mode"] == "capture-only"
    owner = json.loads((server.runner_lease_path / "owner.json").read_text(encoding="utf-8"))
    assert owner["intent"]["action"] == "capture_score"
    assert owner["pid"] == 123456


def test_capture_start_rejects_mismatched_stale_submit_lease(tmp_path):
    server = _server(tmp_path)
    server.submissions.mkdir(parents=True)
    item_path = server.submissions / "accepted.zip"
    server.queue_path.write_text(
        json.dumps(
            {
                "schemaVersion": 3,
                "queue": [
                    {
                        "index": 167,
                        "name": item_path.name,
                        "path": str(item_path),
                        "status": "awaiting_score",
                        "submittedAt": "2026-07-13T07:03:52.864Z",
                        "acceptedAt": "2026-07-13T07:03:54.430Z",
                        "logicalSubmissionId": "logical-167",
                        "artifactSha256": "d" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    server.active_path.write_text(
        json.dumps(
            {
                "status": "awaiting_score",
                "queue_index": 167,
                "logical_submission_id": "logical-167",
                "candidate_sha256": "d" * 64,
                "accepted_at": "2026-07-13T07:03:54.430Z",
            }
        ),
        encoding="utf-8",
    )
    server.runner_lease_path.mkdir()
    (server.runner_lease_path / "owner.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "token": "stale-submit-token",
                "pid": 999_999_991,
                "phase": "running",
                "runner_id": "runner-stale-submit",
                "intent": {
                    "action": "submit_candidate",
                    "queueIndex": 167,
                    "candidateSha256": "e" * 64,
                    "logicalSubmissionId": "logical-167",
                    "runnerId": "runner-stale-submit",
                    "attemptId": "attempt-stale-submit",
                },
            }
        ),
        encoding="utf-8",
    )
    server.runner_state_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "runners": [
                    {
                        "runner_id": "runner-stale-submit",
                        "pid": 999_999_991,
                        "attemptId": "attempt-stale-submit",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    intent = server.queue_status()["runnerStartIntent"]
    with pytest.raises(ValueError, match="RUNNER_START_RECONCILIATION_REQUIRED"):
        server.queue_runner_start({"confirm_real_submit": False, "intent": intent})


def test_score_missed_with_claim_is_terminal_not_identity_corruption(tmp_path):
    server = _server(tmp_path)
    server.submissions.mkdir(parents=True)
    item_path = server.submissions / "accepted.zip"
    claim_key = "aicomp-score-window-v1|167|logical-167|" + ("d" * 64) + "|2026-07-13T07:03:54.430Z|2026-07-13T08:00:00.000Z"
    server.queue_path.write_text(
        json.dumps(
            {
                "schemaVersion": 3,
                "queue": [
                    {
                        "index": 167,
                        "name": item_path.name,
                        "path": str(item_path),
                        "status": "score_missed",
                        "submittedAt": "2026-07-13T07:03:52.864Z",
                        "acceptedAt": "2026-07-13T07:03:54.430Z",
                        "logicalSubmissionId": "logical-167",
                        "artifactSha256": "d" * 64,
                        "scoreCaptureWindowKey": claim_key,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    server.active_path.write_text(
        json.dumps(
            {
                "status": "score_missed",
                "queue_index": 167,
                "logical_submission_id": "logical-167",
                "candidate_sha256": "d" * 64,
                "file": item_path.name,
                "path": str(item_path),
                "submitted_at": "2026-07-13T07:03:52.864Z",
                "accepted_at": "2026-07-13T07:03:54.430Z",
                "expected_publish_at": "2026-07-13T08:00:00.000Z",
                "score_capture_window_key": claim_key,
                "score_capture_attempts": 1,
            }
        ),
        encoding="utf-8",
    )

    status = server.queue_status()

    assert status["state"] == "score_missed_blocked"
    assert status["runnerStartIntent"] is None
    assert "ACTIVE_NOT_CAPTURE_READY" in status["identityIssues"]
    assert "SCORE_CAPTURE_WINDOW_ALREADY_CLAIMED" in status["identityIssues"]


def test_skip_score_is_an_exact_mcp_control_plane_mutation(tmp_path):
    server = _server(tmp_path)
    server.submissions.mkdir(parents=True)
    item_path = server.submissions / "candidate.zip"
    logical_id = "jinyinsai:aicomp-2026:AIC-2026-58579595:legacy-index-164"
    item = {
        "index": 164,
        "name": item_path.name,
        "path": str(item_path),
        "status": "score_missed",
        "submittedAt": "2026-07-08T22:59:25.505Z",
        "acceptedAt": "2026-07-08T22:59:26.657Z",
        "logicalSubmissionId": logical_id,
        "artifactSha256": "b" * 64,
    }
    server.queue_path.write_text(
        json.dumps({"schemaVersion": 3, "queue": [item]}),
        encoding="utf-8",
    )
    server.active_path.write_text(
        json.dumps(
            {
                "status": "score_missed",
                "queue_index": 164,
                "logical_submission_id": logical_id,
                "candidate_sha256": "b" * 64,
                "file": item_path.name,
                "path": str(item_path),
                "submitted_at": item["submittedAt"],
                "accepted_at": item["acceptedAt"],
            }
        ),
        encoding="utf-8",
    )
    before = server.queue_path.read_bytes()

    with pytest.raises(ValueError, match="SKIP_SELECTOR_IDENTITY_MISMATCH"):
        server.queue_skip_score_wait({"selector": item_path.name, "reason": "manual evidence reviewed"})

    assert server.queue_path.read_bytes() == before
    assert server.active_path.exists()

    result = server.queue_skip_score_wait({"selector": logical_id, "reason": "manual evidence reviewed"})

    queue_item = json.loads(server.queue_path.read_text(encoding="utf-8"))["queue"][0]
    event = json.loads(server.events_path.read_text(encoding="utf-8").splitlines()[-1])
    assert result["ok"] is True
    assert result["queueIndex"] == 164
    assert queue_item["status"] == "skipped"
    assert queue_item["acceptedAt"] == item["acceptedAt"]
    assert not server.active_path.exists()
    assert event["event"] == "score.skipped_manual"
    assert event["logical_submission_id"] == logical_id


def test_failed_result_evidence_is_bound_by_queue_index_not_filename(tmp_path):
    server = _server(tmp_path)
    server.submissions.mkdir(parents=True)
    shared_path = server.submissions / "same.zip"
    server.results_path.write_text(
        "query_time,file_index,file_name,file_path,submitted_at,leaderboard_publish_time,team_rank,team_id,team_name,team_submit_time,score,score_time,snapshot_path\n"
        f'"2026-07-09T02:46:26Z","164","same.zip","{shared_path}","2026-07-08T22:59:25Z","07月09日 10时00分","3","team","name","2026-07-09 06:59:26","78.2713","2026-07-09 10:46:26","snap.json"\n',
        encoding="utf-8",
    )
    queue = [
        {
            "index": 144,
            "name": "same.zip",
            "path": str(shared_path),
            "status": "failed",
            "submittedAt": "",
        },
        {
            "index": 164,
            "name": "same.zip",
            "path": str(shared_path),
            "status": "failed",
            "submittedAt": "2026-07-08T22:59:25Z",
        },
    ]

    failed = server.failed_unscored_items(queue)

    assert [item["index"] for item in failed] == [144]
