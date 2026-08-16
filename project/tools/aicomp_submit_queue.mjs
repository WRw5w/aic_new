import { spawn, spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const ROOT = process.env.AICOMP_ROOT
  ? path.resolve(process.env.AICOMP_ROOT)
  : "D:\\02_Projects\\ML\\jinyinsai";
const SUBMISSIONS = path.join(ROOT, "submissions");
const CDP_SCRIPT = path.join(ROOT, "tools", "aicomp_cdp.mjs");
const MANIFEST_SCRIPT = path.join(ROOT, "tools", "aicomp_manifest.mjs");

const QUEUE_PATH = path.join(SUBMISSIONS, "aicomp_submit_queue.json");
const ACTIVE_PATH = path.join(SUBMISSIONS, "aicomp_active_submission.json");
const LOG_PATH = path.join(SUBMISSIONS, "aicomp_submission_log.csv");
const EVENTS_PATH = path.join(SUBMISSIONS, "aicomp_events.jsonl");
const SNAPSHOT_LOG_PATH = path.join(SUBMISSIONS, "aicomp_leaderboard_snapshots.log");
const SNAPSHOT_DIR = path.join(SUBMISSIONS, "leaderboard_snapshots");
const CAPTURE_CLAIM_DIR = path.join(SUBMISSIONS, "score_capture_claims");
const RESULTS_PATH = path.join(SUBMISSIONS, "aicomp_results.csv");
const STATE_PATH = path.join(SUBMISSIONS, "aicomp_state.md");
const CONTROL_LOCK_PATH = path.join(SUBMISSIONS, "aicomp_control.lock");
const RUNNER_LEASE_PATH = path.join(SUBMISSIONS, "aicomp_runner.lock");

const DEFAULT_CURRENT = path.join(
  SUBMISSIONS,
  "pred_results_5sig_full_v2_tta_balanced.zip",
);
const REFRESH_DELAY_MINUTES = Number(process.env.AICOMP_REFRESH_DELAY_MINUTES || 5);
const HEARTBEAT_INTERVAL_MS = Number(process.env.AICOMP_HEARTBEAT_INTERVAL_MS || 30 * 60 * 1000);
const SUBMIT_TIMEOUT_MS = Number(process.env.AICOMP_SUBMIT_TIMEOUT_MS || 8 * 60 * 1000);
const CDP_COMMAND_TIMEOUT_MS = Number(process.env.AICOMP_CDP_COMMAND_TIMEOUT_MS || 7 * 60 * 1000);
const SCORE_CAPTURE_ATTEMPTS_PER_RUN = 1;

const TEAM_ID = "AIC-2026-58579595";
const TEAM_NAME = "swpu_1";
const BLOCKING_STATUSES = new Set([
  "uploading",
  "submit_not_dispatched",
  "outcome_unknown",
  "accepted",
  "awaiting_score",
  "score_missed",
]);
const ACTIVE_STATUSES = new Set(["awaiting_score", "score_missed"]);
const QUEUE_SCHEMA_VERSION = 3;
const LEGACY_RESULT_COLUMNS = [
  "query_time",
  "file_index",
  "file_name",
  "file_path",
  "submitted_at",
  "leaderboard_publish_time",
  "team_rank",
  "team_id",
  "team_name",
  "team_submit_time",
  "score",
  "score_time",
  "snapshot_path",
];
const RESULT_COLUMNS = [
  ...LEGACY_RESULT_COLUMNS.slice(0, 5),
  "accepted_at",
  ...LEGACY_RESULT_COLUMNS.slice(5),
];

let CURRENT_RUNNER_LEASE = null;

function nowIso() {
  return new Date().toISOString();
}

function csvCell(value) {
  return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

function readJson(filePath, fallback) {
  if (!fs.existsSync(filePath)) return fallback;
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function pidIsRunning(pid) {
  const value = Number(pid);
  if (!Number.isInteger(value) || value <= 0) return false;
  try {
    process.kill(value, 0);
    return true;
  } catch {
    return false;
  }
}

function lockOwnerPath(lockPath) {
  return path.join(lockPath, "owner.json");
}

function removeLockDirectory(lockPath, token = null) {
  if (!fs.existsSync(lockPath)) return true;
  const owner = readJson(lockOwnerPath(lockPath), null);
  if (token !== null && owner?.token !== token) return false;
  for (const entry of fs.readdirSync(lockPath)) {
    if (entry === "owner.json" || (entry.startsWith("owner.json.") && entry.endsWith(".tmp"))) {
      fs.rmSync(path.join(lockPath, entry), { force: true });
      continue;
    }
    throw new Error(`LOCK_DIRECTORY_CONTAINS_UNEXPECTED_FILE:${path.join(lockPath, entry)}`);
  }
  fs.rmdirSync(lockPath);
  return true;
}

function acquireControlLock(controlPath = CONTROL_LOCK_PATH) {
  fs.mkdirSync(path.dirname(controlPath), { recursive: true });
  const token = randomUUID();
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      fs.mkdirSync(controlPath);
      writeAtomic(
        lockOwnerPath(controlPath),
        JSON.stringify({ token, pid: process.pid, acquiredAt: nowIso() }, null, 2) + "\n",
        { requireFence: false },
      );
      return { token, path: controlPath };
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
      const owner = readJson(lockOwnerPath(controlPath), null);
      if (owner && !pidIsRunning(owner.pid)) {
        removeLockDirectory(controlPath, owner.token);
        continue;
      }
      throw new Error("SUBMISSION_CONTROL_BUSY");
    }
  }
  throw new Error("SUBMISSION_CONTROL_BUSY");
}

function releaseControlLock(lock) {
  if (lock) removeLockDirectory(lock.path, lock.token);
}

function acquireRunnerLease(leasePath = RUNNER_LEASE_PATH, controlPath = CONTROL_LOCK_PATH, options = {}) {
  const suppliedToken = String(options.token || "");
  if (suppliedToken) {
    if (!fs.existsSync(leasePath)) throw new Error("RUNNER_LEASE_MISSING");
    const owner = readJson(lockOwnerPath(leasePath), null);
    if (!owner) throw new Error("RUNNER_START_RECONCILIATION_REQUIRED");
    if (owner.token !== suppliedToken) throw new Error("RUNNER_LEASE_TOKEN_MISMATCH");
    const runnerId = String(options.runnerId || "");
    if (!runnerId) throw new Error("RUNNER_ID_REQUIRED_FOR_LEASE_ADOPTION");
    const launchingFromParent = owner.phase === "launching" && Number(owner.pid) === Number(process.ppid);
    const alreadyOwnedBySelf =
      owner.phase === "running" &&
      Number(owner.pid) === process.pid &&
      String(owner.runner_id || "") === runnerId;
    if (!launchingFromParent && !alreadyOwnedBySelf) {
      throw new Error("RUNNER_LEASE_ADOPTION_NOT_AUTHORIZED");
    }
    const adopted = {
      ...owner,
      pid: process.pid,
      phase: "running",
      runner_id: runnerId,
      adoptedAt: nowIso(),
    };
    writeAtomic(lockOwnerPath(leasePath), JSON.stringify(adopted, null, 2) + "\n", { requireFence: false });
    return { ...adopted, path: leasePath };
  }

  const control = acquireControlLock(controlPath);
  try {
    if (fs.existsSync(leasePath)) {
      const owner = readJson(lockOwnerPath(leasePath), null);
      if (!owner) throw new Error("RUNNER_START_RECONCILIATION_REQUIRED");
      if (pidIsRunning(owner.pid)) throw new Error("RUNNER_LEASE_ACTIVE");
      removeLockDirectory(leasePath, owner.token);
    }

    const token = randomUUID();
    fs.mkdirSync(leasePath);
    const owner = {
      schemaVersion: 1,
      token,
      pid: process.pid,
      phase: "running",
      runner_id: options.runnerId || "",
      intent: options.intent || null,
      acquiredAt: nowIso(),
    };
    writeAtomic(lockOwnerPath(leasePath), JSON.stringify(owner, null, 2) + "\n", { requireFence: false });
    return { ...owner, path: leasePath };
  } finally {
    releaseControlLock(control);
  }
}

function releaseRunnerLease(lease, leasePath = RUNNER_LEASE_PATH) {
  if (!lease?.token) return false;
  return removeLockDirectory(leasePath, lease.token);
}

function assertRunnerFence() {
  if (!CURRENT_RUNNER_LEASE) throw new Error("RUNNER_FENCE_REQUIRED");
  const owner = readJson(lockOwnerPath(CURRENT_RUNNER_LEASE.path), null);
  if (
    !owner ||
    owner.token !== CURRENT_RUNNER_LEASE.token ||
    Number(owner.pid) !== process.pid
  ) {
    throw new Error("FENCE_LOST");
  }
}

function writeAtomic(filePath, text, { requireFence = true } = {}) {
  if (requireFence) assertRunnerFence();
  const tmp = `${filePath}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, text, "utf8");
  fs.renameSync(tmp, filePath);
}

function writeJson(filePath, value) {
  writeAtomic(filePath, JSON.stringify(value, null, 2) + "\n");
}

function appendLog(row) {
  assertRunnerFence();
  if (!fs.existsSync(LOG_PATH)) {
    fs.writeFileSync(
      LOG_PATH,
      "time,event,index,name,path,exit_code,note\n",
      "utf8",
    );
  }
  fs.appendFileSync(
    LOG_PATH,
    [
      row.time || nowIso(),
      row.event,
      row.index ?? "",
      row.name ?? "",
      row.path ?? "",
      row.exitCode ?? "",
      row.note ?? "",
    ].map(csvCell).join(",") + "\n",
    "utf8",
  );
}

function appendEvent(event, data = {}) {
  assertRunnerFence();
  const payload = {
    time: nowIso(),
    event,
    ...data,
  };
  fs.appendFileSync(EVENTS_PATH, JSON.stringify(payload) + "\n", "utf8");
  appendLog({
    event,
    index: data.queue_index ?? data.index,
    name: data.file ?? data.name,
    path: data.path,
    exitCode: data.exit_code ?? data.exitCode,
    note: data.note ?? "",
  });
}

function syncManifestQuiet() {
  if (!fs.existsSync(MANIFEST_SCRIPT)) return;
  const result = spawnSync(process.execPath, [MANIFEST_SCRIPT, "sync"], {
    cwd: ROOT,
    env: process.env,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  if (result.status !== 0) {
    appendEvent("manifest.sync_failed", {
      exit_code: result.status,
      note: `${result.stderr || result.stdout || ""}`.slice(-500),
    });
  }
}

function appendResult(row) {
  assertRunnerFence();
  let columns = RESULT_COLUMNS;
  if (!fs.existsSync(RESULTS_PATH)) {
    fs.writeFileSync(RESULTS_PATH, resultHeader(columns) + "\n", "utf8");
  } else {
    const existingText = fs.readFileSync(RESULTS_PATH, "utf8");
    columns = resultColumnsFromText(existingText);
    if (!existingText.trim()) {
      fs.writeFileSync(RESULTS_PATH, resultHeader(columns) + "\n", "utf8");
    } else if (columns.length === LEGACY_RESULT_COLUMNS.length) {
      writeAtomic(RESULTS_PATH, upgradeLegacyResultsText(existingText));
      columns = RESULT_COLUMNS;
    }
  }
  fs.appendFileSync(RESULTS_PATH, serializeResultRow(row, columns) + "\n", "utf8");
  syncManifestQuiet();
}

function parseCsvLine(line) {
  const cells = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (quoted && ch === '"' && line[i + 1] === '"') {
      cell += '"';
      i += 1;
    } else if (ch === '"') {
      quoted = !quoted;
    } else if (ch === "," && !quoted) {
      cells.push(cell);
      cell = "";
    } else {
      cell += ch;
    }
  }
  cells.push(cell);
  return cells;
}

function resultHeader(columns = RESULT_COLUMNS) {
  return columns.join(",");
}

function resultColumnsFromText(text) {
  const headerLine = String(text || "").split(/\r?\n/, 1)[0].replace(/^\uFEFF/, "").trim();
  if (!headerLine) return RESULT_COLUMNS;
  const columns = parseCsvLine(headerLine).map((column) => String(column || "").trim());
  const supported = [LEGACY_RESULT_COLUMNS, RESULT_COLUMNS].some(
    (candidate) => candidate.length === columns.length && candidate.every((column, index) => column === columns[index]),
  );
  if (!supported) {
    throw new Error(`UNSUPPORTED_RESULTS_SCHEMA:${columns.join("|")}`);
  }
  return columns;
}

function serializeResultRow(row, columns = RESULT_COLUMNS) {
  const values = {
    query_time: row.queryTime,
    file_index: row.fileIndex,
    file_name: row.fileName,
    file_path: row.filePath,
    submitted_at: row.submittedAt,
    accepted_at: row.acceptedAt,
    leaderboard_publish_time: row.leaderboardPublishTime,
    team_rank: row.teamRank,
    team_id: row.teamId,
    team_name: row.teamName,
    team_submit_time: row.teamSubmitTime,
    score: row.score,
    score_time: row.scoreTime,
    snapshot_path: row.snapshotPath,
  };
  return columns.map((column) => csvCell(values[column])).join(",");
}

function upgradeLegacyResultsText(text) {
  const lines = String(text || "").split(/\r?\n/).filter(Boolean);
  if (lines.length === 0) return resultHeader(RESULT_COLUMNS) + "\n";
  const columns = resultColumnsFromText(lines[0]);
  if (columns.length === RESULT_COLUMNS.length) {
    return String(text).endsWith("\n") ? String(text) : `${text}\n`;
  }
  const upgradedRows = lines.slice(1).map((line, rowIndex) => {
    const cells = parseCsvLine(line);
    if (cells.length !== LEGACY_RESULT_COLUMNS.length) {
      throw new Error(`MALFORMED_LEGACY_RESULT_ROW:${rowIndex + 2}:${cells.length}`);
    }
    cells.splice(5, 0, "");
    return cells.map(csvCell).join(",");
  });
  return [resultHeader(RESULT_COLUMNS), ...upgradedRows].join("\n") + "\n";
}

function parseResultRows(text) {
  const byIndex = new Map();
  const conflicts = new Set();
  const lines = text.trim().split(/\r?\n/).filter(Boolean);
  if (lines.length === 0) return { byIndex, conflicts };
  const columns = resultColumnsFromText(lines[0]);
  const columnIndex = new Map(columns.map((column, index) => [column, index]));
  const value = (cells, column) => cells[columnIndex.get(column)] || "";
  for (const line of lines.slice(1)) {
    const cells = parseCsvLine(line);
    const fileIndex = String(value(cells, "file_index")).trim();
    const name = value(cells, "file_name");
    if (!fileIndex || !name) continue;
    const result = {
      queryTime: value(cells, "query_time"),
      fileIndex,
      fileName: name,
      filePath: value(cells, "file_path"),
      submittedAt: value(cells, "submitted_at"),
      acceptedAt: value(cells, "accepted_at"),
      leaderboardPublishTime: value(cells, "leaderboard_publish_time"),
      teamRank: value(cells, "team_rank"),
      teamId: value(cells, "team_id"),
      teamName: value(cells, "team_name"),
      teamSubmitTime: value(cells, "team_submit_time"),
      score: value(cells, "score"),
      scoreTime: value(cells, "score_time"),
      snapshotPath: value(cells, "snapshot_path"),
    };
    const existing = byIndex.get(fileIndex);
    if (
      existing &&
      (existing.fileName !== result.fileName ||
        (existing.filePath && result.filePath && path.resolve(existing.filePath) !== path.resolve(result.filePath)) ||
        (existing.submittedAt && result.submittedAt && existing.submittedAt !== result.submittedAt) ||
        (existing.acceptedAt && result.acceptedAt && existing.acceptedAt !== result.acceptedAt))
    ) {
      conflicts.add(fileIndex);
      byIndex.delete(fileIndex);
      continue;
    }
    if (!existing || String(result.queryTime).localeCompare(String(existing.queryTime)) >= 0) {
      byIndex.set(fileIndex, result);
    }
  }
  for (const conflict of conflicts) byIndex.delete(conflict);
  return { byIndex, conflicts };
}

function resultMap() {
  const text = fs.existsSync(RESULTS_PATH) ? fs.readFileSync(RESULTS_PATH, "utf8") : "";
  return parseResultRows(text);
}

function zipFiles() {
  return fs
    .readdirSync(SUBMISSIONS, { withFileTypes: true })
    .filter((d) => d.isFile() && d.name.toLowerCase().endsWith(".zip"))
    .map((d) => {
      const full = path.join(SUBMISSIONS, d.name);
      const st = fs.statSync(full);
      return { name: d.name, path: full, mtimeMs: st.mtimeMs, size: st.size };
    });
}

function inferFamily(name) {
  return path
    .basename(name, ".zip")
    .toLowerCase()
    .replace(/^pred_results_/, "")
    .replace(/_balanced(?:_s[0-9.]+)?$/, "")
    .replace(/_tta$/, "")
    .replace(/_balanced$/, "");
}

function inferVariant(name) {
  return name.toLowerCase().includes("balanced") ? "balanced" : "raw";
}

function inferTier(name) {
  const n = name.toLowerCase();
  if (n.includes("clmixsoup2_tta_balanced")) return "critical";
  if (n.includes("clmixsoup_tta_balanced")) return "critical";
  if (n.includes("cleanlabsoup_tta_balanced")) return "critical";
  if (n.includes("cb05_tta_balanced")) return "critical";
  if (n.includes("balanced")) return "normal";
  return "waste";
}

function tierRank(tier) {
  return { waste: 0, normal: 1, critical: 2 }[tier] ?? 1;
}

function priorityForName(name) {
  const n = name.toLowerCase();
  let score = 0;
  if (n.includes("ortho_clmixsoup2_tta_balanced")) score += 11000;
  else if (n.includes("ortho_clmixsoup_tta_balanced")) score += 10900;
  else if (n.includes("ortho_cleanlabsoup_tta_balanced")) score += 10800;
  else if (n.includes("ortho_cb05_tta_balanced")) score += 10700;
  else if (n.includes("ortho_cb10_tta_balanced")) score += 10600;
  else if (n.includes("ortho_") && n.includes("balanced")) score += 1200;
  else if (n.includes("ortho_")) score += 100;
  if (n.includes("5sig_full_v2")) score += 1000;
  if (n.includes("5sig_full")) score += 800;
  if (n.includes("5sig")) score += 650;
  if (n.includes("soup")) score += 520;
  if (n.includes("swa")) score += 500;
  if (n.includes("c448_dr_rank32_keep90")) score += 450;
  if (n.includes("gce")) score += 400;
  if (n.includes("balanced")) score += 80;
  if (n.includes("tta")) score += 40;
  return score;
}

function normalizeItem(item, index, results = { byIndex: new Map(), conflicts: new Set() }) {
  const itemIndex = Number(item.index) || index;
  const name = item.name || path.basename(item.path || "");
  const pathValue = item.path || path.join(SUBMISSIONS, name);
  let result = results?.byIndex?.get(String(itemIndex));
  let resultIdentityIssue = "";
  if (results?.conflicts?.has(String(itemIndex))) {
    resultIdentityIssue = "RESULT_IDENTITY_CONFLICT";
    result = null;
  }
  if (
    result &&
    (result.fileName !== name ||
      (result.filePath && pathValue && path.resolve(result.filePath) !== path.resolve(pathValue)) ||
      (result.submittedAt && item.submittedAt && result.submittedAt !== item.submittedAt) ||
      (result.acceptedAt && item.acceptedAt && result.acceptedAt !== item.acceptedAt))
  ) {
    resultIdentityIssue = "RESULT_IDENTITY_MISMATCH";
    result = null;
  }
  const status = normalizeStatus(item, Boolean(result));
  let size = item.size || "";
  if (pathValue && fs.existsSync(pathValue)) size = fs.statSync(pathValue).size;
  return {
    ...item,
    index: itemIndex,
    name,
    path: pathValue,
    size,
    priority: Number(item.priority) || priorityForName(name),
    status,
    family: item.family || inferFamily(name),
    variant: item.variant || inferVariant(name),
    tier: item.tier || inferTier(name),
    createdAt: item.createdAt || item.importedAt || item.submittedAt || "",
    submittedAt: item.submittedAt || "",
    acceptedAt: item.acceptedAt || "",
    score: item.score || result?.score || "",
    scoreTime: item.scoreTime || result?.scoreTime || "",
    leaderboardPublishTime: item.leaderboardPublishTime || result?.leaderboardPublishTime || "",
    teamSubmitTime: item.teamSubmitTime || result?.teamSubmitTime || "",
    exitCode: item.exitCode ?? "",
    note: item.note || "",
    resultIdentityIssue: item.resultIdentityIssue || resultIdentityIssue,
  };
}

function normalizeStatus(item, hasScore) {
  const status = item.status || "queued";
  if (status === "pending") return "queued";
  if (status === "submitting") return item.acceptedAt ? "awaiting_score" : "queued";
  if (status === "submitted_current" || status === "awaiting_refresh") return "awaiting_score";
  if (status === "capture_missed") return "score_missed";
  if (status === "done") return hasScore || item.score ? "scored" : "score_missed";
  if (status === "paused_low_value" || status === "paused_duplicate_family") return "paused";
  if (String(status).startsWith("skipped")) return "skipped";
  return status;
}

function buildQueue(currentPath = DEFAULT_CURRENT) {
  const files = zipFiles().sort((a, b) => {
    const p = priorityForName(b.name) - priorityForName(a.name);
    if (p !== 0) return p;
    return b.mtimeMs - a.mtimeMs;
  });
  const deduped = [];
  const seen = new Set();
  for (const f of files) {
    const key = path.resolve(f.path).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(f);
  }
  const queue = deduped.map((f, i) => normalizeItem({
    index: i + 1,
    name: f.name,
    path: f.path,
    size: f.size,
    priority: priorityForName(f.name),
    status: "queued",
    submittedAt: "",
    acceptedAt: "",
    note: currentPath ? "imported without submission receipt; queued only" : "",
  }, i + 1));
  enforceFamilyPauses(queue);
  return queue;
}

function readQueueDoc() {
  const doc = readJson(QUEUE_PATH, null);
  if (!doc) {
    const queue = buildQueue();
    const nextDoc = { updatedAt: nowIso(), schemaVersion: QUEUE_SCHEMA_VERSION, refreshDelayMinutes: REFRESH_DELAY_MINUTES, queue };
    saveQueueDoc(nextDoc);
    appendEvent("queue.created", { note: `items=${queue.length}` });
    return nextDoc;
  }
  const results = resultMap();
  doc.queue = (Array.isArray(doc.queue) ? doc.queue : []).map((item, i) => normalizeItem(item, i + 1, results));
  doc.schemaVersion = QUEUE_SCHEMA_VERSION;
  doc.refreshDelayMinutes = doc.refreshDelayMinutes ?? REFRESH_DELAY_MINUTES;
  enforceFamilyPauses(doc.queue);
  return doc;
}

function saveQueueDoc(doc) {
  doc.updatedAt = nowIso();
  doc.schemaVersion = QUEUE_SCHEMA_VERSION;
  writeJson(QUEUE_PATH, doc);
  syncManifestQuiet();
}

function loadQueue() {
  const doc = readQueueDoc();
  return doc.queue;
}

function saveQueue(queue) {
  const doc = readQueueDoc();
  doc.queue = queue;
  saveQueueDoc(doc);
  writeState(queue, readActive());
}

function enforceFamilyPauses(queue) {
  const bestByFamily = new Map();
  for (const item of queue) {
    if (item.allowDuplicateFamily) continue;
    const rank = tierRank(item.tier);
    const prev = bestByFamily.get(item.family);
    if (!prev || rank > tierRank(prev.tier)) bestByFamily.set(item.family, item);
  }
  for (const item of queue) {
    if (item.allowDuplicateFamily || item.status !== "queued") continue;
    const best = bestByFamily.get(item.family);
    if (!best || best === item) continue;
    if (tierRank(best.tier) <= tierRank(item.tier)) continue;
    item.status = "paused";
    item.pauseReason = "paused_duplicate_family";
    item.note = `${item.note || ""}\npaused: lower-tier duplicate of ${best.name}`.trim().slice(-1500);
  }
}

function readActive() {
  return readJson(ACTIVE_PATH, null);
}

function writeActive(active) {
  assertRunnerFence();
  if (active) writeJson(ACTIVE_PATH, active);
  else if (fs.existsSync(ACTIVE_PATH)) fs.unlinkSync(ACTIVE_PATH);
}

function activeFromQueue(queue) {
  const blocking = queue.filter((item) => BLOCKING_STATUSES.has(item.status));
  if (blocking.length > 1) {
    throw new Error(`MULTIPLE_BLOCKING_QUEUE_ITEMS:${blocking.map((item) => item.index).join(",")}`);
  }
  return blocking[0] || null;
}

function ensureActiveLock(queue) {
  const existing = readActive();
  if (existing && existing.file) {
    if (existing.queue_index === undefined || existing.queue_index === null || existing.queue_index === "") {
      throw new Error("ACTIVE_QUEUE_INDEX_REQUIRED");
    }
    const matches = queue.filter((candidate) => sameItem(candidate, existing));
    if (matches.length !== 1) throw new Error("ACTIVE_QUEUE_INDEX_NOT_UNIQUE");
    const item = matches[0];
    if (!ACTIVE_STATUSES.has(item.status) || item.status !== existing.status) {
      throw new Error(`ACTIVE_QUEUE_STATUS_MISMATCH:${item.status}:${existing.status}`);
    }
    const itemAccepted = String(item.acceptedAt || "");
    const activeAccepted = String(existing.accepted_at || "");
    if (!itemAccepted || !activeAccepted) throw new Error("ACTIVE_ACCEPTED_AT_REQUIRED");
    if (itemAccepted !== activeAccepted) throw new Error("ACTIVE_ACCEPTED_AT_MISMATCH");
    const itemLogicalId = String(item.logicalSubmissionId || "");
    const activeLogicalId = String(existing.logical_submission_id || "");
    if (!itemLogicalId || !activeLogicalId) throw new Error("ACTIVE_LOGICAL_ID_REQUIRED");
    if (itemLogicalId !== activeLogicalId) throw new Error("ACTIVE_LOGICAL_ID_MISMATCH");
    const itemSha256 = itemCandidateSha256(item);
    const activeSha256 = String(existing.candidate_sha256 || "").trim().toLowerCase();
    if (!itemSha256 || !activeSha256) throw new Error("ACTIVE_CANDIDATE_SHA256_REQUIRED");
    if (itemSha256 !== activeSha256) throw new Error("ACTIVE_CANDIDATE_SHA256_MISMATCH");
    return { active: existing, item };
  }
  const item = activeFromQueue(queue);
  if (!item) return { active: null, item: null };
  return { active: null, item, blockingReason: "BLOCKING_QUEUE_ITEM_WITHOUT_ACTIVE_LOCK" };
}

function sameItem(item, active) {
  if (!item || !active) return false;
  if (active.queue_index === undefined || active.queue_index === null || active.queue_index === "") return false;
  return String(item.index) === String(active.queue_index);
}

function buildActiveLock(item, acceptedAt) {
  const receipt = String(acceptedAt || "").trim();
  if (!receipt || Number.isNaN(new Date(receipt).getTime())) {
    throw new Error("ACTIVE_ACCEPTED_AT_REQUIRED");
  }
  const expectedPublishAt = expectedPublishAfter(receipt);
  const logicalSubmissionId = String(item.logicalSubmissionId || "").trim();
  const candidateSha256 = itemCandidateSha256(item);
  if (!logicalSubmissionId) throw new Error("ACTIVE_LOGICAL_ID_REQUIRED");
  if (!candidateSha256) throw new Error("ACTIVE_CANDIDATE_SHA256_REQUIRED");
  return {
    schemaVersion: QUEUE_SCHEMA_VERSION,
    status: "awaiting_score",
    queue_index: item.index,
    logical_submission_id: logicalSubmissionId,
    candidate_sha256: candidateSha256,
    file: item.name,
    path: item.path,
    accepted_at: receipt,
    submitted_at: item.submittedAt || "",
    expected_publish_at: expectedPublishAt.toISOString(),
    capture_start_at: captureStartAfter(receipt).toISOString(),
    score_capture_attempts: 0,
    last_snapshot_at: "",
    source_status: item.status,
  };
}

function parseSnapshotOutput(output) {
  const start = output.indexOf("{");
  const end = output.lastIndexOf("}");
  if (start < 0 || end <= start) return null;
  try {
    return JSON.parse(output.slice(start, end + 1));
  } catch {
    return null;
  }
}

function parseOurResult(snapshot) {
  const text = (snapshot?.text || "").replace(/\s+/g, " ").trim();
  const publish = text.match(/发布时间：(\d{2}月\d{2}日 \d{2}时\d{2}分)/)?.[1] || "";
  const rows = [];
  const rowRe =
    /(?:(\d+)\s+)?(AIC-2026-\d+)\s+(.+?)\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+([0-9.]+)\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})/g;
  let m;
  while ((m = rowRe.exec(text)) !== null) {
    rows.push({
      rank: m[1] || String(rows.length + 1),
      teamId: m[2],
      teamName: m[3].trim(),
      teamSubmitTime: m[4],
      score: m[5],
      scoreTime: m[6],
    });
  }
  const ours = rows.find((r) => r.teamId === TEAM_ID || r.teamName === TEAM_NAME);
  return ours ? { ...ours, leaderboardPublishTime: publish } : { leaderboardPublishTime: publish };
}

function parseSubmitClickedAt(output) {
  return output.match(/^SUBMIT_CLICKED_AT=([^\s]+)\s*$/m)?.[1] || "";
}

function parseSubmitAcceptedAt(output) {
  return output.match(/^SUBMIT_ACCEPTED_AT=([^\s]+)\s*$/m)?.[1] || "";
}

function parseSubmitDispatchState(output) {
  if (/^SUBMIT_(?:CLICKED|ACCEPTED)_AT=[^\s]+/m.test(output)) return "dispatched";
  if (/^SUBMIT_NOT_DISPATCHED=NO_SUBMIT_BUTTON_CLICKED\s*$/m.test(output)) return "not_dispatched";
  return "unknown";
}

function parseChinaLocalTime(value) {
  if (!value) return null;
  const date = new Date(`${value.replace(" ", "T")}+08:00`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function resultFreshness(active, parsed) {
  if (active?.status !== "awaiting_score") {
    return { fresh: false, reason: `ACTIVE_STATUS_NOT_AWAITING_SCORE:${active?.status || ""}` };
  }
  if (!parsed?.score || !parsed?.scoreTime || !parsed?.teamSubmitTime) {
    return { fresh: false, reason: "TEAM_ROW_INCOMPLETE" };
  }
  const teamSubmitAt = parseChinaLocalTime(parsed.teamSubmitTime);
  const scoreAt = parseChinaLocalTime(parsed.scoreTime);
  if (!active.accepted_at) {
    return { fresh: false, reason: "ACTIVE_ACCEPTED_AT_REQUIRED" };
  }
  const acceptedAt = new Date(active.accepted_at);
  if (!teamSubmitAt || Number.isNaN(acceptedAt.getTime())) {
    return { fresh: false, reason: "TEAM_SUBMIT_OR_ACTIVE_TIME_UNPARSEABLE" };
  }
  if (!scoreAt) {
    return { fresh: false, reason: "SCORE_TIME_UNPARSEABLE" };
  }
  const windowMs = 60_000;
  if (teamSubmitAt.getTime() < acceptedAt.getTime() - windowMs) {
    return {
      fresh: false,
      reason: "PUBLIC_LEADERBOARD_PREVIOUS_SUBMISSION_BEFORE_ACTIVE_ACCEPTED_AT",
      acceptedAt: acceptedAt.toISOString(),
      teamSubmitAt: teamSubmitAt.toISOString(),
    };
  }
  if (Math.abs(teamSubmitAt.getTime() - acceptedAt.getTime()) > windowMs) {
    return {
      fresh: false,
      reason: "PUBLIC_LEADERBOARD_SUBMISSION_TIME_OUTSIDE_ACTIVE_WINDOW",
      acceptedAt: acceptedAt.toISOString(),
      teamSubmitAt: teamSubmitAt.toISOString(),
    };
  }
  if (scoreAt.getTime() < teamSubmitAt.getTime()) {
    return {
      fresh: false,
      reason: "PUBLIC_LEADERBOARD_SCORE_TIME_BEFORE_TEAM_SUBMIT_TIME",
      teamSubmitAt: teamSubmitAt.toISOString(),
      scoreAt: scoreAt.toISOString(),
    };
  }
  return {
    fresh: true,
    reason: "TEAM_SUBMIT_TIME_MATCHES_ACTIVE",
    acceptedAt: acceptedAt.toISOString(),
    teamSubmitAt: teamSubmitAt.toISOString(),
    scoreAt: scoreAt.toISOString(),
  };
}

function expectedPublishAfter(acceptedAt) {
  const base = new Date(String(acceptedAt || ""));
  if (Number.isNaN(base.getTime())) throw new Error("ACTIVE_ACCEPTED_AT_REQUIRED");
  const target = new Date(base);
  target.setMinutes(0, 0, 0);
  target.setHours(target.getHours() + 1);
  return target;
}

function captureStartAfter(acceptedAt) {
  const target = expectedPublishAfter(acceptedAt);
  target.setMinutes(target.getMinutes() + REFRESH_DELAY_MINUTES);
  return target;
}

function itemCandidateSha256(item) {
  return String(
    item?.provenanceCandidateSha256 || item?.artifactSha256 || item?.candidateSha256 || "",
  ).trim().toLowerCase();
}

function captureWindowKey(active, item) {
  const queueIndex = String(item?.index ?? "");
  const acceptedAt = new Date(String(active?.accepted_at || ""));
  const logicalSubmissionId = String(item?.logicalSubmissionId || "").trim();
  const candidateSha256 = itemCandidateSha256(item);
  if (!queueIndex || Number.isNaN(acceptedAt.getTime())) throw new Error("CAPTURE_WINDOW_IDENTITY_REQUIRED");
  if (!logicalSubmissionId) throw new Error("ACTIVE_LOGICAL_ID_REQUIRED");
  if (!candidateSha256) throw new Error("ACTIVE_CANDIDATE_SHA256_REQUIRED");
  const publishAt = expectedPublishAfter(acceptedAt.toISOString()).toISOString();
  return [
    "aicomp-score-window-v1",
    queueIndex,
    logicalSubmissionId,
    candidateSha256,
    acceptedAt.toISOString(),
    publishAt,
  ].join("|");
}

function claimScoreCaptureWindow(active, item) {
  assertRunnerFence();
  const key = captureWindowKey(active, item);
  const existingKeys = [active?.score_capture_window_key, item?.scoreCaptureWindowKey]
    .map((value) => String(value || "").trim())
    .filter(Boolean);
  if (existingKeys.length > 0) {
    throw new Error(`SCORE_CAPTURE_WINDOW_ALREADY_CLAIMED:${existingKeys.join(",")}`);
  }
  fs.mkdirSync(CAPTURE_CLAIM_DIR, { recursive: true });
  const digest = createHash("sha256").update(key).digest("hex");
  const claimPath = path.join(CAPTURE_CLAIM_DIR, `${digest}.json`);
  const claimedAt = nowIso();
  const claim = {
    schemaVersion: 1,
    key,
    queueIndex: item.index,
    logicalSubmissionId: item.logicalSubmissionId,
    candidateSha256: itemCandidateSha256(item),
    acceptedAt: active.accepted_at,
    expectedPublishAt: expectedPublishAfter(active.accepted_at).toISOString(),
    claimedAt,
    runnerId: CURRENT_RUNNER_LEASE?.runner_id || "",
  };
  try {
    fs.writeFileSync(claimPath, JSON.stringify(claim, null, 2) + "\n", { encoding: "utf8", flag: "wx" });
  } catch (error) {
    if (error?.code === "EEXIST") throw new Error(`SCORE_CAPTURE_WINDOW_ALREADY_USED:${key}`);
    throw error;
  }
  assertRunnerFence();
  return { ...claim, path: claimPath };
}

function nextQueued(queue) {
  const candidates = queue
    .filter((item) => item.status === "queued")
    .sort((a, b) => {
      const p = Number(b.priority || 0) - Number(a.priority || 0);
      if (p !== 0) return p;
      const ac = a.createdAt ? Date.parse(a.createdAt) : NaN;
      const bc = b.createdAt ? Date.parse(b.createdAt) : NaN;
      if (!Number.isNaN(ac) || !Number.isNaN(bc)) {
        return (Number.isNaN(ac) ? Infinity : ac) - (Number.isNaN(bc) ? Infinity : bc);
      }
      return Number(a.index || 0) - Number(b.index || 0);
    });
  return candidates[0] || null;
}

function sha256File(filePath) {
  const digest = createHash("sha256");
  digest.update(fs.readFileSync(filePath));
  return digest.digest("hex");
}

function validateRunPreconditions(queue, active, mode, expected) {
  const queueIndex = String(expected?.queueIndex ?? "");
  if (!queueIndex) throw new Error("EXPECTED_QUEUE_INDEX_REQUIRED");
  if (mode === "capture-only") {
    if (!active) throw new Error("CAPTURE_ACTIVE_REQUIRED");
    const blocking = activeFromQueue(queue);
    if (!blocking || String(blocking.index) !== queueIndex) throw new Error("CAPTURE_QUEUE_INDEX_MISMATCH");
    if (String(active.queue_index) !== queueIndex) throw new Error("CAPTURE_ACTIVE_INDEX_MISMATCH");
    if (blocking.status !== "awaiting_score" || active.status !== "awaiting_score") {
      throw new Error("CAPTURE_ACTIVE_NOT_READY");
    }
    if (!blocking.acceptedAt || !active.accepted_at || blocking.acceptedAt !== active.accepted_at) {
      throw new Error("CAPTURE_ACCEPTED_AT_MISMATCH");
    }
    if (!expected.acceptedAt || expected.acceptedAt !== active.accepted_at) throw new Error("CAPTURE_CONFIRMATION_STALE");
    const itemLogicalId = String(blocking.logicalSubmissionId || "");
    const activeLogicalId = String(active.logical_submission_id || "");
    const expectedLogicalId = String(expected.logicalSubmissionId || "");
    if (!itemLogicalId || !activeLogicalId || !expectedLogicalId) throw new Error("CAPTURE_LOGICAL_ID_REQUIRED");
    if (itemLogicalId !== activeLogicalId || itemLogicalId !== expectedLogicalId) {
      throw new Error("CAPTURE_LOGICAL_ID_MISMATCH");
    }
    const itemSha256 = itemCandidateSha256(blocking);
    const activeSha256 = String(active.candidate_sha256 || "").trim().toLowerCase();
    const expectedSha256 = String(expected.candidateSha256 || "").trim().toLowerCase();
    if (!itemSha256 || !activeSha256 || !expectedSha256) throw new Error("CAPTURE_CANDIDATE_SHA256_REQUIRED");
    if (itemSha256 !== activeSha256 || itemSha256 !== expectedSha256) {
      throw new Error("CAPTURE_CANDIDATE_SHA256_MISMATCH");
    }
    return blocking;
  }
  if (mode === "submit-once") {
    if (active || queue.some((item) => BLOCKING_STATUSES.has(item.status))) {
      throw new Error("SUBMIT_BLOCKED_BY_ACTIVE");
    }
    const item = nextQueued(queue);
    if (!item || String(item.index) !== queueIndex) throw new Error("SUBMIT_QUEUE_INDEX_MISMATCH");
    if (itemCandidateSha256(item) !== String(expected.candidateSha256 || "").trim().toLowerCase()) {
      throw new Error("SUBMIT_CANDIDATE_SHA256_MISMATCH");
    }
    if (String(item.logicalSubmissionId || "") !== String(expected.logicalSubmissionId || "")) {
      throw new Error("SUBMIT_LOGICAL_ID_MISMATCH");
    }
    return item;
  }
  throw new Error(`RUN_MODE_UNSUPPORTED:${mode || ""}`);
}

function expectedRunIdentityFromEnvironment() {
  return {
    queueIndex: process.env.AICOMP_EXPECTED_QUEUE_INDEX || "",
    candidateSha256: process.env.AICOMP_EXPECTED_CANDIDATE_SHA256 || "",
    logicalSubmissionId: process.env.AICOMP_EXPECTED_LOGICAL_SUBMISSION_ID || "",
    queueRevision: process.env.AICOMP_EXPECTED_QUEUE_REVISION || "",
    acceptedAt: process.env.AICOMP_EXPECTED_ACCEPTED_AT || "",
    attemptId: process.env.AICOMP_SUBMIT_ATTEMPT_ID || "",
    runnerId: process.env.AICOMP_RUNNER_ID || "",
  };
}

function applySubmissionReceipt(item, receipt) {
  item.submittedAt = String(receipt?.clickedAt || "");
  item.acceptedAt = String(receipt?.acceptedAt || "");
  item.exitCode = Number(receipt?.code ?? 0);
  item.submitDispatchState = String(receipt?.dispatchState || "unknown");
  if (item.acceptedAt) {
    item.status = "awaiting_score";
    return "accepted";
  }
  if (item.submitDispatchState === "not_dispatched" && !item.submittedAt && item.exitCode === 5) {
    item.status = "submit_not_dispatched";
    return "not_dispatched";
  }
  item.status = "outcome_unknown";
  return "outcome_unknown";
}

function recoverStaleUploadingItem(item, recoveredAt = nowIso()) {
  if (item.status !== "uploading") return "unchanged";
  if (item.acceptedAt) {
    item.status = "awaiting_score";
    return "accepted";
  }
  item.status = "outcome_unknown";
  item.note = `${item.note || ""}\ncommit outcome unknown after interrupted upload at ${recoveredAt}`.trim().slice(-1500);
  return "outcome_unknown";
}

function selectionReason(item) {
  if (!item) return "no queued item";
  return `selected by priority desc, createdAt asc: priority=${item.priority} createdAt=${item.createdAt || "index-order"} family=${item.family} tier=${item.tier}`;
}

function runChild(args, options = {}) {
  const timeoutMs = options.timeoutMs || CDP_COMMAND_TIMEOUT_MS;
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [CDP_SCRIPT, ...args], {
      cwd: ROOT,
      env: options.env || process.env,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let output = "";
    let settled = false;
    const finish = (code, extra = "") => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ code, output: output + extra });
    };
    const timer = setTimeout(() => {
      const note = `\nCHILD_TIMEOUT_AFTER_MS=${timeoutMs}\n`;
      output += note;
      process.stderr.write(note);
      child.kill("SIGTERM");
      setTimeout(() => {
        if (!settled) child.kill("SIGKILL");
      }, 5000).unref();
      finish(124);
    }, timeoutMs);
    child.stdout.on("data", (d) => {
      const s = d.toString();
      output += s;
      process.stdout.write(s);
    });
    child.stderr.on("data", (d) => {
      const s = d.toString();
      output += s;
      process.stderr.write(s);
    });
    child.on("exit", (code) => finish(code ?? 0));
    child.on("error", (err) => finish(125, `\nCHILD_ERROR=${String(err)}\n`));
  });
}

function runSubmit(item) {
  assertRunnerFence();
  return runChild(["submit-one", item.path], {
    timeoutMs: SUBMIT_TIMEOUT_MS,
    env: {
      ...process.env,
      AICOMP_SUBMIT_ATTEMPT_ID: String(item.attemptId || ""),
      AICOMP_POST_UPLOAD_DELAY_MS: process.env.AICOMP_POST_UPLOAD_DELAY_MS || "45000",
      AICOMP_POST_SUBMIT_DELAY_MS: process.env.AICOMP_POST_SUBMIT_DELAY_MS || "45000",
    },
  });
}

function runCdpCommand(args) {
  return runChild(args, { timeoutMs: CDP_COMMAND_TIMEOUT_MS });
}

async function waitWithHeartbeat(deadline, item) {
  while (Date.now() < deadline.getTime()) {
    const chunkMs = Math.min(HEARTBEAT_INTERVAL_MS, Math.max(0, deadline.getTime() - Date.now()));
    if (chunkMs > 0) await sleep(chunkMs);
    if (Date.now() >= deadline.getTime()) break;
    appendEvent("heartbeat.start", {
      queue_index: item?.index,
      file: item?.name,
      path: item?.path,
      note: `remaining_ms=${Math.max(0, deadline.getTime() - Date.now())}`,
    });
    const result = await runCdpCommand(["heartbeat"]);
    appendEvent(result.code === 0 ? "heartbeat.ok" : "heartbeat.failed", {
      queue_index: item?.index,
      file: item?.name,
      path: item?.path,
      exit_code: result.code,
      note: result.output.slice(-500),
    });
  }
}

async function waitForCaptureStart(active, item) {
  const target = new Date(active.capture_start_at || captureStartAfter(active.accepted_at));
  const waitMs = target.getTime() - Date.now();
  appendEvent(waitMs > 0 ? "score.wait_until_refresh" : "score.refresh_window_ready", {
    queue_index: item?.index,
    file: item?.name,
    path: item?.path,
    note: `capture_start_at=${target.toISOString()} wait_ms=${Math.max(0, waitMs)}`,
  });
  if (waitMs > 0) {
    console.log(`Window lock held by ${active.file}; waiting until ${target.toLocaleString()} for leaderboard capture.`);
    await waitWithHeartbeat(target, item);
  }
}

function saveSnapshotFile(reason, active, result, snapshot, parsed, freshness) {
  assertRunnerFence();
  fs.mkdirSync(SNAPSHOT_DIR, { recursive: true });
  const stamp = nowIso().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
  const safeFile = String(active?.file || "unknown").replace(/[^a-z0-9_.-]+/gi, "_");
  const filePath = path.join(SNAPSHOT_DIR, `${stamp}_${reason}_${safeFile}.json`);
  writeJson(filePath, {
    time: nowIso(),
    reason,
    active,
    cdpExitCode: result.code,
    rawOutputTail: result.output.slice(-4000),
    snapshot,
    parsed,
    freshness,
  });
  return filePath;
}

async function snapshotLeaderboard(reason, active, item) {
  appendEvent("leaderboard.snapshot_start", {
    queue_index: item?.index,
    file: item?.name,
    path: item?.path,
    note: reason,
  });
  const result = await runCdpCommand(["leaderboard"]);
  assertRunnerFence();
  fs.appendFileSync(
    SNAPSHOT_LOG_PATH,
    `\n===== ${nowIso()} ${reason} ${item?.name || ""} exit=${result.code} =====\n${result.output}\n`,
    "utf8",
  );
  const snapshot = parseSnapshotOutput(result.output);
  const parsed = result.code === 0 && snapshot ? parseOurResult(snapshot) : {};
  const freshness = result.code === 0 && snapshot
    ? resultFreshness(active, parsed)
    : { fresh: false, reason: `LEADERBOARD_COMMAND_FAILED:${result.code}` };
  const fresh = freshness.fresh;
  const snapshotPath = saveSnapshotFile(reason, active, result, snapshot, parsed, freshness);
  appendEvent(fresh ? "leaderboard.snapshot_fresh" : "leaderboard.snapshot_stale", {
    queue_index: item?.index,
    file: item?.name,
    path: item?.path,
    exit_code: result.code,
    snapshot_path: snapshotPath,
    note: `fresh=${fresh} reason=${freshness.reason} publish=${parsed.leaderboardPublishTime || ""} team_submit=${parsed.teamSubmitTime || ""} score=${parsed.score || ""}`,
  });
  return { ok: fresh, code: result.code, parsed, snapshot, snapshotPath, freshness };
}

async function captureScore(queue, active, item) {
  if (active.status === "score_missed") {
    console.log(`Active submission ${active.file} is score_missed. Manual skip-score is required before any new submit.`);
    return false;
  }

  await waitForCaptureStart(active, item);
  for (let attempt = 1; attempt <= SCORE_CAPTURE_ATTEMPTS_PER_RUN; attempt += 1) {
    const claim = claimScoreCaptureWindow(active, item);
    active.score_capture_attempts = Number(active.score_capture_attempts || 0) + 1;
    active.last_snapshot_at = claim.claimedAt;
    active.score_capture_window_key = claim.key;
    active.score_capture_claim_path = claim.path;
    item.scoreCaptureWindowKey = claim.key;
    item.scoreCaptureClaimedAt = claim.claimedAt;
    writeActive(active);
    saveQueue(queue);
    writeState(queue, active);

    const shot = await snapshotLeaderboard(`score_attempt_${attempt}`, active, item);
    if (shot.ok) {
      item.status = "scored";
      item.score = shot.parsed.score;
      item.scoreTime = shot.parsed.scoreTime || nowIso();
      item.leaderboardPublishTime = shot.parsed.leaderboardPublishTime || "";
      item.teamSubmitTime = shot.parsed.teamSubmitTime || "";
      item.nextRefreshAnchor = "";
      item.note = `${item.note || ""}\nscored ${shot.parsed.score} at ${nowIso()}`.trim().slice(-1500);
      appendResult({
        queryTime: shot.snapshot?.time || nowIso(),
        fileIndex: item.index,
        fileName: item.name,
        filePath: item.path,
        submittedAt: item.submittedAt,
        acceptedAt: item.acceptedAt,
        leaderboardPublishTime: shot.parsed.leaderboardPublishTime ?? "",
        teamRank: shot.parsed.rank ?? "",
        teamId: shot.parsed.teamId ?? "",
        teamName: shot.parsed.teamName ?? "",
        teamSubmitTime: shot.parsed.teamSubmitTime ?? "",
        score: shot.parsed.score ?? "",
        scoreTime: shot.parsed.scoreTime ?? "",
        snapshotPath: shot.snapshotPath,
      });
      appendEvent("score.captured", {
        queue_index: item.index,
        file: item.name,
        path: item.path,
        score: shot.parsed.score,
        leaderboard_publish_time: shot.parsed.leaderboardPublishTime,
      });
      writeActive(null);
      saveQueue(queue);
      return true;
    }

    item.note = `score not fresh yet; reason=${shot.freshness?.reason || ""} last_team_submit=${shot.parsed?.teamSubmitTime || ""} last_score=${shot.parsed?.score || ""}`;
    saveQueue(queue);
  }

  item.status = "score_missed";
  item.note = `${item.note || ""}\nscore capture exhausted; active lock retained, run skip-score only after manual decision`.trim().slice(-1500);
  active.status = "score_missed";
  active.note = item.note;
  writeActive(active);
  saveQueue(queue);
  appendEvent("score.missed_blocking", {
    queue_index: item.index,
    file: item.name,
    path: item.path,
    note: "active lock retained; no further automatic submissions",
  });
  return false;
}

function beginSubmissionAttempt(item, attemptId, runnerId, startedAt = nowIso()) {
  if (!attemptId || !runnerId) throw new Error("SUBMIT_ATTEMPT_BINDING_REQUIRED");
  item.status = "uploading";
  item.attemptId = attemptId;
  item.runnerId = runnerId;
  item.submitStartedAt = startedAt;
  item.submittedAt = "";
  item.acceptedAt = "";
  item.exitCode = "";
  item.submitDispatchState = "pending";
  item.nextRefreshAnchor = "";
  item.note = "";
  return item;
}

async function submitNext(queue, item) {
  const attemptId = String(process.env.AICOMP_SUBMIT_ATTEMPT_ID || "");
  const runnerId = String(process.env.AICOMP_RUNNER_ID || "");
  beginSubmissionAttempt(item, attemptId, runnerId);
  saveQueue(queue);
  appendEvent("submit.start", {
    queue_index: item.index,
    attempt_id: item.attemptId,
    logical_submission_id: item.logicalSubmissionId || "",
    candidate_sha256: item.provenanceCandidateSha256 || "",
    file: item.name,
    path: item.path,
    note: selectionReason(item),
  });

  const result = await runSubmit(item);
  const clickedAt = parseSubmitClickedAt(result.output);
  const acceptedAt = parseSubmitAcceptedAt(result.output);
  const dispatchState = parseSubmitDispatchState(result.output);
  const receiptState = applySubmissionReceipt(item, { code: result.code, clickedAt, acceptedAt, dispatchState });
  item.note = result.output.slice(-1000);

  if (receiptState === "not_dispatched") {
    saveQueue(queue);
    appendEvent("submit.not_dispatched", {
      queue_index: item.index,
      attempt_id: item.attemptId,
      runner_id: item.runnerId,
      logical_submission_id: item.logicalSubmissionId || "",
      candidate_sha256: itemCandidateSha256(item),
      file: item.name,
      path: item.path,
      exit_code: result.code,
      note: "CDP emitted an explicit no-click marker; exact reconciliation is required before any retry",
    });
    return null;
  }

  if (receiptState === "outcome_unknown") {
    saveQueue(queue);
    appendEvent("submit.outcome_unknown_blocking", {
      queue_index: item.index,
      attempt_id: item.attemptId,
      file: item.name,
      path: item.path,
      exit_code: result.code,
      note: `no platform acceptance receipt; clicked_at=${clickedAt || ""}; automatic retry forbidden`,
    });
    return null;
  }

  const active = buildActiveLock(item, item.acceptedAt);
  writeActive(active);
  saveQueue(queue);
  appendEvent("submit.accepted", {
    queue_index: item.index,
    attempt_id: item.attemptId,
    logical_submission_id: item.logicalSubmissionId || "",
    candidate_sha256: item.provenanceCandidateSha256 || "",
    file: item.name,
    path: item.path,
    accepted_at: acceptedAt,
    submitted_at: item.submittedAt,
    expected_publish: active.expected_publish_at,
  });
  return active;
}

function recoverStaleUploading(queue) {
  for (const item of queue) {
    if (item.status !== "uploading") continue;
    const transition = recoverStaleUploadingItem(item);
    appendEvent(transition === "accepted" ? "uploading.accepted_recovered" : "uploading.outcome_unknown_blocking", {
      queue_index: item.index,
      file: item.name,
      path: item.path,
      note: transition === "accepted"
        ? "accepted receipt was already durable; active lock must be reconciled"
        : "interrupted upload has an unknown remote commit outcome; automatic retry forbidden",
    });
  }
}

function writeState(queue, active = readActive()) {
  const next = nextQueued(queue);
  const maxRecordedScored = queue
    .filter((item) => item.status === "scored" && item.score !== "")
    .sort((a, b) => Number(b.score) - Number(a.score))[0];
  const recentScored = queue
    .filter((item) => item.status === "scored")
    .sort((a, b) => String(b.scoreTime || b.acceptedAt || "").localeCompare(String(a.scoreTime || a.acceptedAt || "")))[0];
  const counts = {};
  for (const item of queue) counts[item.status] = (counts[item.status] || 0) + 1;
  const lines = [
    "# AICOMP State",
    "",
    `Updated: ${nowIso()}`,
    `Window lock: ${active ? "locked" : "open"}`,
    "",
    "## Active",
    active
      ? `- ${active.file} | status=${active.status} | accepted=${active.accepted_at || ""} | capture_start=${active.capture_start_at || ""} | attempts=${active.score_capture_attempts || 0}`
      : "- none",
    "",
    "## Next",
    next ? `- ${next.name} | priority=${next.priority} | family=${next.family} | tier=${next.tier}` : "- none",
    "",
    "## Recent",
    recentScored ? `- last scored: ${recentScored.name} score=${recentScored.score || ""}` : "- last scored: none",
    maxRecordedScored
      ? `- max recorded queue score (local history, not AICOMP public latest): ${maxRecordedScored.name} score=${maxRecordedScored.score}`
      : "- max recorded queue score (local history, not AICOMP public latest): none",
    "",
    "## Counts",
    `- ${Object.entries(counts).map(([k, v]) => `${k}=${v}`).join(" ")}`,
    "",
    "## Next Action",
    active
      ? `- capture score for ${active.file} at/after ${active.capture_start_at || active.expected_publish_at || ""}`
      : next
        ? `- submit ${next.name} (${selectionReason(next)})`
        : "- idle",
    "",
  ];
  writeAtomic(STATE_PATH, lines.join("\n"));
}

function printPlan(queue) {
  const { active } = ensureActiveLock(queue);
  writeState(queue, active);
  for (const item of queue) {
    console.log(
      `${String(item.index).padStart(2, "0")} ${String(item.status).padEnd(16)} p=${String(item.priority).padStart(5, " ")} ${item.name}`,
    );
  }
  const next = nextQueued(queue);
  console.log("");
  console.log(`active: ${active ? `${active.file} (${active.status})` : "none"}`);
  console.log(`next:   ${next ? `${next.name} - ${selectionReason(next)}` : "none"}`);
  console.log(`queue:  ${QUEUE_PATH}`);
  console.log(`active: ${ACTIVE_PATH}`);
  console.log(`state:  ${STATE_PATH}`);
}

async function runQueue() {
  const mode = process.env.AICOMP_RUN_MODE || "";
  if (!new Set(["submit-once", "capture-only"]).has(mode)) {
    throw new Error("AICOMP_RUN_MODE_REQUIRED: use the MCP-bound runner intent");
  }
  const suppliedLeaseToken = process.env.AICOMP_RUNNER_LEASE_TOKEN || "";
  if (!suppliedLeaseToken) {
    throw new Error("MCP_RUNNER_LEASE_TOKEN_REQUIRED");
  }
  const expected = expectedRunIdentityFromEnvironment();
  const lease = acquireRunnerLease(RUNNER_LEASE_PATH, CONTROL_LOCK_PATH, {
    token: suppliedLeaseToken,
    runnerId: process.env.AICOMP_RUNNER_ID || "",
  });
  CURRENT_RUNNER_LEASE = lease;
  try {
    const expectedAction = mode === "submit-once" ? "submit_candidate" : "capture_score";
    if (lease.intent) {
      if (lease.intent.action !== expectedAction) throw new Error("RUNNER_LEASE_ACTION_MISMATCH");
      if (String(lease.intent.queueIndex) !== String(expected.queueIndex)) throw new Error("RUNNER_LEASE_INDEX_MISMATCH");
      if (String(lease.intent.queueRevision || "") !== String(expected.queueRevision || "")) {
        throw new Error("RUNNER_LEASE_REVISION_MISMATCH");
      }
      if (String(lease.intent.candidateSha256 || "") !== String(expected.candidateSha256 || "")) {
        throw new Error("RUNNER_LEASE_SHA256_MISMATCH");
      }
      if (String(lease.intent.logicalSubmissionId || "") !== String(expected.logicalSubmissionId || "")) {
        throw new Error("RUNNER_LEASE_LOGICAL_ID_MISMATCH");
      }
      if (mode === "submit-once") {
        if (!expected.attemptId || String(lease.intent.attemptId || "") !== String(expected.attemptId)) {
          throw new Error("RUNNER_LEASE_ATTEMPT_ID_MISMATCH");
        }
        if (!expected.runnerId || String(lease.intent.runnerId || "") !== String(expected.runnerId)) {
          throw new Error("RUNNER_LEASE_RUNNER_ID_MISMATCH");
        }
      }
    }

    const queue = loadQueue();
    recoverStaleUploading(queue);
    saveQueue(queue);
    const active = readActive();
    const item = validateRunPreconditions(queue, active, mode, expected);
    writeState(queue, active);
    appendEvent("runner.started", {
      queue_index: item.index,
      file: item.name,
      path: item.path,
      logical_submission_id: item.logicalSubmissionId || "",
      candidate_sha256: item.provenanceCandidateSha256 || "",
      note: `mode=${mode} refresh_delay=${REFRESH_DELAY_MINUTES}min heartbeat_ms=${HEARTBEAT_INTERVAL_MS}`,
    });

    if (mode === "capture-only") {
      const captured = await captureScore(queue, active, item);
      if (!captured) throw new Error("CAPTURE_NOT_COMPLETED");
    } else {
      if (!item.path || !fs.existsSync(item.path)) throw new Error("SUBMIT_CANDIDATE_FILE_MISSING");
      const actualSha256 = sha256File(item.path);
      if (actualSha256 !== expected.candidateSha256) {
        throw new Error(`SUBMIT_CANDIDATE_BYTES_CHANGED:${actualSha256}`);
      }
      console.log(`Submitting exactly one bound candidate: ${item.name}`);
      console.log(selectionReason(item));
      const submittedActive = await submitNext(queue, item);
      if (!submittedActive) {
        if (item.status === "submit_not_dispatched") {
          throw new Error("SUBMIT_NOT_DISPATCHED_RECONCILIATION_REQUIRED");
        }
        throw new Error("SUBMIT_OUTCOME_UNKNOWN_BLOCKING");
      }
      console.log(
        `Submission accepted; capture deferred to a separate capture-only runner at ${submittedActive.capture_start_at || submittedActive.expected_publish_at}.`,
      );
      appendEvent("submit.accepted_capture_deferred", {
        queue_index: item.index,
        attempt_id: item.attemptId || "",
        runner_id: CURRENT_RUNNER_LEASE?.runner_id || process.env.AICOMP_RUNNER_ID || "",
        logical_submission_id: item.logicalSubmissionId || "",
        candidate_sha256: itemCandidateSha256(item),
        accepted_at: submittedActive.accepted_at || "",
        expected_publish: submittedActive.expected_publish_at || "",
        capture_start_at: submittedActive.capture_start_at || "",
        note: "submit-once exits after durable acceptance; capture-only runner owns the later leaderboard window",
      });
    }

    writeState(queue, readActive());
    appendEvent("runner.finished", { queue_index: item.index, file: item.name, note: `mode=${mode}` });
  } catch (error) {
    try {
      appendEvent("runner.blocked", { note: String(error?.message || error) });
    } catch {
      // A lost fence deliberately prevents the stale runner from writing even its failure event.
    }
    throw error;
  } finally {
    releaseRunnerLease(lease, RUNNER_LEASE_PATH);
    CURRENT_RUNNER_LEASE = null;
  }
}

function mainPlan(args) {
  const forceReset = args.includes("--reset");
  if (forceReset || !fs.existsSync(QUEUE_PATH)) {
    throw new Error("DIRECT_QUEUE_MUTATION_DISABLED_USE_JINYINSAI_SUBMIT_MCP");
  }
  const queue = readQueueDoc().queue;
  console.log("Read-only queue plan. All queue mutations require the jinyinsai-submit MCP control plane.");
  printPlan(queue);
}

export {
  acquireRunnerLease,
  activeFromQueue,
  applySubmissionReceipt,
  beginSubmissionAttempt,
  buildActiveLock,
  captureWindowKey,
  normalizeItem,
  parseChinaLocalTime,
  parseSubmitDispatchState,
  parseResultRows,
  resultColumnsFromText,
  resultHeader,
  recoverStaleUploadingItem,
  releaseRunnerLease,
  resultFreshness,
  SCORE_CAPTURE_ATTEMPTS_PER_RUN,
  sameItem,
  serializeResultRow,
  upgradeLegacyResultsText,
  validateRunPreconditions,
};

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const cmd = process.argv[2] || "plan";
  if (cmd === "plan") {
    mainPlan(process.argv.slice(3));
  } else if (cmd === "run") {
    await runQueue();
  } else if (cmd === "sync" || cmd === "skip-score") {
    console.error("DIRECT_QUEUE_MUTATION_DISABLED_USE_JINYINSAI_SUBMIT_MCP");
    process.exitCode = 77;
  } else {
    console.error(`Unknown command: ${cmd}`);
    process.exitCode = 1;
  }
}
