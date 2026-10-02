// Exercise the real desktop launcher and Chromium pages without browser packages.
// Node 22 supplies WebSocket/fetch. All user data is synthetic and isolated.
import { spawn, execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { copyFileSync, createWriteStream, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { isAbsolute, join, relative, resolve } from 'node:path';
import { promisify } from 'node:util';

const [mode, targetPath, browserPath, artifactParent, crashFlag] = process.argv.slice(2);
if (!['source', 'frozen'].includes(mode) || !targetPath || !browserPath || !artifactParent) {
  throw new Error('usage: browser_lifecycle_audit.mjs source|frozen TARGET BROWSER ARTIFACT_ROOT crash|no-crash');
}
const allowedRoot = resolve('artifacts', 'browser-lifecycle-smoke');
const parent = resolve(artifactParent);
const parentRelative = relative(allowedRoot, parent);
if (parentRelative.startsWith('..') || isAbsolute(parentRelative)) throw new Error('unsafe artifact root');
const stamp = new Date().toISOString().replace(/[-:.]/g, '');
const runRoot = join(parent, stamp);
mkdirSync(runRoot, { recursive: true });
const unrelatedCwd = join(runRoot, 'Unrelated working directory');
const appData = join(runRoot, 'Isolated user ü, data');
const workspace = join(appData, 'MusicMasteringTools', 'workspace');
mkdirSync(unrelatedCwd);
mkdirSync(appData);
let target = resolve(targetPath);
if (mode === 'frozen') {
  const relocation = join(runRoot, 'Relocated application ü, spaces');
  mkdirSync(relocation);
  target = join(relocation, 'MusicMasteringTools.exe');
  copyFileSync(targetPath, target);
}
const appEnvironment = Object.fromEntries(Object.entries(process.env).map(([key, value]) => [key.toUpperCase(), value]));
appEnvironment.LOCALAPPDATA = appData;
for (const key of ['PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV']) delete appEnvironment[key];
if (mode === 'frozen') appEnvironment.PATH = [join(appEnvironment.SYSTEMROOT, 'System32'), appEnvironment.SYSTEMROOT].join(';');
const execute = promisify(execFile);
const sleep = ms => new Promise(done => setTimeout(done, ms));
const checks = {};
const timings = {};
const details = {};
const tokens = [];
const applications = [];
const browsers = [];
let failure = null;
let currentReady;

function checkpoint(name, passed, evidence) {
  checks[name] = Boolean(passed);
  if (evidence !== undefined) details[name] = evidence;
  console.log(`[browser lifecycle] ${name}: ${passed ? 'PASS' : 'FAIL'}`);
  if (!passed) throw new Error(`Lifecycle check failed: ${name}`);
}

async function waitFor(predicate, timeout = 15000, label = 'condition') {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await predicate();
    if (value) return value;
    await sleep(100);
  }
  throw new Error(`Timed out waiting for ${label}`);
}

async function api(ready, path, body) {
  const token = new URL(ready.url).searchParams.get('token');
  const response = await fetch(ready.origin + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'X-MMT-Token': token, Origin: ready.origin, 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.timeout(10000),
  });
  const value = await response.json();
  if (!response.ok || !value.ok) throw new Error(`API ${path} failed (${response.status})`);
  return value.data;
}

