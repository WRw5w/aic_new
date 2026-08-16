from pathlib import Path
import hashlib
import json
import os
import subprocess
import time


REPO_ROOT = Path(__file__).resolve().parents[1]
QUEUE_SCRIPT = REPO_ROOT / "tools" / "aicomp_submit_queue.mjs"
CDP_SCRIPT = REPO_ROOT / "tools" / "aicomp_cdp.mjs"


def test_queue_runner_blocks_unknown_submit_outcomes_without_retrying():
    source = QUEUE_SCRIPT.read_text(encoding="utf-8")

    assert "submit.outcome_unknown_blocking" in source
    assert "submit.infra_failed_requeued" not in source


def test_cdp_emits_explicit_no_dispatch_marker_before_exit_five():
    source = CDP_SCRIPT.read_text(encoding="utf-8")

    marker = 'console.log("SUBMIT_NOT_DISPATCHED=NO_SUBMIT_BUTTON_CLICKED")'
    marker_index = source.index(marker)
    exit_index = source.index("return 5;", marker_index)

    assert marker_index < exit_index


def test_queue_runner_is_bound_to_one_candidate_per_process():
    source = QUEUE_SCRIPT.read_text(encoding="utf-8")

    assert '"submit-once"' in source
    assert "Submitting exactly one bound candidate" in source
    assert "submit.failed_continue" not in source


def test_submit_once_defers_hourly_capture_to_a_separate_runner():
    source = QUEUE_SCRIPT.read_text(encoding="utf-8")

    start = source.index("const submittedActive = await submitNext(queue, item);")
    end = source.index("writeState(queue, readActive());", start)
    submit_completion_block = source[start:end]

    assert "captureScore" not in submit_completion_block
    assert 'submit.accepted_capture_deferred' in submit_completion_block
    assert "capture_start_at" in submit_completion_block


