// Build a compact, token-safe evidence index for one populated smoke run.

import { createHash } from "node:crypto";
import {
  readFileSync,
  existsSync,
  readdirSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { basename, join, relative, resolve } from "node:path";

const [runDirectory] = process.argv.slice(2);
if (!runDirectory) throw new Error("usage: build_summary.mjs RUN_DIRECTORY");
const projectRoot = process.cwd();
const runRoot = resolve(runDirectory);
const allowedRoot = resolve(projectRoot, "artifacts", "ux-populated-smoke");
if (!runRoot.startsWith(`${allowedRoot}\\`) && !runRoot.startsWith(`${allowedRoot}/`)) {
  throw new Error(`refusing summary output outside ${allowedRoot}`);
}

const readJson = (name) => JSON.parse(readFileSync(join(runRoot, name), "utf8"));
const audit = readJson("browser-audit.json");
const fixture = readJson("fixture-report.json");
const assets = [
  "src/music_mastering_tools/web_assets/index.html",
  "src/music_mastering_tools/web_assets/app.css",
  "src/music_mastering_tools/web_assets/app.js",
];
const hashes = Object.fromEntries(
  assets.map((asset) => [
    asset,
    createHash("sha256").update(readFileSync(resolve(projectRoot, asset))).digest("hex"),
  ]),
);
const checkEntries = Object.entries(audit.checks);
const browserLog = readFileSync(join(runRoot, "browser.log"), "utf8");
const portalLog = readFileSync(join(runRoot, "portal-stderr.log"), "utf8");
const chromeDiagnostics = browserLog
  .split(/\r?\n/)
  .filter((line) => line.includes(":ERROR:"));
const portalIssues = portalLog
  .split(/\r?\n/)
  .filter((line) =>
    /Traceback|Portal request failed|\s(?:4\d\d|5\d\d)\s-|\s(?:ERROR|CRITICAL)\s/.test(line),
  );
const screenshots = readdirSync(runRoot)
  .filter((name) => name.endsWith(".png"))
  .sort();

const summary = {
  kind: "music-mastering-tools-populated-final-browser-audit",
  schema_version: 2,
  run_directory: runRoot,
  generated_at: new Date().toISOString(),
  isolation: {
    workspace: fixture.workspace,
    normal_private_workspace_used: false,
    artifact_root_only: true,
  },
  fixture,
  asset_sha256: hashes,
  commands: [
    ".\\.venv\\Scripts\\python.exe scripts\\populate_portal_smoke.py <RUN_DIRECTORY>",
    ".\\.venv\\Scripts\\python.exe -m music_mastering_tools gui --workspace <RUN_DIRECTORY>\\workspace --no-browser --write-ready <RUN_DIRECTORY>\\portal-ready.json",
    "node scripts\\portal_browser_audit.mjs <BROWSER_PATH> <READY_FILE> <RUN_DIRECTORY>",
    "POST /api/shutdown with X-MMT-Token and application/json body",
  ],
  browser: {
    passed: Boolean(audit.passed),
    check_count: checkEntries.length,
    failed_checks: checkEntries.filter(([, passed]) => !passed).map(([name]) => name),
    runtime_exception_count: audit.runtimeExceptions.length,
    page_error_log_count: audit.browserErrorLogs.length,
    chromium_stderr_diagnostic_count: chromeDiagnostics.length,
    chromium_stderr_diagnostics: chromeDiagnostics,
  },
  targeted_evidence: {
    activity_1280_document_overflow_px: audit.activity1280.documentLayout.horizontalOverflow,
    activity_1280_workspace_overflow_px: audit.activity1280.workspace.horizontalOverflow,
    activity_1280_run_list_overflow_px: audit.activity1280.runList.horizontalOverflow,
    activity_1280_toast_count: audit.activity1280.toast.count,
    activity_1280_toast_scroll: {
      client_height: audit.activity1280.toast.clientHeight,
      scroll_height: audit.activity1280.toast.scrollHeight,
      overflow_y: audit.activity1280.toast.overflowY,
    },
    activity_1280_toasts_within_viewport: audit.activity1280.toast.regionWithinViewport,
    activity_1280_toasts_above_dock: audit.activity1280.toast.aboveAuditionDock,
    dialog_enter_title: audit.renameDialogKeyboard.afterEnter.title,
    dialog_enter_closed: !audit.renameDialogKeyboard.afterEnter.dialogOpen,
    task_reload_operation_id: audit.taskReloadReconstruction.afterNavigation.operationId,
    task_reload_state: audit.taskReloadReconstruction.afterNavigation.state,
    task_reload_target_count: audit.taskReloadReconstruction.afterNavigation.taskRows.length,
    task_reload_statuses: audit.taskReloadReconstruction.afterNavigation.taskRows.map(
      (row) => row.status,
    ),
    task_reload_review_masters_visible:
      audit.taskReloadReconstruction.afterNavigation.reviewMastersVisible,
  },
  portal: {
    graceful_shutdown: portalLog.includes("Portal stopped."),
    logged_issue_count: portalIssues.length,
    logged_issues: portalIssues,
    log: "portal-stderr.log",
  },
  security: {
    launch_token_redacted: true,
    token_leak_file_count: 0,
  },
  visual_review: existsSync(join(runRoot, 'visual-review.json'))
    ? readJson('visual-review.json')
    : { performed: false, notes: ['Inspect the retained screenshots and record visual-review.json.'] },
  responsive_actions: audit.responsiveActions,
  themes: { normal: audit.themeNormal, forced_colors: audit.themeForced },
  screenshots,
};

const textExtensions = new Set([".json", ".log", ".html", ".txt"]);
const stack = [runRoot];
const leaks = [];
while (stack.length) {
  const directory = stack.pop();
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      stack.push(path);
      continue;
    }
    const extension = entry.name.slice(entry.name.lastIndexOf("."));
    if (!textExtensions.has(extension)) continue;
    const text = readFileSync(path, "utf8");
    if (/token=[A-Za-z0-9_-]{32,}/.test(text) || /"token"\s*:\s*"[A-Za-z0-9_-]{32,}"/.test(text)) {
      leaks.push(relative(runRoot, path));
    }
  }
}
summary.security.launch_token_redacted = leaks.length === 0;
summary.security.token_leak_file_count = leaks.length;
writeFileSync(
  join(runRoot, "verification-summary.json"),
  `${JSON.stringify(summary, null, 2)}\n`,
  "utf8",
);
if (leaks.length) throw new Error(`unredacted portal token found in: ${leaks.join(", ")}`);

console.log(
  JSON.stringify({
    run_directory: runRoot,
    passed: summary.browser.passed,
    check_count: summary.browser.check_count,
    failed_checks: summary.browser.failed_checks,
    token_leaks: leaks,
    graceful_shutdown: summary.portal.graceful_shutdown,
  }),
);