async function snapshotProcesses() {
  const powershell = join(appEnvironment.SYSTEMROOT, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  const result = await execute(powershell, ['-NoProfile', '-NonInteractive', '-Command',
    'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CreationDate | ConvertTo-Json -Compress'],
  { windowsHide: true, maxBuffer: 4 * 1024 * 1024 });
  return JSON.parse(result.stdout);
}

function descendants(processes, rootPid) {
  const found = new Set([rootPid]);
  let added = true;
  while (added) {
    added = false;
    for (const item of processes) {
      if (found.has(item.ParentProcessId) && !found.has(item.ProcessId)) {
        found.add(item.ProcessId);
        added = true;
      }
    }
  }
  return processes.filter(item => found.has(item.ProcessId));
}

async function startApplication(caseName) {
  const caseRoot = join(runRoot, caseName);
  mkdirSync(caseRoot);
  const readyPath = join(caseRoot, 'private-ready.json');
  const arguments_ = ['--no-browser', '--exit-with-browser', '--write-ready', readyPath];
  if (mode === 'source') arguments_.unshift('-m', 'music_mastering_tools.desktop');
  const stdout = createWriteStream(join(caseRoot, 'desktop-stdout.log'));
  const stderr = createWriteStream(join(caseRoot, 'desktop-stderr.log'));
  const child = spawn(target, arguments_, {
    cwd: unrelatedCwd, env: appEnvironment, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout.pipe(stdout);
  child.stderr.pipe(stderr);
  child.on('error', error => { child.launchError = error.message; });
  const record = { child, caseRoot, readyPath, processes: [] };
  applications.push(record);
  const ready = await waitFor(() => {
    if (child.launchError || child.exitCode !== null) throw new Error(`Application startup failed: ${child.launchError || child.exitCode}`);
    if (!existsSync(readyPath)) return null;
    try { return JSON.parse(readFileSync(readyPath, 'utf8')); } catch { return null; }
  }, 120000, 'desktop readiness');
  tokens.push(new URL(ready.url).searchParams.get('token'));
  currentReady = ready;
  record.ready = ready;
  record.processes = descendants(await snapshotProcesses(), child.pid);
  record.pids = record.processes.map(item => item.ProcessId);
  record.bootstrap = await api(ready, '/api/bootstrap');
  checkpoint(`${caseName}: default per-user workspace`, resolve(record.bootstrap.workspace.root) === workspace);
  checkpoint(`${caseName}: browser lifecycle enabled`, record.bootstrap.browser_lifetime?.enabled === true);
  if (mode === 'frozen') checkpoint(`${caseName}: bootloader and child present`, record.processes.length >= 2,
    record.processes.map(item => ({ pid: item.ProcessId, parent_pid: item.ParentProcessId, name: item.Name })));
  return record;
}

async function waitForApplicationExit(record, label, timeout = 20000) {
  const started = Date.now();
  await waitFor(() => record.child.exitCode !== null, timeout, label);
  checkpoint(`${label}: exit code zero`, record.child.exitCode === 0);
  const processes = await snapshotProcesses();
  const survivors = processes.filter(item => record.pids.includes(item.ProcessId));
  checkpoint(`${label}: complete process tree exited`, survivors.length === 0, { tracked_pids: record.pids, surviving_pids: survivors.map(item => item.ProcessId) });
  checkpoint(`${label}: session file removed`, !existsSync(join(workspace, '.desktop-session.json')));
  timings[label] = { shutdown_seconds: (Date.now() - started) / 1000 };
}

class Cdp {
  constructor(url) { this.url = url; this.pending = new Map(); this.events = []; this.nextId = 1; }
  async open() {
    this.socket = new WebSocket(this.url);
    await new Promise((done, failed) => {
      this.socket.addEventListener('open', done, { once: true });
      this.socket.addEventListener('error', failed, { once: true });
    });
    this.socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (!message.id) { this.events.push(message); return; }
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      if (message.error) pending.failed(new Error(`CDP ${pending.method}: ${message.error.message}`));
      else pending.done(message.result);
    });
  }
  call(method, params = {}) {
    const id = this.nextId++;
    return new Promise((done, failed) => {
      const timer = setTimeout(() => { this.pending.delete(id); failed(new Error(`CDP timeout: ${method}`)); }, 15000);
      this.pending.set(id, { method, done, failed, timer });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }
  async evaluate(expression) {
    const result = await this.call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
  }
}

function lifecycleEvents(session) {
  return session.events.filter(event => event.method === 'Network.requestWillBeSent' &&
    event.params.request.url.endsWith('/api/browser-session')).map(event => {
      try { return JSON.parse(event.params.request.postData || '{}'); } catch { return {}; }
    });
}
const openEvents = session => lifecycleEvents(session).filter(event => event.event === 'open');

async function attachPage(browser, targetId) {
  const pages = await (await fetch(`http://127.0.0.1:${browser.port}/json/list`)).json();
  const page = pages.find(item => item.id === targetId);
  if (!page) throw new Error('Browser page target missing');
  const session = new Cdp(page.webSocketDebuggerUrl);
  await session.open();
  await session.call('Page.enable');
  await session.call('Runtime.enable');
  await session.call('Network.enable');
  await session.call('Log.enable');
  browser.sessions.push(session);
  return session;
}

async function navigateReady(session, ready) {
  const before = openEvents(session).length;
  await session.call('Page.navigate', { url: ready.url });
  await waitFor(async () => {
    try { return await session.evaluate("document.body?.dataset.appState === 'ready'"); } catch { return false; }
  }, 30000, 'ready browser page');
  await waitFor(() => openEvents(session).length > before, 15000, 'browser document registration');
  return openEvents(session).at(-1).document_id;
}

async function newPage(browser, ready) {
  const result = await browser.control.call('Target.createTarget', { url: 'about:blank' });
  const session = await attachPage(browser, result.targetId);
  const documentId = await navigateReady(session, ready);
  return { session, targetId: result.targetId, documentId };
}

async function startBrowser(record, name, nativeWindow = false) {
  const profile = join(record.caseRoot, name);
  mkdirSync(profile);
  const browserLog = createWriteStream(join(record.caseRoot, `${name}.log`));
  const child = spawn(browserPath, [
    ...(nativeWindow ? ['--start-minimized'] : ['--headless=new']),
    '--disable-gpu', '--disable-extensions', '--disable-background-networking',
    '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0',
    '--remote-allow-origins=*', `--user-data-dir=${profile}`, 'about:blank',
  ], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
  child.stderr.pipe(browserLog);
  const record_ = { child, profile, sessions: [] };
  browsers.push(record_);
  const portData = await waitFor(() => {
    if (child.exitCode !== null) throw new Error(`Browser exited before DevTools (${child.exitCode})`);
    try { return readFileSync(join(profile, 'DevToolsActivePort'), 'utf8'); } catch { return null; }
  }, 20000, 'browser DevTools');
  const [port, path] = portData.trim().split(/\r?\n/);
  record_.port = Number(port);
  record_.control = new Cdp(`ws://127.0.0.1:${port}${path}`);
  await record_.control.open();
  // Reuse the startup blank tab as the first actual workbench tab. A blank page
  // never registers an application lease and cannot affect server ownership.
  const targets = await record_.control.call('Target.getTargets');
  const initial = targets.targetInfos.find(item => item.type === 'page');
  record_.initialTargetId = initial.targetId;
  record_.initialSession = await attachPage(record_, initial.targetId);
  return record_;
}

function writeTone(path) {
  const frames = 44100;
  const bytes = frames * 4;
  const wav = Buffer.alloc(44 + bytes);
  wav.write('RIFF', 0); wav.writeUInt32LE(bytes + 36, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(2, 22);
  wav.writeUInt32LE(44100, 24); wav.writeUInt32LE(176400, 28); wav.writeUInt16LE(4, 32);
  wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(bytes, 40);
  for (let frame = 0; frame < frames; frame++) {
    const value = Math.round(8000 * Math.sin(2 * Math.PI * 330 * frame / 44100));
    wav.writeInt16LE(value, 44 + frame * 4); wav.writeInt16LE(value, 46 + frame * 4);
  }
  writeFileSync(path, wav);
}

async function closeBrowser(browser) {
  if (browser.child.exitCode !== null) return;
  try { await browser.control.call('Browser.close'); } catch { /* Transport can close before acknowledgement. */ }
  await waitFor(() => browser.child.exitCode !== null, 10000, 'browser shutdown');
}

async function closeNativeWindow(browser) {
  // CDP Browser.close ends Chromium without promising pagehide. Send the
  // ordinary Windows window-close message to this isolated minimized browser
  // instead, so this check covers the same path as its title-bar close button.
  const powershell = join(appEnvironment.SYSTEMROOT, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  const result = await execute(powershell, ['-NoProfile', '-NonInteractive', '-Command',
    `$taskBrowserProcess = Get-Process -Id ${browser.child.pid} -ErrorAction Stop; ` +
    'if ($taskBrowserProcess.MainWindowHandle.ToInt64() -eq 0) { throw "Isolated browser has no native window." }; ' +
    '$taskBrowserProcess.CloseMainWindow()'], { windowsHide: true });
  checkpoint('native minimized browser window accepts close', result.stdout.trim() === 'True');
  await waitFor(() => browser.child.exitCode !== null, 15000, 'native browser window shutdown');
}

async function forceOwnedTree(child) {
  if (child.exitCode !== null) return;
  await execute(join(appEnvironment.SYSTEMROOT, 'System32', 'taskkill.exe'),
    ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true });
}

async function cleanupOwnedSurvivors(record) {
  // A failed bootloader may already have exited while its recorded child lives.
  // Verify PID, name and creation identity before touching any such survivor.
  const processes = await snapshotProcesses();
  for (const original of record.processes) {
    const current = processes.find(item => item.ProcessId === original.ProcessId &&
      item.Name === original.Name && JSON.stringify(item.CreationDate) === JSON.stringify(original.CreationDate));
    if (current) {
      try {
        await execute(join(appEnvironment.SYSTEMROOT, 'System32', 'taskkill.exe'),
          ['/PID', String(current.ProcessId), '/T', '/F'], { windowsHide: true });
      } catch { /* Another owned ancestor may already have removed this process. */ }
    }
  }
}

try {
  const first = await startApplication('tabs-and-refresh');
  const source = join(runRoot, 'Lifecycle synthetic source ü.wav');
  writeTone(source);
  await api(first.ready, '/api/tracks/add', { paths: [source], roles: ['target'] });
  const tracks = await api(first.ready, '/api/tracks');
  const catalogId = first.bootstrap.catalog.catalog_id;
  const trackId = tracks[0].track_id;
  const destination = join(runRoot, 'Remembered deliveries ü');
  mkdirSync(destination);
  const preferences = await api(first.ready, '/api/preferences');
  preferences.default_output_directory = destination;
  await api(first.ready, '/api/preferences', preferences);
  const browser = await startBrowser(first, 'browser-tabs');
  const session = browser.initialSession;
  let documentId = await navigateReady(session, first.ready);
  checkpoint('actual browser registers document', Boolean(documentId));
  await session.call('Page.reload');
  await waitFor(() => openEvents(session).at(-1)?.document_id !== documentId, 20000, 'new refresh identity');
  documentId = openEvents(session).at(-1).document_id;
  await sleep((first.bootstrap.browser_lifetime.close_grace_seconds + 1) * 1000);
  checkpoint('refresh keeps application alive', first.child.exitCode === null && Boolean(await api(first.ready, '/api/bootstrap')));

  // A closed UUID is tombstoned. An active page must recover with a new lease
  // instead of repeatedly reopening the refused identity in a busy RPC loop.
  const refusedId = documentId;
  await api(first.ready, '/api/browser-session', { document_id: refusedId, event: 'close' });
  await waitFor(() => openEvents(session).at(-1)?.document_id !== refusedId, 15000, 'fresh refused-lease identity');
  documentId = openEvents(session).at(-1).document_id;
  const refusedOpen = await api(first.ready, '/api/browser-session', { document_id: refusedId, event: 'open' });
  await sleep((first.bootstrap.browser_lifetime.close_grace_seconds + 1) * 1000);
  checkpoint('active page recovers from tombstoned identity', first.child.exitCode === null &&
    documentId !== refusedId && refusedOpen.registered === false);

  // Use a real beforeunload prompt and decline it, rather than faking pagehide.
  await session.evaluate(`window.__lifecycleBeforeUnload = event => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', window.__lifecycleBeforeUnload); true`);
  await session.call('Input.dispatchMouseEvent', { type: 'mousePressed', x: 25, y: 25, button: 'left', clickCount: 1 });
  await session.call('Input.dispatchMouseEvent', { type: 'mouseReleased', x: 25, y: 25, button: 'left', clickCount: 1 });
  const dialogsBefore = session.events.filter(item => item.method === 'Page.javascriptDialogOpening').length;
  const attempted = session.call('Page.navigate', { url: 'about:blank' });
  await waitFor(() => session.events.filter(item => item.method === 'Page.javascriptDialogOpening').length > dialogsBefore, 10000, 'beforeunload prompt');
  await session.call('Page.handleJavaScriptDialog', { accept: false });
  await attempted;
  await session.evaluate("window.removeEventListener('beforeunload', window.__lifecycleBeforeUnload); true");
  await sleep((first.bootstrap.browser_lifetime.close_grace_seconds + 1) * 1000);
  checkpoint('canceled navigation keeps application alive', first.child.exitCode === null &&
    await session.evaluate("document.body.dataset.appState === 'ready'"));

  const history = await session.call('Page.getNavigationHistory');
  const oldId = documentId;
  await session.call('Page.navigate', { url: 'about:blank' });
  await waitFor(async () => {
    try { return await session.evaluate("location.href === 'about:blank'"); } catch { return false; }
  }, 15000, 'committed temporary navigation');
  await session.call('Page.navigateToHistoryEntry', { entryId: history.entries[history.currentIndex].id });
  await waitFor(async () => {
    try { return await session.evaluate("document.body?.dataset.appState === 'ready'"); } catch { return false; }
  }, 20000, 'history-restored application');
  await waitFor(() => openEvents(session).at(-1)?.document_id !== oldId, 15000, 'fresh restored identity');
  documentId = openEvents(session).at(-1).document_id;
  await api(first.ready, '/api/browser-session', { document_id: oldId, event: 'close' });
  await sleep((first.bootstrap.browser_lifetime.close_grace_seconds + 1) * 1000);
  checkpoint('restored visit survives delayed old close', first.child.exitCode === null && documentId !== oldId);

  const second = await newPage(browser, first.ready);
  checkpoint('second tab has independent identity', second.documentId !== documentId);
  await browser.control.call('Target.activateTarget', { targetId: second.targetId });
  const window = await browser.control.call('Browser.getWindowForTarget', { targetId: browser.initialTargetId });
  await browser.control.call('Browser.setWindowBounds', { windowId: window.windowId, bounds: { windowState: 'minimized' } });
  const waitsBefore = session.events.filter(event => event.method === 'Network.requestWillBeSent' && event.params.request.url.endsWith('/api/browser-session/wait')).length;
  await sleep((first.bootstrap.browser_lifetime.wait_seconds + 2) * 1000);
  const waitsAfter = session.events.filter(event => event.method === 'Network.requestWillBeSent' && event.params.request.url.endsWith('/api/browser-session/wait')).length;
  const visibility = await session.evaluate('({ hidden: document.hidden, visibility: document.visibilityState })');
  const observedWindow = await browser.control.call('Browser.getWindowBounds', { windowId: window.windowId });
  checkpoint('minimized window renews server-held presence', first.child.exitCode === null && waitsAfter > waitsBefore &&
    visibility.hidden && observedWindow.bounds.windowState === 'minimized',
    { ...visibility, window_state: observedWindow.bounds.windowState, long_poll_requests_before: waitsBefore, long_poll_requests_after: waitsAfter });
  await browser.control.call('Browser.setWindowBounds', { windowId: window.windowId, bounds: { windowState: 'normal' } });
  await browser.control.call('Target.closeTarget', { targetId: second.targetId });
  await sleep((first.bootstrap.browser_lifetime.close_grace_seconds + 1) * 1000);
  checkpoint('closing second tab keeps first tab alive', first.child.exitCode === null && Boolean(await api(first.ready, '/api/bootstrap')));
  await browser.control.call('Target.closeTarget', { targetId: browser.initialTargetId });
  await waitForApplicationExit(first, 'last workbench tab closes');
  await closeBrowser(browser);

  const restarted = await startApplication('restart-and-window-close');
  checkpoint('restart retains catalog identity', restarted.bootstrap.catalog.catalog_id === catalogId);
  checkpoint('restart retains registered source', (await api(restarted.ready, '/api/tracks')).some(track => track.track_id === trackId));
  checkpoint('restart retains output preference', (await api(restarted.ready, '/api/preferences')).default_output_directory === destination);
  const nextBrowser = await startBrowser(restarted, 'browser-window', true);
  await navigateReady(nextBrowser.initialSession, restarted.ready);
  const nativeWindow = await nextBrowser.control.call('Browser.getWindowForTarget', { targetId: nextBrowser.initialTargetId });
  await nextBrowser.control.call('Browser.setWindowBounds', { windowId: nativeWindow.windowId, bounds: { windowState: 'minimized' } });
  await closeNativeWindow(nextBrowser);
  await waitForApplicationExit(restarted, 'last browser window closes');

  if (crashFlag === 'crash') {
    const crashed = await startApplication('missing-close-fallback');
    const crashBrowser = await startBrowser(crashed, 'browser-crash');
    await navigateReady(crashBrowser.initialSession, crashed.ready);
    const started = Date.now();
    await forceOwnedTree(crashBrowser.child);
    const limit = (crashed.bootstrap.browser_lifetime.stale_timeout_seconds + crashed.bootstrap.browser_lifetime.wait_seconds + 30) * 1000;
    const progress = setInterval(() => console.log(`[browser lifecycle] Waiting for crash lease expiry (${Math.round((Date.now() - started) / 1000)} s).`), 30000);
    try { await waitForApplicationExit(crashed, 'abrupt browser crash fallback', limit); }
    finally { clearInterval(progress); }
    timings.crash_fallback = { seconds_from_browser_kill: (Date.now() - started) / 1000 };
  }
  for (const browser_ of browsers) {
    const exceptions = browser_.sessions.flatMap(session_ => session_.events.filter(event => event.method === 'Runtime.exceptionThrown'));
    checkpoint(`browser ${relative(runRoot, browser_.profile)} has no runtime exceptions`, exceptions.length === 0);
  }
} catch (error) {
  failure = { name: error.name, message: error.message, stack: error.stack };
  process.exitCode = 1;
} finally {
  for (const browser of browsers) {
    try { await closeBrowser(browser); } catch { try { await forceOwnedTree(browser.child); } catch { /* Original failure retained. */ } }
  }
  for (const record of applications) {
    if (record.child.exitCode === null) {
      try { await api(record.ready || currentReady, '/api/shutdown', {}); } catch { /* Preserve the original failure. */ }
      try { await waitFor(() => record.child.exitCode !== null, 10000, 'cleanup shutdown'); }
      catch { try { await forceOwnedTree(record.child); } catch { /* Original failure retained. */ } }
    }
    try { await cleanupOwnedSurvivors(record); } catch { /* Preserve original failure and process evidence. */ }
  }
  // Redact credentials throughout text artifacts, including private readiness
  // files and browser profile JSON. Never serialize CDP request headers/events.
  const stack = [runRoot];
  let redactedFiles = 0;
  while (stack.length) {
    const directory = stack.pop();
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) { stack.push(path); continue; }
      if (!/\.(?:json|log|txt|html)$/.test(entry.name)) continue;
      try {
        const original = readFileSync(path, 'utf8');
        let contents = original;
        for (const token of tokens) if (token) contents = contents.replaceAll(token, '[REDACTED]');
        if (contents !== original) { writeFileSync(path, contents); redactedFiles++; }
      } catch { /* Chromium profile files may still be settling; they are not publication artifacts. */ }
    }
  }
  const summary = {
    schema_version: 1,
    kind: 'music-mastering-tools-browser-lifecycle-smoke',
    mode, generated_at: new Date().toISOString(),
    passed: failure === null && Object.values(checks).every(Boolean),
    checks, details, timings, failure,
    isolation: { default_local_app_data_used: true, normal_private_workspace_used: false, workspace: relative(runRoot, workspace), frozen_path_contains_python_or_ffmpeg: false },
    credential_redaction: { performed: true, files_redacted: redactedFiles },
    application_sha256: mode === 'frozen' ? createHash('sha256').update(readFileSync(target)).digest('hex') : null,
    asset_sha256: Object.fromEntries(['index.html', 'app.css', 'app.js'].map(name => [name,
      createHash('sha256').update(readFileSync(resolve('src', 'music_mastering_tools', 'web_assets', name))).digest('hex')])),
    evidence: applications.map(record => ({ case: relative(runRoot, record.caseRoot), process_ids: record.pids || [] })),
  };
  const summaryPath = join(runRoot, 'verification-summary.json');
  let serialized = JSON.stringify(summary, null, 2) + '\n';
  for (const token of tokens) if (token) serialized = serialized.replaceAll(token, '[REDACTED]');
  if (tokens.some(token => token && serialized.includes(token))) throw new Error('Credential redaction failed.');
  writeFileSync(summaryPath, serialized);
  const safeSummary = JSON.parse(serialized);
  console.log(JSON.stringify({ passed: safeSummary.passed, check_count: Object.keys(checks).length, summary: summaryPath, failure: safeSummary.failure?.message || null }));
}