def test_submit_once_process_exits_after_fake_acceptance_without_sleeping_for_capture(tmp_path):
    submissions = tmp_path / "submissions"
    tools = tmp_path / "tools"
    submissions.mkdir()
    tools.mkdir()

    candidate = tmp_path / "candidate.zip"
    candidate.write_bytes(b"fake candidate bytes")
    candidate_sha256 = hashlib.sha256(candidate.read_bytes()).hexdigest()
    logical_submission_id = "test:logical-submission"
    runner_id = "runner-test-submit-once"
    attempt_id = "attempt-test-submit-once"
    lease_token = "lease-test-submit-once"

    (tools / "aicomp_cdp.mjs").write_text(
        "if (process.argv[2] === 'submit-one') {\n"
        "  console.log('SUBMIT_CLICKED_AT=2026-07-15T04:00:00.000Z');\n"
        "  console.log('SUBMIT_ACCEPTED_AT=2026-07-15T04:00:01.000Z');\n"
        "}\n",
        encoding="utf-8",
    )
    (submissions / "aicomp_submit_queue.json").write_text(
        json.dumps(
            {
                "schemaVersion": 3,
                "queue": [
                    {
                        "index": 1,
                        "name": candidate.name,
                        "path": str(candidate),
                        "status": "queued",
                        "priority": 1,
                        "createdAt": "2026-07-15T03:00:00Z",
                        "family": "fake",
                        "tier": "normal",
                        "provenanceCandidateSha256": candidate_sha256,
                        "logicalSubmissionId": logical_submission_id,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    lease_path = submissions / "aicomp_runner.lock"
    lease_path.mkdir()
    (lease_path / "owner.json").write_text(
        json.dumps(
            {
                "token": lease_token,
                "pid": os.getpid(),
                "phase": "launching",
                "runner_id": runner_id,
            }
        ),
        encoding="utf-8",
    )

    env = {
        **os.environ,
        "AICOMP_ROOT": str(tmp_path),
        "AICOMP_RUN_MODE": "submit-once",
        "AICOMP_RUNNER_LEASE_TOKEN": lease_token,
        "AICOMP_RUNNER_ID": runner_id,
        "AICOMP_SUBMIT_ATTEMPT_ID": attempt_id,
        "AICOMP_EXPECTED_QUEUE_INDEX": "1",
        "AICOMP_EXPECTED_CANDIDATE_SHA256": candidate_sha256,
        "AICOMP_EXPECTED_LOGICAL_SUBMISSION_ID": logical_submission_id,
        "AICOMP_POST_UPLOAD_DELAY_MS": "0",
        "AICOMP_POST_SUBMIT_DELAY_MS": "0",
        "AICOMP_SUBMIT_TIMEOUT_MS": "5000",
    }
    started = time.monotonic()
    result = subprocess.run(
        ["node", str(QUEUE_SCRIPT), "run"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=15,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert elapsed < 8, result.stdout
    events = (submissions / "aicomp_events.jsonl").read_text(encoding="utf-8")
    assert "submit.accepted_capture_deferred" in events
    assert "score.wait_until_refresh" not in events
    queue = json.loads((submissions / "aicomp_submit_queue.json").read_text(encoding="utf-8"))
    assert queue["queue"][0]["status"] == "awaiting_score"
    assert (submissions / "aicomp_active_submission.json").exists()


def test_queue_runner_blocks_stale_uploading_items_as_outcome_unknown():
    source = QUEUE_SCRIPT.read_text(encoding="utf-8")

    assert "recoverStaleUploading" in source
    assert "uploading.outcome_unknown_blocking" in source


def test_queue_runner_does_not_match_duplicate_active_items_by_name_when_index_exists():
    source = QUEUE_SCRIPT.read_text(encoding="utf-8")

    assert "return String(item.index) === String(active.queue_index);" in source
    assert "return item.name === active.file" not in source


def test_queue_runner_records_public_best_stale_reason():
    source = QUEUE_SCRIPT.read_text(encoding="utf-8")

    assert "PUBLIC_LEADERBOARD_PREVIOUS_SUBMISSION_BEFORE_ACTIVE_ACCEPTED_AT" in source
    assert "PUBLIC_LEADERBOARD_SUBMISSION_TIME_OUTSIDE_ACTIVE_WINDOW" in source
    assert "PUBLIC_LEADERBOARD_SCORE_TIME_BEFORE_TEAM_SUBMIT_TIME" in source
    assert "resultFreshness(active, parsed)" in source
    assert "reason=${freshness.reason}" in source


def test_queue_state_summary_does_not_call_local_history_best_public_score():
    source = QUEUE_SCRIPT.read_text(encoding="utf-8")

    assert "max recorded queue score (local history, not AICOMP public latest)" in source
    assert "`- best score:" not in source


def test_queue_runner_result_freshness_rejects_non_matching_leaderboard_times():
    script = f"""
      import {{ pathToFileURL }} from 'node:url';
      const mod = await import(pathToFileURL({str(QUEUE_SCRIPT)!r}).href);
      const active = {{
        status: 'awaiting_score',
        accepted_at: '2026-07-08T09:02:25.665Z',
        submitted_at: '2026-07-08T09:02:23.104Z',
      }};
      const cases = {{
        fresh: mod.resultFreshness(active, {{
          score: '78.1872',
          teamSubmitTime: '2026-07-08 17:02:26',
          scoreTime: '2026-07-08 17:06:03',
        }}),
        later: mod.resultFreshness(active, {{
          score: '78.1872',
          teamSubmitTime: '2026-07-08 17:10:30',
          scoreTime: '2026-07-08 17:16:03',
        }}),
        scoreBeforeSubmit: mod.resultFreshness(active, {{
          score: '78.1872',
          teamSubmitTime: '2026-07-08 17:02:26',
          scoreTime: '2026-07-08 17:01:00',
        }}),
      }};
      console.log(JSON.stringify(cases));
    """
    result = subprocess.run(["node", "--input-type=module", "-e", script], check=True, capture_output=True, text=True)
    cases = json.loads(result.stdout)

    assert cases["fresh"]["fresh"] is True
    assert cases["later"]["fresh"] is False
    assert cases["later"]["reason"] == "PUBLIC_LEADERBOARD_SUBMISSION_TIME_OUTSIDE_ACTIVE_WINDOW"
    assert cases["scoreBeforeSubmit"]["fresh"] is False
    assert cases["scoreBeforeSubmit"]["reason"] == "PUBLIC_LEADERBOARD_SCORE_TIME_BEFORE_TEAM_SUBMIT_TIME"


def test_submit_page_cdp_connection_is_retryable():
    source = CDP_SCRIPT.read_text(encoding="utf-8")

    assert 'const RETRYABLE_PAGE_KINDS = new Set(["submit", "leaderboard"]);' in source
    assert "RETRYABLE_PAGE_KINDS.has(kind)" in source
