import json
import os
from pathlib import Path
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
QUEUE_SCRIPT = REPO_ROOT / "tools" / "aicomp_submit_queue.mjs"
LEGACY_SIDE_EFFECT_SCRIPTS = [
    "aicomp_apply_guide_queue.mjs",
    "aicomp_enqueue_backlog.mjs",
    "aicomp_goal_watch_until_noon.mjs",
    "aicomp_hourly_acceptance_alarm.mjs",
    "aicomp_import_next_queue.mjs",
    "aicomp_queue_watchdog.mjs",
    "aicomp_reconcile_leaderboard.mjs",
    "aicomp_wait_all_accepted.mjs",
]


def _run_node(script: str) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def test_score_capture_is_hard_limited_to_one_snapshot_per_runner():
    script = f"""
      process.env.AICOMP_SCORE_ATTEMPTS_PER_WINDOW = '99';
      import {{ pathToFileURL }} from 'node:url';
      const mod = await import(pathToFileURL({str(QUEUE_SCRIPT)!r}).href);
      console.log(JSON.stringify({{ attempts: mod.SCORE_CAPTURE_ATTEMPTS_PER_RUN }}));
    """

    assert _run_node(script)["attempts"] == 1


@pytest.mark.parametrize("command", ["sync", "skip-score"])
def test_direct_queue_mutation_cli_is_disabled(command, tmp_path):
    submissions = tmp_path / "submissions"
    submissions.mkdir()
    queue_path = submissions / "aicomp_submit_queue.json"
    active_path = submissions / "aicomp_active_submission.json"
    queue_path.write_text(
        json.dumps({"schemaVersion": 3, "queue": [{"index": 164, "status": "score_missed"}]}),
        encoding="utf-8",
    )
    active_path.write_text(
        json.dumps({"queue_index": 164, "status": "score_missed"}),
        encoding="utf-8",
    )
    queue_before = queue_path.read_bytes()
    active_before = active_path.read_bytes()
    env = {**os.environ, "AICOMP_ROOT": str(tmp_path)}

    result = subprocess.run(
        ["node", str(QUEUE_SCRIPT), command, "164", "manual reason"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )

    assert result.returncode == 77
    assert "DIRECT_QUEUE_MUTATION_DISABLED" in result.stderr
    assert queue_path.read_bytes() == queue_before
    assert active_path.read_bytes() == active_before


@pytest.mark.parametrize("script_name", LEGACY_SIDE_EFFECT_SCRIPTS)
def test_legacy_queue_and_score_side_effect_scripts_are_quarantined(script_name):
    result = subprocess.run(
        ["node", str(REPO_ROOT / "tools" / script_name)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )

    assert result.returncode == 77
    assert "DISABLED_USE_JINYINSAI_SUBMIT_MCP" in result.stderr


def test_results_are_attributed_by_queue_index_not_filename():
    script = f"""
      import {{ pathToFileURL }} from 'node:url';
      const mod = await import(pathToFileURL({str(QUEUE_SCRIPT)!r}).href);
      const csv = [
        'query_time,file_index,file_name,file_path,submitted_at,leaderboard_publish_time,team_rank,team_id,team_name,team_submit_time,score,score_time,snapshot_path',
        '"2026-07-09T02:46:26Z","164","same.zip","D:/submissions/same.zip","2026-07-08T22:59:25Z","07月09日 10时00分","3","AIC-2026-58579595","swpu_1","2026-07-09 06:59:26","78.2713","2026-07-09 10:46:26","snap.json"',
      ].join('\\n');
      const results = mod.parseResultRows(csv);
      const oldItem = mod.normalizeItem({{
        index: 144,
        name: 'same.zip',
        path: 'D:/submissions/same.zip',
        status: 'skipped',
        submittedAt: '',
        acceptedAt: '',
      }}, 144, results);
      const realItem = mod.normalizeItem({{
        index: 164,
        name: 'same.zip',
        path: 'D:/submissions/same.zip',
        status: 'awaiting_score',
        submittedAt: '2026-07-08T22:59:25Z',
        acceptedAt: '2026-07-08T22:59:26Z',
      }}, 164, results);
      console.log(JSON.stringify({{ oldItem, realItem }}));
    """

    result = _run_node(script)

    assert result["oldItem"]["score"] == ""
    assert result["oldItem"]["status"] == "skipped"
    assert result["realItem"]["score"] == "78.2713"


def test_result_serializer_and_parser_round_trip_both_ledger_schemas():
    script = f"""
      import {{ pathToFileURL }} from 'node:url';
      const mod = await import(pathToFileURL({str(QUEUE_SCRIPT)!r}).href);
      const row = {{
        queryTime: '2026-07-09T02:46:26Z',
        fileIndex: 165,
        fileName: 'candidate.zip',
        filePath: 'D:/submissions/candidate.zip',
        submittedAt: '2026-07-08T22:59:25Z',
        acceptedAt: '2026-07-08T22:59:26Z',
        leaderboardPublishTime: '07月09日 10时00分',
        teamRank: 3,
        teamId: 'AIC-2026-58579595',
        teamName: 'swpu_1',
        teamSubmitTime: '2026-07-09 06:59:26',
        score: '78.2713',
        scoreTime: '2026-07-09 10:46:26',
        snapshotPath: 'snap.json',
      }};
      const currentHeader = mod.resultHeader();
      const currentCsv = [currentHeader, mod.serializeResultRow(row)].join('\\n');
      const current = mod.parseResultRows(currentCsv).byIndex.get('165');
      const legacyHeader = 'query_time,file_index,file_name,file_path,submitted_at,leaderboard_publish_time,team_rank,team_id,team_name,team_submit_time,score,score_time,snapshot_path';
      const legacyColumns = mod.resultColumnsFromText(legacyHeader);
      const legacyCsv = [legacyHeader, mod.serializeResultRow(row, legacyColumns)].join('\\n');
      const legacy = mod.parseResultRows(legacyCsv).byIndex.get('165');
      const upgradedCsv = mod.upgradeLegacyResultsText(legacyCsv);
      const appendedRow = {{ ...row, queryTime: '2026-07-09T03:46:26Z', acceptedAt: '2026-07-08T22:59:27Z' }};
      const upgradedWithAppend = upgradedCsv + mod.serializeResultRow(appendedRow) + '\\n';
      const upgraded = mod.parseResultRows(upgradedWithAppend).byIndex.get('165');
      console.log(JSON.stringify({{
        currentHeaderColumns: currentHeader.split(',').length,
        current,
        legacyRowColumns: mod.serializeResultRow(row, legacyColumns).split(',').length,
        legacy,
        upgradedHeaderColumns: upgradedCsv.split(/\\r?\\n/, 1)[0].split(',').length,
        upgraded,
      }}));
    """

    result = _run_node(script)

    assert result["currentHeaderColumns"] == 14
    assert result["current"]["acceptedAt"] == "2026-07-08T22:59:26Z"
    assert result["current"]["leaderboardPublishTime"] == "07月09日 10时00分"
    assert result["current"]["score"] == "78.2713"
    assert result["current"]["snapshotPath"] == "snap.json"
    assert result["legacyRowColumns"] == 13
    assert result["legacy"]["acceptedAt"] == ""
    assert result["legacy"]["leaderboardPublishTime"] == "07月09日 10时00分"
    assert result["legacy"]["score"] == "78.2713"
    assert result["upgradedHeaderColumns"] == 14
    assert result["upgraded"]["acceptedAt"] == "2026-07-08T22:59:27Z"
    assert result["upgraded"]["score"] == "78.2713"


def test_multiple_blocking_items_fail_closed():
    script = f"""
      import {{ pathToFileURL }} from 'node:url';
      const mod = await import(pathToFileURL({str(QUEUE_SCRIPT)!r}).href);
      let error = '';
      try {{
        mod.activeFromQueue([
          {{ index: 144, status: 'awaiting_score', acceptedAt: '2026-07-08T22:00:00Z' }},
          {{ index: 164, status: 'awaiting_score', acceptedAt: '2026-07-08T23:00:00Z' }},
        ]);
      }} catch (caught) {{ error = String(caught.message || caught); }}
      console.log(JSON.stringify({{ error }}));
    """

    result = _run_node(script)

    assert "MULTIPLE_BLOCKING_QUEUE_ITEMS" in result["error"]


def test_active_identity_never_falls_back_to_duplicate_filename():
    script = f"""
      import {{ pathToFileURL }} from 'node:url';
      const mod = await import(pathToFileURL({str(QUEUE_SCRIPT)!r}).href);
      const matches = mod.sameItem(
        {{ index: 144, name: 'same.zip', path: 'D:/same.zip' }},
        {{ queue_index: 164, file: 'same.zip', path: 'D:/same.zip' }},
      );
      console.log(JSON.stringify({{ matches }}));
    """

    result = _run_node(script)

    assert result["matches"] is False


def test_active_lock_requires_platform_acceptance_receipt():
    script = f"""
      import {{ pathToFileURL }} from 'node:url';
      const mod = await import(pathToFileURL({str(QUEUE_SCRIPT)!r}).href);
      let error = '';
      try {{
        mod.buildActiveLock({{ index: 144, name: 'old.zip', submittedAt: '', acceptedAt: '' }}, '');
      }} catch (caught) {{ error = String(caught.message || caught); }}
      const freshness = mod.resultFreshness({{
        status: 'awaiting_score',
        submitted_at: '2026-07-08T22:59:25Z',
        accepted_at: '',
      }}, {{ score: '78.2', teamSubmitTime: '2026-07-09 06:59:25', scoreTime: '2026-07-09 07:05:00' }});
      console.log(JSON.stringify({{ error, freshness }}));
    """

    result = _run_node(script)

    assert "ACTIVE_ACCEPTED_AT_REQUIRED" in result["error"]
    assert result["freshness"]["fresh"] is False
    assert result["freshness"]["reason"] == "ACTIVE_ACCEPTED_AT_REQUIRED"


def test_stale_uploading_becomes_outcome_unknown_not_queued():
    script = f"""
      import {{ pathToFileURL }} from 'node:url';
      const mod = await import(pathToFileURL({str(QUEUE_SCRIPT)!r}).href);
      const item = {{ index: 165, name: 'candidate.zip', status: 'uploading', submittedAt: '', acceptedAt: '' }};
      const transition = mod.recoverStaleUploadingItem(item, '2026-07-10T00:00:00Z');
      console.log(JSON.stringify({{ item, transition }}));
    """

    result = _run_node(script)

    assert result["item"]["status"] == "outcome_unknown"
    assert result["transition"] == "outcome_unknown"


def test_missing_acceptance_marker_never_synthesizes_receipt():
    script = f"""
      import {{ pathToFileURL }} from 'node:url';
      const mod = await import(pathToFileURL({str(QUEUE_SCRIPT)!r}).href);
      const item = {{ index: 165, name: 'candidate.zip', status: 'uploading' }};
      const transition = mod.applySubmissionReceipt(item, {{ code: 0, clickedAt: '', acceptedAt: '' }});
      console.log(JSON.stringify({{ item, transition }}));
    """

    result = _run_node(script)

    assert result["item"]["status"] == "outcome_unknown"
    assert result["item"]["submittedAt"] == ""
    assert result["item"]["acceptedAt"] == ""
    assert result["transition"] == "outcome_unknown"


def test_new_submit_attempt_clears_previous_receipt_classification_fields():
    script = f"""
      import {{ pathToFileURL }} from 'node:url';
      const mod = await import(pathToFileURL({str(QUEUE_SCRIPT)!r}).href);
      const item = {{
        index: 166,
        status: 'queued',
        exitCode: 5,
        submitDispatchState: 'not_dispatched',
        submittedAt: 'old',
        acceptedAt: 'old',
        note: 'old evidence',
      }};
      mod.beginSubmissionAttempt(item, 'attempt-2', 'runner-2', '2026-07-11T00:00:00Z');
      console.log(JSON.stringify(item));
    """

    item = _run_node(script)

    assert item["status"] == "uploading"
    assert item["attemptId"] == "attempt-2"
    assert item["runnerId"] == "runner-2"
    assert item["exitCode"] == ""
    assert item["submitDispatchState"] == "pending"
    assert item["submittedAt"] == ""
    assert item["acceptedAt"] == ""
    assert item["note"] == ""


def test_explicit_no_dispatch_receipt_is_distinct_from_unknown_outcome():
    script = f"""
      import {{ pathToFileURL }} from 'node:url';
      const mod = await import(pathToFileURL({str(QUEUE_SCRIPT)!r}).href);
      const output = [
        'upload ready: {{"ready":true}}',
        'click: {{"clicked":false}}',
        'click: {{"clicked":false}}',
        'click: {{"clicked":false}}',
        'SUBMIT_NOT_DISPATCHED=NO_SUBMIT_BUTTON_CLICKED',
      ].join('\\n');
      const dispatchState = mod.parseSubmitDispatchState(output);
      const item = {{ index: 166, name: 'candidate.zip', status: 'uploading' }};
      const transition = mod.applySubmissionReceipt(item, {{
        code: 5,
        clickedAt: '',
        acceptedAt: '',
        dispatchState,
      }});
      console.log(JSON.stringify({{ item, transition, dispatchState }}));
    """

    result = _run_node(script)

    assert result["dispatchState"] == "not_dispatched"
    assert result["transition"] == "not_dispatched"
    assert result["item"]["status"] == "submit_not_dispatched"
    assert result["item"]["submittedAt"] == ""
    assert result["item"]["acceptedAt"] == ""


def test_no_dispatch_marker_cannot_override_a_click_receipt():
    script = f"""
      import {{ pathToFileURL }} from 'node:url';
      const mod = await import(pathToFileURL({str(QUEUE_SCRIPT)!r}).href);
      const output = [
        'SUBMIT_CLICKED_AT=2026-07-10T14:33:57.592Z',
        'SUBMIT_NOT_DISPATCHED=NO_SUBMIT_BUTTON_CLICKED',
      ].join('\\n');
      console.log(JSON.stringify({{ dispatchState: mod.parseSubmitDispatchState(output) }}));
    """

    result = _run_node(script)

    assert result["dispatchState"] == "dispatched"


def test_runner_lease_is_atomic_and_fenced(tmp_path):
    lease_path = tmp_path / "runner.lock"
    control_path = tmp_path / "control.lock"
    script = f"""
      import {{ pathToFileURL }} from 'node:url';
      const mod = await import(pathToFileURL({str(QUEUE_SCRIPT)!r}).href);
      const first = mod.acquireRunnerLease({str(lease_path)!r}, {str(control_path)!r}, {{ runnerId: 'first' }});
      let secondError = '';
      try {{
        mod.acquireRunnerLease({str(lease_path)!r}, {str(control_path)!r}, {{ runnerId: 'second' }});
      }} catch (caught) {{ secondError = String(caught.message || caught); }}
      mod.releaseRunnerLease(first, {str(lease_path)!r});
      const third = mod.acquireRunnerLease({str(lease_path)!r}, {str(control_path)!r}, {{ runnerId: 'third' }});
      mod.releaseRunnerLease(third, {str(lease_path)!r});
      console.log(JSON.stringify({{ first: first.token, secondError, third: third.token }}));
    """

    result = _run_node(script)

    assert result["first"] != result["third"]
    assert "RUNNER_LEASE_ACTIVE" in result["secondError"]


def test_child_can_adopt_parent_reserved_lease_while_parent_control_lock_exists(tmp_path):
    lease_path = tmp_path / "runner.lock"
    control_path = tmp_path / "control.lock"
    script = f"""
      import fs from 'node:fs';
      import path from 'node:path';
      import {{ pathToFileURL }} from 'node:url';
      const mod = await import(pathToFileURL({str(QUEUE_SCRIPT)!r}).href);
      fs.mkdirSync({str(lease_path)!r});
      fs.writeFileSync(path.join({str(lease_path)!r}, 'owner.json'), JSON.stringify({{
        token: 'parent-token', pid: process.ppid, phase: 'launching', intent: {{ action: 'capture_score' }},
      }}));
      fs.mkdirSync({str(control_path)!r});
      fs.writeFileSync(path.join({str(control_path)!r}, 'owner.json'), JSON.stringify({{
        token: 'control-token', pid: process.pid,
      }}));
      const adopted = mod.acquireRunnerLease(
        {str(lease_path)!r},
        {str(control_path)!r},
        {{ token: 'parent-token', runnerId: 'child' }},
      );
      let stolenError = '';
      try {{
        mod.acquireRunnerLease(
          {str(lease_path)!r},
          {str(control_path)!r},
          {{ token: 'parent-token', runnerId: 'thief' }},
        );
      }} catch (caught) {{ stolenError = String(caught.message || caught); }}
      fs.rmSync({str(control_path)!r}, {{ recursive: true, force: true }});
      mod.releaseRunnerLease(adopted, {str(lease_path)!r});
      console.log(JSON.stringify({{ pid: adopted.pid, phase: adopted.phase, runnerId: adopted.runner_id, stolenError }}));
    """

    result = _run_node(script)

    assert result["phase"] == "running"
    assert result["runnerId"] == "child"
    assert "RUNNER_LEASE_ADOPTION_NOT_AUTHORIZED" in result["stolenError"]


def test_run_modes_cannot_cross_submit_and_capture_boundaries():
    script = f"""
      import {{ pathToFileURL }} from 'node:url';
      const mod = await import(pathToFileURL({str(QUEUE_SCRIPT)!r}).href);
      const item = {{
        index: 165,
        name: 'candidate.zip',
        path: 'D:/candidate.zip',
        status: 'queued',
        provenanceCandidateSha256: 'abc',
        logicalSubmissionId: 'logical-165',
      }};
      let captureError = '';
      try {{
        mod.validateRunPreconditions([item], null, 'capture-only', {{ queueIndex: 165 }});
      }} catch (caught) {{ captureError = String(caught.message || caught); }}
      let submitError = '';
      try {{
        mod.validateRunPreconditions(
          [{{ ...item, status: 'awaiting_score', acceptedAt: '2026-07-08T22:59:26Z' }}],
          {{ queue_index: 165, status: 'awaiting_score', accepted_at: '2026-07-08T22:59:26Z' }},
          'submit-once',
          {{ queueIndex: 165, candidateSha256: 'abc', logicalSubmissionId: 'logical-165' }},
        );
      }} catch (caught) {{ submitError = String(caught.message || caught); }}
      console.log(JSON.stringify({{ captureError, submitError }}));
    """

    result = _run_node(script)

    assert "CAPTURE_ACTIVE_REQUIRED" in result["captureError"]
    assert "SUBMIT_BLOCKED_BY_ACTIVE" in result["submitError"]


def test_capture_requires_index_receipt_logical_id_and_sha_to_match():
    script = f"""
      import {{ pathToFileURL }} from 'node:url';
      const mod = await import(pathToFileURL({str(QUEUE_SCRIPT)!r}).href);
      const item = {{
        index: 165,
        name: 'candidate.zip',
        path: 'D:/candidate.zip',
        status: 'awaiting_score',
        acceptedAt: '2026-07-08T22:59:26Z',
        artifactSha256: 'abc',
        logicalSubmissionId: 'logical-165',
      }};
      const expected = {{
        queueIndex: 165,
        acceptedAt: item.acceptedAt,
        candidateSha256: 'abc',
        logicalSubmissionId: 'logical-165',
      }};
      let logicalError = '';
      try {{
        mod.validateRunPreconditions([item], {{
          queue_index: 165,
          status: 'awaiting_score',
          accepted_at: item.acceptedAt,
          logical_submission_id: 'wrong-logical',
          candidate_sha256: 'abc',
        }}, 'capture-only', expected);
      }} catch (caught) {{ logicalError = String(caught.message || caught); }}
      let shaError = '';
      try {{
        mod.validateRunPreconditions([item], {{
          queue_index: 165,
          status: 'awaiting_score',
          accepted_at: item.acceptedAt,
          logical_submission_id: 'logical-165',
          candidate_sha256: 'wrong-sha',
        }}, 'capture-only', expected);
      }} catch (caught) {{ shaError = String(caught.message || caught); }}
      const matched = mod.validateRunPreconditions([item], {{
        queue_index: 165,
        status: 'awaiting_score',
        accepted_at: item.acceptedAt,
        logical_submission_id: 'logical-165',
        candidate_sha256: 'abc',
      }}, 'capture-only', expected);
      console.log(JSON.stringify({{ logicalError, shaError, matchedIndex: matched.index }}));
    """

    result = _run_node(script)

    assert "CAPTURE_LOGICAL_ID_MISMATCH" in result["logicalError"]
    assert "CAPTURE_CANDIDATE_SHA256_MISMATCH" in result["shaError"]
    assert result["matchedIndex"] == 165
