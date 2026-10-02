// Drive a stock Chromium instance through its local DevTools endpoint.
// No browser-automation dependency is needed; Node 22 provides WebSocket.

import { spawn } from "node:child_process";
import { mkdirSync, readFileSync, rmSync, writeFileSync, createWriteStream } from "node:fs";
import { isAbsolute, join, relative, resolve } from "node:path";

const [browserPath, readyPath, outputDirectory] = process.argv.slice(2);
if (!browserPath || !readyPath || !outputDirectory) {
  throw new Error("usage: portal_browser_audit.mjs BROWSER_PATH READY_FILE OUTPUT_DIRECTORY");
}
const { url: launchUrl } = JSON.parse(readFileSync(readyPath, "utf8"));

const outputRoot = resolve(outputDirectory);
const allowedRoot = resolve("artifacts", "ux-populated-smoke");
const outputRelative = relative(allowedRoot, outputRoot);
if (!outputRelative || outputRelative.startsWith("..") || isAbsolute(outputRelative)) {
  throw new Error(`refusing artifact output outside ${allowedRoot}`);
}
mkdirSync(outputRoot, { recursive: true });
const profile = join(outputRoot, "browser-profile");
mkdirSync(profile, { recursive: false });
const browserLog = createWriteStream(join(outputRoot, "browser.log"), { flags: "a" });
const chrome = spawn(
  browserPath,
  [
    "--headless=new",
    "--disable-gpu",
    "--disable-gpu-sandbox",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-sync",
    "--metrics-recording-only",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
    "--remote-debugging-port=0",
    "--remote-allow-origins=*",
    `--user-data-dir=${profile}`,
    "--window-size=1440,1000",
    `${launchUrl.split("#")[0]}#catalog`,
  ],
  { stdio: ["ignore", "ignore", "pipe"] },
);
chrome.stderr.pipe(browserLog);

const sleep = (milliseconds) => new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));

async function waitForFile(path, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      return readFileSync(path, "utf8");
    } catch {
      if (chrome.exitCode !== null) {
        throw new Error(`Chromium exited before DevTools became ready (code ${chrome.exitCode})`);
      }
      await sleep(100);
    }
  }
  throw new Error(`timed out waiting for ${path}`);
}

async function waitForPage(port, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/list`);
      const pages = await response.json();
      const page = pages.find((candidate) => candidate.type === "page");
      if (page?.webSocketDebuggerUrl) return page;
    } catch {
      // The endpoint exists slightly after DevToolsActivePort is materialized.
    }
    await sleep(100);
  }
  throw new Error("timed out waiting for a Chromium page target");
}

class CdpSession {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.events = [];
  }

  async open() {
    await new Promise((resolvePromise, rejectPromise) => {
      this.socket.addEventListener("open", resolvePromise, { once: true });
      this.socket.addEventListener("error", rejectPromise, { once: true });
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(JSON.stringify(message.error)));
        else pending.resolve(message.result);
        return;
      }
      this.events.push(message);
    });
  }

  call(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolvePromise, rejectPromise) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        rejectPromise(new Error(`CDP call timed out: ${method}`));
      }, 15000);
      this.pending.set(id, {
        resolve: (value) => {
          clearTimeout(timeout);
          resolvePromise(value);
        },
        reject: (error) => {
          clearTimeout(timeout);
          rejectPromise(error);
        },
      });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }
}

async function evaluate(session, expression) {
  const result = await session.call("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (result.exceptionDetails) {
    throw new Error(
      result.exceptionDetails.exception?.description ||
        result.exceptionDetails.text ||
        "browser evaluation failed",
    );
  }
  return result.result.value;
}

async function waitForReady(session, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const state = await evaluate(
        session,
        "document.body ? document.body.dataset.appState || null : null",
      );
      if (state === "ready") return;
      if (state === "error") throw new Error("portal page entered data-app-state=error");
    } catch (error) {
      // A deliberate full-page navigation briefly destroys the prior runtime
      // execution context. Retry until the replacement document is available.
      if (String(error).includes("data-app-state=error")) throw error;
    }
    await sleep(150);
  }
  throw new Error("portal page did not reach data-app-state=ready");
}

async function waitForExpression(session, expression, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const value = await evaluate(session, expression);
      if (value) return value;
    } catch {
      // The page may be between runtime contexts during a deliberate reload.
    }
    await sleep(100);
  }
  throw new Error(`timed out waiting for browser expression: ${expression}`);
}

async function setViewport(session, width, height, deviceScaleFactor = 1) {
  await session.call("Emulation.setDeviceMetricsOverride", {
    width,
    height,
    deviceScaleFactor,
    mobile: false,
    screenWidth: width,
    screenHeight: height,
  });
  await sleep(250);
}

async function screenshot(session, name) {
  const result = await session.call("Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: false,
    fromSurface: true,
  });
  writeFileSync(join(outputRoot, name), Buffer.from(result.data, "base64"));
}

async function auditActionReachability(session, tab, selectors) {
  await evaluate(session, `document.getElementById(${JSON.stringify(`tab-${tab}`)}).click(); true`);
  await sleep(200);
  const metrics = [];
  for (const selector of selectors) {
    metrics.push(await evaluate(session, `(() => {
      const control = document.querySelector(${JSON.stringify(selector)});
      if (!control) return { selector: ${JSON.stringify(selector)}, missing: true, reachable: false };
      const opened = [];
      for (let parent = control.parentElement; parent; parent = parent.parentElement) {
        if (parent.tagName === 'DETAILS' && !parent.open) {
          parent.open = true;
          opened.push(parent);
        }
      }
      control.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
      let bounds = control.getBoundingClientRect();
      const headerBottom = document.querySelector('.app-header').getBoundingClientRect().bottom;
      const dock = document.getElementById('audition-dock');
      const usableBottom = dock.hidden || dock.contains(control) ? innerHeight : dock.getBoundingClientRect().top;
      if (!dock.contains(control) && (bounds.top < headerBottom || bounds.bottom > usableBottom)) {
        window.scrollBy({
          top: bounds.top + bounds.height / 2 - (headerBottom + usableBottom) / 2,
          behavior: 'instant',
        });
        bounds = control.getBoundingClientRect();
      }
      const centerX = bounds.left + bounds.width / 2;
      const centerY = bounds.top + bounds.height / 2;
      const hit = document.elementFromPoint(centerX, centerY);
      const points = [
        [centerX, centerY],
        [bounds.left + 2, bounds.top + 2], [bounds.right - 2, bounds.top + 2],
        [bounds.left + 2, bounds.bottom - 2], [bounds.right - 2, bounds.bottom - 2],
      ];
      const obstructions = points.map(([x, y]) => {
        const item = document.elementFromPoint(x, y);
        return item && (item === control || control.contains(item)) ? null : {
          x, y, element: item ? (item.id || item.className || item.tagName) : null,
        };
      }).filter(Boolean);
      const unobscured = obstructions.length === 0;
      const style = getComputedStyle(control);
      const contained = bounds.left >= -1 && bounds.right <= innerWidth + 1 &&
        bounds.top >= -1 && bounds.bottom <= innerHeight + 1;
      const metric = {
        selector: ${JSON.stringify(selector)},
        text: (control.textContent || control.getAttribute('aria-label') || '').trim(),
        bounds: { left: bounds.left, top: bounds.top, right: bounds.right, bottom: bounds.bottom },
        rendered: bounds.width > 0 && bounds.height > 0 && style.visibility !== 'hidden',
        contained,
        occluded: !unobscured,
        obstructions,
        reachable: contained && bounds.width > 0 && bounds.height > 0 &&
          unobscured,
        occludingElement: hit && !(control === hit || control.contains(hit)) ?
          (hit.id || hit.className || hit.tagName) : null,
      };
      opened.forEach(details => { details.open = false; });
      return metric;
    })()`));
  }
  const layout = await evaluate(session, `({
    activePanel: Array.from(document.querySelectorAll('.tab-panel')).find(panel => !panel.hidden)?.id,
    viewport: { width: innerWidth, height: innerHeight, devicePixelRatio },
    documentOverflow: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
    mixCount: document.querySelectorAll('#job-targets-body .source-row').length,
    referenceCount: document.querySelectorAll('#job-references-body .source-row').length,
  })`);
  return { ...layout, controls: metrics, allReachable: metrics.every(item => item.reachable) };
}

async function pressKey(session, key, modifiers = 0) {
  const definitions = {
    Enter: { code: "Enter", windowsVirtualKeyCode: 13 },
    Space: { key: " ", code: "Space", windowsVirtualKeyCode: 32, text: " " },
    Escape: { code: "Escape", windowsVirtualKeyCode: 27 },
    ArrowDown: { code: "ArrowDown", windowsVirtualKeyCode: 40 },
    ArrowUp: { code: "ArrowUp", windowsVirtualKeyCode: 38 },
    Home: { code: "Home", windowsVirtualKeyCode: 36 },
    Tab: { code: "Tab", windowsVirtualKeyCode: 9 },
  };
  const definition = definitions[key];
  if (!definition) throw new Error(`unsupported audit key: ${key}`);
  const actualKey = definition.key || key;
  const common = {
    modifiers,
    key: actualKey,
    code: definition.code,
    windowsVirtualKeyCode: definition.windowsVirtualKeyCode,
    nativeVirtualKeyCode: definition.windowsVirtualKeyCode,
  };
  await session.call("Input.dispatchKeyEvent", {
    type: "keyDown",
    ...common,
    ...(definition.text ? { text: definition.text, unmodifiedText: definition.text } : {}),
  });
  if (key === "Enter") {
    await session.call("Input.dispatchKeyEvent", {
      type: "char",
      ...common,
      text: "\r",
      unmodifiedText: "\r",
    });
  }
  await session.call("Input.dispatchKeyEvent", { type: "keyUp", ...common });
}

const layoutAuditExpression = `(() => {
  const rect = (element) => {
    const value = element.getBoundingClientRect();
    return {
      left: Math.round(value.left),
      top: Math.round(value.top),
      right: Math.round(value.right),
      bottom: Math.round(value.bottom),
      width: Math.round(value.width),
      height: Math.round(value.height),
    };
  };
  const list = document.querySelector('.library-source-panel');
  const detail = document.querySelector('.library-detail-panel');
  const selected = document.querySelector('#master-library-body tr[aria-selected="true"]');
  const a = document.getElementById('audition-picker-a');
  const b = document.getElementById('audition-picker-b');
  const options = Array.from(a.options);
  const listRect = rect(list);
  const detailRect = rect(detail);
  const versionViewport = document.querySelector('.master-version-table');
  const versionViewportRect = rect(versionViewport);
  const controls = Array.from(versionViewport.querySelectorAll('input, select, button'));
  const controlMetrics = controls.map((control) => {
    const controlRect = rect(control);
    const style = getComputedStyle(control);
    return {
      tag: control.tagName.toLowerCase(),
      text: (control.textContent || control.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim(),
      versionControl: control.dataset.versionControl || null,
      rect: controlRect,
      rendered: controlRect.width > 0 && controlRect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden',
      horizontallyReachable:
        controlRect.left >= versionViewportRect.left - 3 &&
        controlRect.right <= versionViewportRect.right + 3,
    };
  });
  const trackIdTerm = Array.from(document.querySelectorAll('#track-detail-facts dt'))
    .find((term) => term.textContent.trim() === 'Track ID');
  const trackIdValue = trackIdTerm?.nextElementSibling;
  return {
    appState: document.body.dataset.appState,
    activePanel: Array.from(document.querySelectorAll('.tab-panel')).find((panel) => !panel.hidden)?.id,
    sourceRowCount: document.querySelectorAll('#master-library-body tr.master-library-row').length,
    selectedSource: selected?.textContent.replace(/\\s+/g, ' ').trim() || null,
    detailTitle: document.getElementById('track-detail-title')?.textContent.trim() || null,
    detailText: detail.textContent.replace(/\\s+/g, ' ').trim(),
    detailEmptyHidden: document.getElementById('track-detail-empty').hidden,
    detailContentHidden: document.getElementById('track-detail-content').hidden,
    trackId: trackIdValue ? {
      text: trackIdValue.textContent.trim(),
      title: trackIdValue.title,
      rect: rect(trackIdValue),
      lineHeight: parseFloat(getComputedStyle(trackIdValue).lineHeight),
      compact: trackIdValue.textContent.trim().length <= 22 && trackIdValue.title.length > trackIdValue.textContent.trim().length,
    } : null,
    listRect,
    detailRect,
    sideBySide: detailRect.left >= listRect.right - 2 && detailRect.top < listRect.bottom,
    overlapPixels: Math.max(0, Math.min(listRect.right, detailRect.right) - Math.max(listRect.left, detailRect.left)),
    aOptionCount: a.options.length,
    bOptionCount: b.options.length,
    originalCandidateCount: options.filter((option) => option.value.startsWith('original:')).length,
    masterCandidateCount: options.filter((option) => option.value.startsWith('master:')).length,
    optionGroups: Array.from(a.querySelectorAll('optgroup')).map((group) => ({
      label: group.label,
      optionCount: group.querySelectorAll('option').length,
    })),
    aValue: a.value,
    bValue: b.value,
    swapDisabled: document.getElementById('audition-swap-button').disabled,
    dockVisible: !document.getElementById('audition-dock').hidden,
    nowPlaying: document.getElementById('audition-now-playing').textContent.trim(),
    exportDrawerOpen: document.getElementById('master-library-actions').open,
    versionLayout: {
      clientWidth: versionViewport.clientWidth,
      scrollWidth: versionViewport.scrollWidth,
      horizontalOverflow: Math.max(0, versionViewport.scrollWidth - versionViewport.clientWidth),
      overflowX: getComputedStyle(versionViewport).overflowX,
      controlCount: controlMetrics.length,
      allControlsRendered: controlMetrics.length > 0 && controlMetrics.every((item) => item.rendered),
      allControlsHorizontallyReachable:
        controlMetrics.length > 0 && controlMetrics.every((item) => item.horizontallyReachable),
      controls: controlMetrics,
    },
    viewport: { width: innerWidth, height: innerHeight },
  };
})()`;

const activityLayoutAuditExpression = `(() => {
  const rect = (element) => {
    if (!element) return null;
    const value = element.getBoundingClientRect();
    return {
      left: Math.round(value.left),
      top: Math.round(value.top),
      right: Math.round(value.right),
      bottom: Math.round(value.bottom),
      width: Math.round(value.width),
      height: Math.round(value.height),
    };
  };
  const panel = document.getElementById('panel-runs');
  const workspace = panel.querySelector('.runs-workspace');
  const cards = Array.from(workspace.children);
  const list = panel.querySelector('.run-list-table');
  const listRows = Array.from(document.querySelectorAll('#runs-body > tr'));
  const detail = panel.querySelector('.run-detail-card');
  const artifacts = panel.querySelector('.artifacts-table');
  const toastRegion = document.getElementById('toast-region');
  const toastItems = Array.from(toastRegion.querySelectorAll('.toast'));
  const dock = document.getElementById('audition-dock');
  const panelRect = rect(panel);
  const workspaceRect = rect(workspace);
  const listRect = rect(list);
  const detailRect = rect(detail);
  const toastRect = rect(toastRegion);
  const dockRect = dock.hidden ? null : rect(dock);
  const withinViewport = (value) => Boolean(value) &&
    value.left >= -1 && value.right <= innerWidth + 1 &&
    value.top >= -1 && value.bottom <= innerHeight + 1;
  const horizontallyContained = (value) => Boolean(value) &&
    value.left >= -1 && value.right <= innerWidth + 1;
  return {
    appState: document.body.dataset.appState,
    activePanel: Array.from(document.querySelectorAll('.tab-panel')).find((candidate) => !candidate.hidden)?.id,
    viewport: { width: innerWidth, height: innerHeight },
    documentLayout: {
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      horizontalOverflow: Math.max(0, document.documentElement.scrollWidth - document.documentElement.clientWidth),
    },
    runCount: listRows.filter((row) => !row.querySelector('.empty-cell')).length,
    selectedRunCount: listRows.filter((row) => row.getAttribute('aria-selected') === 'true').length,
    detailPopulated: !document.getElementById('run-detail-content').hidden &&
      document.getElementById('run-detail-empty').hidden,
    panelRect,
    workspace: {
      rect: workspaceRect,
      horizontalOverflow: Math.max(0, workspace.scrollWidth - workspace.clientWidth),
      horizontallyContained: horizontallyContained(workspaceRect),
      cardCount: cards.length,
      allCardsHorizontallyContained: cards.every((card) => horizontallyContained(rect(card))),
    },
    runList: {
      rect: listRect,
      clientWidth: list.clientWidth,
      scrollWidth: list.scrollWidth,
      horizontalOverflow: Math.max(0, list.scrollWidth - list.clientWidth),
      overflowX: getComputedStyle(list).overflowX,
      allRowsContained: listRows.every((row) => {
        const rowRect = rect(row);
        return rowRect.left >= listRect.left - 1 && rowRect.right <= listRect.right + 1;
      }),
    },
    detail: {
      rect: detailRect,
      horizontallyContained: horizontallyContained(detailRect),
      artifactViewportHorizontallyContained: horizontallyContained(rect(artifacts)),
      artifactOverflowContainedInternally: artifacts.scrollWidth >= artifacts.clientWidth,
    },
    toast: {
      count: toastItems.length,
      texts: toastItems.map((item) => item.textContent.trim()),
      rect: toastRect,
      overflowY: getComputedStyle(toastRegion).overflowY,
      clientHeight: toastRegion.clientHeight,
      scrollHeight: toastRegion.scrollHeight,
      regionWithinViewport: withinViewport(toastRect),
      aboveAuditionDock: !dockRect || toastRect.bottom <= dockRect.top + 1,
      dockRect,
    },
  };
})()`;

let session;
let audit;
try {
  const activePortText = await waitForFile(join(profile, "DevToolsActivePort"));
  const [portText] = activePortText.trim().split(/\r?\n/);
  const port = Number(portText);
  if (!Number.isInteger(port) || port <= 0) throw new Error("invalid DevTools port");
  const page = await waitForPage(port);
  session = new CdpSession(page.webSocketDebuggerUrl);
  await session.open();
  await session.call("Page.enable");
  await session.call("Runtime.enable");
  await session.call("Log.enable");
  await setViewport(session, 1440, 1000);
  await waitForReady(session);
  await sleep(800);

  const rowKeyboardBefore = await evaluate(
    session,
    `(() => {
      const row = Array.from(document.querySelectorAll('#master-library-body tr.master-library-row'))
        .find((candidate) => candidate.textContent.includes('Neon Current'));
      if (!row) return { ok: false };
      window.__auditOldLibraryRow = row;
      row.focus();
      return {
        ok: true,
        trackId: row.dataset.trackId,
        wasSelected: row.getAttribute('aria-selected'),
        activeIsRow: document.activeElement === row,
      };
    })()`,
  );
  if (!rowKeyboardBefore.ok) throw new Error("could not focus the populated Neon Current source row");
  await pressKey(session, "Enter");
  await sleep(400);
  const rowKeyboardAfter = await evaluate(
    session,
    `(() => {
      const active = document.activeElement;
      const selected = document.querySelector('#master-library-body tr[aria-selected="true"]');
      return {
        oldConnected: window.__auditOldLibraryRow.isConnected,
        activeConnected: active.isConnected,
        activeTag: active.tagName.toLowerCase(),
        activeTrackId: active.closest('.master-library-row')?.dataset.trackId || null,
        activeIsReplacementRow: active.matches('.master-library-row') && active !== window.__auditOldLibraryRow,
        selectedTrackId: selected?.dataset.trackId || null,
        detailTitle: document.getElementById('track-detail-title').textContent.trim(),
      };
    })()`,
  );

  const sourceCheckboxBefore = await evaluate(
    session,
    `(() => {
      document.getElementById('master-library-actions').open = true;
      const row = Array.from(document.querySelectorAll('#master-library-body tr.master-library-row'))
        .find((candidate) => candidate.textContent.includes('Glass Skyline'));
      const checkbox = row?.querySelector('[data-library-control="export"]');
      if (!checkbox) return { ok: false };
      window.__auditOldSourceCheckbox = checkbox;
      checkbox.focus();
      return {
        ok: true,
        trackId: row.dataset.trackId,
        checked: checkbox.checked,
        selectedTrackId: document.querySelector('#master-library-body tr[aria-selected="true"]')?.dataset.trackId,
        activeIsCheckbox: document.activeElement === checkbox,
      };
    })()`,
  );
  if (!sourceCheckboxBefore.ok) throw new Error("could not focus a nested source export checkbox");
  await pressKey(session, "Space");
  await sleep(400);
  const sourceCheckboxAfter = await evaluate(
    session,
    `(() => {
      const row = Array.from(document.querySelectorAll('#master-library-body tr.master-library-row'))
        .find((candidate) => candidate.dataset.trackId === ${JSON.stringify(sourceCheckboxBefore.trackId)});
      const checkbox = row?.querySelector('[data-library-control="export"]');
      return {
        oldConnected: window.__auditOldSourceCheckbox.isConnected,
        replacementConnected: Boolean(checkbox?.isConnected),
        checked: checkbox?.checked,
        activeIsReplacementCheckbox: document.activeElement === checkbox,
        activeTag: document.activeElement.tagName.toLowerCase(),
        selectedTrackId: document.querySelector('#master-library-body tr[aria-selected="true"]')?.dataset.trackId,
      };
    })()`,
  );

  const versionOutputBefore = await evaluate(
    session,
    `(() => {
      const row = document.querySelector('#master-versions-body .version-row');
      const output = row?.querySelector('[data-version-control="output"]');
      if (!row || !output || output.options.length < 2) return { ok: false };
      window.__auditOldVersionOutput = output;
      output.focus();
      return {
        ok: true,
        versionId: row.dataset.versionId,
        value: output.value,
        selectedIndex: output.selectedIndex,
        optionCount: output.options.length,
        direction: output.selectedIndex < output.options.length - 1 ? 'ArrowDown' : 'ArrowUp',
        activeIsOutput: document.activeElement === output,
      };
    })()`,
  );
  if (!versionOutputBefore.ok) throw new Error("multi-version output selector was unavailable");
  await pressKey(session, versionOutputBefore.direction);
  await sleep(450);
  const versionOutputAfter = await evaluate(
    session,
    `(() => {
      const row = Array.from(document.querySelectorAll('#master-versions-body .version-row'))
        .find((candidate) => candidate.dataset.versionId === ${JSON.stringify(versionOutputBefore.versionId)});
      const output = row?.querySelector('[data-version-control="output"]');
      return {
        oldConnected: window.__auditOldVersionOutput.isConnected,
        replacementConnected: Boolean(output?.isConnected),
        value: output?.value,
        selectedIndex: output?.selectedIndex,
        activeIsReplacementOutput: document.activeElement === output,
        activeControl: document.activeElement.dataset.versionControl || null,
        activeVersionId: document.activeElement.closest('.version-row')?.dataset.versionId || null,
      };
    })()`,
  );

  const layout1440 = await evaluate(session, layoutAuditExpression);
  await evaluate(session, "document.getElementById('toast-region').replaceChildren(); true");
  await screenshot(session, "library-keyboard-version-actions-1440x1000.png");
  await setViewport(session, 1280, 800);
  const layout1280 = await evaluate(session, layoutAuditExpression);
  await screenshot(session, "library-keyboard-version-actions-1280x800.png");
  await setViewport(session, 1440, 1000);

  const menuOpen = await evaluate(
    session,
    `(() => {
      const summary = document.querySelector('#app-menu > summary');
      summary.focus();
      return { activeIsSummary: document.activeElement === summary };
    })()`,
  );
  const menuCommandFocused = await evaluate(
    session,
    `(() => {
      const menu = document.getElementById('app-menu');
      menu.open = true;
      const command = menu.querySelector('[data-command]');
      if (!menu.open || !command) return { ok: false, open: menu.open };
      command.focus();
      return {
        ok: true,
        open: menu.open,
        command: command.dataset.command,
        activeIsCommand: document.activeElement === command,
      };
    })()`,
  );
  if (!menuCommandFocused.ok) throw new Error("app menu did not open from keyboard");
  await pressKey(session, "Escape");
  await sleep(200);
  const menuAfterEscape = await evaluate(
    session,
    `(() => {
      const menu = document.getElementById('app-menu');
      const summary = menu.querySelector('summary');
      return {
        open: menu.open,
        activeIsSummary: document.activeElement === summary,
        activeTag: document.activeElement.tagName.toLowerCase(),
      };
    })()`,
  );

  const pickerSetup = await evaluate(
    session,
    `(() => {
      const a = document.getElementById('audition-picker-a');
      const b = document.getElementById('audition-picker-b');
      const original = Array.from(a.options).find((option) =>
        option.value.startsWith('original:') && option.textContent.includes('Neon Current'));
      const master = Array.from(b.options).find((option) =>
        option.value.startsWith('master:') &&
        option.parentElement?.label.includes('Glass Skyline') &&
        option.textContent.toLowerCase().includes('limited'));
      if (!original || !master) return { ok: false };
      a.value = original.value;
      a.dispatchEvent(new Event('change', { bubbles: true }));
      b.value = master.value;
      b.dispatchEvent(new Event('change', { bubbles: true }));
      document.getElementById('audition-slot-a').click();
      return {
        ok: true,
        originalValue: original.value,
        originalText: original.textContent,
        masterValue: master.value,
        masterText: master.textContent,
      };
    })()`,
  );
  if (!pickerSetup.ok) throw new Error("cross-track A/B candidates were not available in the pickers");
  await sleep(700);
  await evaluate(
    session,
    `(() => {
      const audio = document.getElementById('audition-player');
      if (Number.isFinite(audio.duration) && audio.duration > 0.5) audio.currentTime = 0.35;
      return true;
    })()`,
  );
  await sleep(150);
  const pairBeforeRowChange = await evaluate(
    session,
    `(() => ({
      aValue: document.getElementById('audition-picker-a').value,
      bValue: document.getElementById('audition-picker-b').value,
      activeA: document.getElementById('audition-slot-a').getAttribute('aria-pressed'),
      activeB: document.getElementById('audition-slot-b').getAttribute('aria-pressed'),
      trackId: document.getElementById('audition-player').dataset.trackId,
      src: document.getElementById('audition-player').getAttribute('src'),
      currentTime: document.getElementById('audition-player').currentTime,
    }))()`,
  );

  const differentRowReady = await evaluate(
    session,
    `(() => {
      const row = Array.from(document.querySelectorAll('#master-library-body tr.master-library-row'))
        .find((candidate) => candidate.textContent.includes('Afterimage'));
      if (!row) return false;
      row.focus();
      return true;
    })()`,
  );
  if (!differentRowReady) throw new Error("different Library source row was unavailable");
  await pressKey(session, "Enter");
  await sleep(400);
  const pairAfterRowChange = await evaluate(
    session,
    `(() => ({
      aValue: document.getElementById('audition-picker-a').value,
      bValue: document.getElementById('audition-picker-b').value,
      dockVisible: !document.getElementById('audition-dock').hidden,
      selectedTitle: document.getElementById('track-detail-title').textContent.trim(),
      focusedTrackId: document.activeElement.closest('.master-library-row')?.dataset.trackId || null,
      selectedTrackId: document.querySelector('#master-library-body tr[aria-selected="true"]')?.dataset.trackId || null,
    }))()`,
  );
  await evaluate(session, "document.getElementById('toast-region').replaceChildren(); true");
  await screenshot(session, "library-cross-track-ab-preserved-1440x1000.png");

  const beforeSwap = await evaluate(
    session,
    `(() => {
      const button = document.getElementById('audition-swap-button');
      const audio = document.getElementById('audition-player');
      button.focus();
      return {
        aValue: document.getElementById('audition-picker-a').value,
        bValue: document.getElementById('audition-picker-b').value,
        activeA: document.getElementById('audition-slot-a').getAttribute('aria-pressed'),
        activeB: document.getElementById('audition-slot-b').getAttribute('aria-pressed'),
        trackId: audio.dataset.trackId,
        src: audio.getAttribute('src'),
        currentTime: audio.currentTime,
        swapFocused: document.activeElement === button,
        swapDisabled: button.disabled,
      };
    })()`,
  );
  await pressKey(session, "Enter");
  await sleep(250);
  const afterSwap = await evaluate(
    session,
    `(() => {
      const audio = document.getElementById('audition-player');
      return {
        aValue: document.getElementById('audition-picker-a').value,
        bValue: document.getElementById('audition-picker-b').value,
        activeA: document.getElementById('audition-slot-a').getAttribute('aria-pressed'),
        activeB: document.getElementById('audition-slot-b').getAttribute('aria-pressed'),
        trackId: audio.dataset.trackId,
        src: audio.getAttribute('src'),
        currentTime: audio.currentTime,
        nowPlaying: document.getElementById('audition-now-playing').textContent.trim(),
      };
    })()`,
  );
  await screenshot(session, "library-cross-track-after-swap-1440x1000.png");

  const clearABefore = await evaluate(
    session,
    `(() => {
      const picker = document.getElementById('audition-picker-a');
      picker.focus();
      return {
        aValue: picker.value,
        bValue: document.getElementById('audition-picker-b').value,
        activeIsPicker: document.activeElement === picker,
      };
    })()`,
  );
  await pressKey(session, "Home");
  await sleep(350);
  const clearAAfter = await evaluate(
    session,
    `(() => ({
      aValue: document.getElementById('audition-picker-a').value,
      bValue: document.getElementById('audition-picker-b').value,
      aLabel: document.getElementById('audition-label-a').textContent.trim(),
      bLabel: document.getElementById('audition-label-b').textContent.trim(),
      dockVisible: !document.getElementById('audition-dock').hidden,
      activeB: document.getElementById('audition-slot-b').getAttribute('aria-pressed'),
      pickerStillFocused: document.activeElement === document.getElementById('audition-picker-a'),
    }))()`,
  );
  await evaluate(session, "document.getElementById('toast-region').replaceChildren(); true");
  await screenshot(session, "library-a-cleared-1440x1000.png");

  const addQueueTarget = async (label) => {
    const result = await evaluate(
      session,
      `(() => {
        document.getElementById('tab-catalog').click();
        const row = Array.from(document.querySelectorAll('#master-library-body tr.master-library-row'))
          .find((candidate) => candidate.textContent.includes(${JSON.stringify(label)}));
        if (!row) return { ok: false, reason: 'row missing' };
        row.click();
        const add = document.getElementById('track-role-target-button');
        if (add.disabled) return { ok: false, reason: 'add input disabled' };
        add.click();
        return {
          ok: true,
          activePanel: Array.from(document.querySelectorAll('.tab-panel')).find((panel) => !panel.hidden)?.id,
          targetCount: document.querySelectorAll('#job-targets-body .source-row').length,
        };
      })()`,
    );
    await sleep(200);
    return result;
  };
  const queueAddFirst = await addQueueTarget("Neon Current");
  const queueAddSecond = await addQueueTarget("Glass Skyline");
  if (!queueAddFirst.ok || !queueAddSecond.ok) {
    throw new Error(`could not populate Master queue: ${JSON.stringify({ queueAddFirst, queueAddSecond })}`);
  }
  const queueControlBefore = await evaluate(
    session,
    `(() => {
      const rows = Array.from(document.querySelectorAll('#job-targets-body .source-row'));
      const row = rows[0];
      const down = row?.querySelector('[data-queue-control="down"]');
      if (!row || !down || down.disabled) return { ok: false };
      window.__auditOldQueueControl = down;
      down.focus();
      return {
        ok: true,
        trackId: row.dataset.queueTrackId,
        order: rows.map((candidate) => candidate.dataset.queueTrackId),
        activeIsControl: document.activeElement === down,
        control: down.dataset.queueControl,
      };
    })()`,
  );
  if (!queueControlBefore.ok) throw new Error("Master queue move control was unavailable");
  await pressKey(session, "Enter");
  await sleep(350);
  const queueControlAfter = await evaluate(
    session,
    `(() => {
      const rows = Array.from(document.querySelectorAll('#job-targets-body .source-row'));
      const active = document.activeElement;
      return {
        oldConnected: window.__auditOldQueueControl.isConnected,
        activeConnected: active.isConnected,
        activeTag: active.tagName.toLowerCase(),
        activeControl: active.dataset.queueControl || null,
        activeTrackId: active.closest('.source-row')?.dataset.queueTrackId || null,
        order: rows.map((candidate) => candidate.dataset.queueTrackId),
        targetCount: rows.length,
      };
    })()`,
  );
  await evaluate(session, "document.getElementById('toast-region').replaceChildren(); true");
  await screenshot(session, "master-queue-keyboard-focus-1440x1000.png");
  await evaluate(session, "document.getElementById('tab-catalog').click(); true");
  await sleep(200);

  const renamedLabel = "Glass Skyline Keyboard Audit";
  const renameDialogLaunch = await evaluate(
    session,
    `(() => {
      const button = document.getElementById('track-rename-button');
      const selected = document.querySelector('#master-library-body tr[aria-selected="true"]');
      if (!button || button.disabled || !selected) return { ok: false };
      button.focus();
      button.click();
      return {
        ok: true,
        selectedTrackId: selected.dataset.trackId,
        oldLabel: document.getElementById('track-detail-title').textContent.trim(),
        launchButtonFocused: document.activeElement === button,
      };
    })()`,
  );
  if (!renameDialogLaunch.ok) throw new Error("could not open the populated track rename dialog");
  await waitForExpression(session, "document.getElementById('app-dialog').open === true");
  const renameDialogBeforeEnter = await evaluate(
    session,
    `(() => {
      const dialog = document.getElementById('app-dialog');
      const form = document.getElementById('dialog-form');
      const input = document.querySelector('#dialog-body input');
      const confirm = document.querySelector('#dialog-actions button[type="submit"]');
      if (!dialog.open || !input || !confirm) return { ok: false };
      input.value = ${JSON.stringify(renamedLabel)};
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      input.focus();
      window.__auditRenameInput = input;
      return {
        ok: true,
        dialogOpen: dialog.open,
        formMethod: form.method,
        confirmType: confirm.type,
        inputValue: input.value,
        inputFocused: document.activeElement === input,
      };
    })()`,
  );
  if (!renameDialogBeforeEnter.ok) throw new Error("rename dialog input or submit action was unavailable");
  await screenshot(session, "library-rename-dialog-before-enter-1440x1000.png");
  await pressKey(session, "Enter");
  const renameDialogAfterEnter = await waitForExpression(
    session,
    `(() => {
      const dialog = document.getElementById('app-dialog');
      const title = document.getElementById('track-detail-title').textContent.trim();
      const selected = document.querySelector('#master-library-body tr[aria-selected="true"]');
      const toastTexts = Array.from(document.querySelectorAll('#toast-region .toast'))
        .map((item) => item.textContent.trim());
      if (dialog.open || title !== ${JSON.stringify(renamedLabel)} ||
          !toastTexts.some((text) => text.includes(${JSON.stringify(renamedLabel)}))) return null;
      return {
        dialogOpen: dialog.open,
        title,
        selectedTrackId: selected?.dataset.trackId || null,
        selectedText: selected?.textContent.replace(/\\s+/g, ' ').trim() || null,
        oldInputConnected: window.__auditRenameInput.isConnected,
        toastTexts,
      };
    })()`,
    15000,
  );
  await screenshot(session, "library-rename-dialog-after-enter-1440x1000.png");
  await evaluate(session, "document.getElementById('toast-region').replaceChildren(); true");

  await evaluate(session, "document.getElementById('tab-runs').click(); true");
  await waitForExpression(
    session,
    `(() => {
      const active = Array.from(document.querySelectorAll('.tab-panel')).find((panel) => !panel.hidden);
      return active?.id === 'panel-runs' &&
        document.querySelectorAll('#runs-body > tr:not(:has(.empty-cell))').length >= 3;
    })()`,
  );
  const activityRunBefore = await evaluate(
    session,
    `(() => {
      const row = document.querySelector('#runs-body > tr:not(:has(.empty-cell))');
      if (!row) return { ok: false };
      window.__auditOldRunRow = row;
      row.focus();
      return {
        ok: true,
        runId: row.children[1]?.title || row.children[1]?.textContent.trim() || null,
        focused: document.activeElement === row,
      };
    })()`,
  );
  if (!activityRunBefore.ok) throw new Error("populated Activity run row was unavailable");
  await pressKey(session, "Enter");
  const activityRunAfter = await waitForExpression(
    session,
    `(() => {
      const active = document.activeElement;
      const content = document.getElementById('run-detail-content');
      const title = document.getElementById('run-detail-title').textContent.trim();
      if (content.hidden || !title) return null;
      return {
        oldConnected: window.__auditOldRunRow.isConnected,
        activeConnected: active.isConnected,
        activeIsReplacementRow: active.matches('#runs-body > tr') && active !== window.__auditOldRunRow,
        title,
        artifactCount: document.querySelectorAll('#run-artifacts-body > tr').length,
      };
    })()`,
    15000,
  );

  const activityToastSetup = await evaluate(
    session,
    `(() => {
      document.getElementById('toast-region').replaceChildren();
      const pickerA = document.getElementById('audition-picker-a');
      const slotA = document.getElementById('audition-slot-a');
      slotA.focus();
      return {
        aIsBlank: pickerA.value === '',
        dockVisible: !document.getElementById('audition-dock').hidden,
        slotFocused: document.activeElement === slotA,
      };
    })()`,
  );
  if (!activityToastSetup.aIsBlank || !activityToastSetup.dockVisible) {
    throw new Error(`Activity toast fixture was not ready: ${JSON.stringify(activityToastSetup)}`);
  }
  for (let index = 0; index < 10; index += 1) {
    await pressKey(session, "Enter");
  }
  await sleep(200);
  const activity1440 = await evaluate(session, activityLayoutAuditExpression);
  await screenshot(session, "activity-bounded-toasts-1440x1000.png");
  await setViewport(session, 1280, 800);
  const activity1280 = await evaluate(session, activityLayoutAuditExpression);
  await screenshot(session, "activity-bounded-toasts-1280x800.png");
  await setViewport(session, 1440, 1000);

  const reloadFixtureSetup = await evaluate(
    session,
    `(() => {
      document.getElementById('toast-region').replaceChildren();
      document.getElementById('tab-catalog').click();
      const row = Array.from(document.querySelectorAll('#master-library-body tr.master-library-row'))
        .find((candidate) => candidate.textContent.includes('Reference'));
      if (!row) return { ok: false, reason: 'reference row missing' };
      row.click();
      const add = document.getElementById('track-role-reference-button');
      if (!add || add.disabled) return { ok: false, reason: 'reference add disabled' };
      add.click();
      return {
        ok: true,
        activePanel: Array.from(document.querySelectorAll('.tab-panel')).find((panel) => !panel.hidden)?.id,
        targetCount: document.querySelectorAll('#job-targets-body .source-row').length,
        referenceCount: document.querySelectorAll('#job-references-body .source-row').length,
        dryRunDisabled: document.getElementById('dry-run-button').disabled,
      };
    })()`,
  );
  if (!reloadFixtureSetup.ok || reloadFixtureSetup.dryRunDisabled) {
    throw new Error(`could not prepare the isolated reload task: ${JSON.stringify(reloadFixtureSetup)}`);
  }

  // A 1280 x 800 physical viewport at 200% zoom has a 640 x 400 CSS viewport.
  // CDP reproduces that reflow and output scale without depending on browser UI
  // shortcuts. This records zoom-equivalent layout, not an OS DPI certification.
  const actionSelectors = {
    'master-job': [
      '#target-add-button', '#reference-add-button', '#job-output-directory',
      '#output-folder-choose-button', '#job-validate-button', '#dry-run-button', '#render-button',
    ],
    catalog: [
      '#catalog-search', '#catalog-refresh-button', '#track-compare-button',
      '#track-rename-button', '#audition-picker-a', '#audition-picker-b', '#audition-swap-button',
    ],
    runs: ['#runs-refresh-button', '#run-recover-file-button', '#run-tab-artifacts', '#run-tab-overview'],
  };
  const responsiveActions = {};
  for (const scale of [1, 2]) {
    await setViewport(session, 1280 / scale, 800 / scale, scale);
    const layouts = {};
    for (const [tab, selectors] of Object.entries(actionSelectors)) {
      if (tab === 'catalog') {
        await evaluate(session, `(() => {
          document.getElementById('tab-catalog').click();
          const row = Array.from(document.querySelectorAll('#master-library-body tr.master-library-row'))
            .find(candidate => candidate.textContent.includes('Neon Current'));
          row.click();
          return true;
        })()`);
        await sleep(200);
      }
      layouts[tab] = await auditActionReachability(session, tab, selectors);
      await screenshot(session, `${tab}-actions-1280x800-${scale === 1 ? '100' : '200'}percent.png`);
    }
    responsiveActions[`${scale * 100}percent`] = layouts;
  }
  await setViewport(session, 1280, 800);
  await evaluate(session, `(() => {
    document.getElementById('tab-master-job').click();
    document.getElementById('audition-clear-button').click();
    const rail = document.querySelector('.validation-panel');
    const probe = document.createElement('div');
    probe.id = 'smoke-status-probe';
    probe.className = 'detail-actions';
    for (const [role, label] of [['good', 'Ready'], ['warning', 'Warning'], ['danger', 'Error']]) {
      const badge = document.createElement('span');
      badge.className = 'state-badge ' + role;
      badge.textContent = label;
      probe.append(badge);
    }
    rail.append(probe);
    return true;
  })()`);
  // Programmatic focus alone can retain the previous input modality or sample
  // focus styling before Chromium paints it. Exercise a real keyboard entry
  // from the field's next tab stop in an explicitly focused browser page.
  await session.call('Page.bringToFront');
  await session.call('Emulation.setFocusEmulationEnabled', { enabled: true });
  await evaluate(session, "document.getElementById('output-folder-choose-button').focus(); true");
  await pressKey(session, 'Tab', 8); // CDP modifier 8 is Shift: move to the prior field.
  const keyboardFocusEnteredField = await evaluate(session,
    "document.activeElement === document.getElementById('job-output-directory')");
  const browserVersion = await session.call('Browser.getVersion');
  const themeExpression = `(() => {
    const focus = document.getElementById('job-output-directory');
    const focusStyle = getComputedStyle(focus);
    return {
      forcedColors: matchMedia('(forced-colors: active)').matches,
      documentHasFocus: document.hasFocus(),
      activeIsField: document.activeElement === focus,
      focusMatches: focus.matches(':focus'),
      focusVisible: focus.matches(':focus-visible'),
      focusToken: focusStyle.getPropertyValue('--focus').trim(),
      outlineStyle: focusStyle.outlineStyle,
      outlineWidth: focusStyle.outlineWidth,
      outlineColor: focusStyle.outlineColor,
      borderColor: focusStyle.borderColor,
      primaryFill: getComputedStyle(document.getElementById('render-button')).backgroundColor,
      statuses: Array.from(document.querySelectorAll('#smoke-status-probe .state-badge')).map(badge => ({
        text: badge.textContent, color: getComputedStyle(badge).color,
        borderStyle: getComputedStyle(badge).borderTopStyle,
        borderWidth: getComputedStyle(badge).borderTopWidth,
      })),
    };
  })()`;
  async function captureSettledTheme(forcedColors) {
    const started = Date.now();
    let snapshot;
    let settled = false;
    do {
      await evaluate(session, 'new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(true))))');
      snapshot = await evaluate(session, themeExpression);
      settled = snapshot.forcedColors === forcedColors && snapshot.documentHasFocus &&
        snapshot.activeIsField && snapshot.focusMatches && snapshot.focusVisible &&
        snapshot.outlineStyle === 'solid' && parseFloat(snapshot.outlineWidth) >= 3 &&
        (forcedColors || (snapshot.outlineColor === 'rgb(255, 120, 212)' &&
          snapshot.primaryFill === 'rgb(118, 255, 83)'));
      if (settled) break;
      await sleep(50);
    } while (Date.now() - started < 3000);
    // Retain the last actual browser result on timeout; never normalize a bad
    // color into the expected value or discard the remaining audit evidence.
    return { ...snapshot, settled, settledAfterMs: Date.now() - started };
  }
  const themeNormal = await captureSettledTheme(false);
  await session.call('Emulation.setEmulatedMedia', { features: [{ name: 'forced-colors', value: 'active' }] });
  const themeForced = await captureSettledTheme(true);
  await screenshot(session, 'master-forced-colors-statuses-1280x800.png');
  await session.call('Emulation.setEmulatedMedia', { features: [] });
  await evaluate(session, "document.getElementById('smoke-status-probe').remove(); true");
  await setViewport(session, 1440, 1000);

  const dryRunBefore = await evaluate(
    session,
    `(() => {
      const button = document.getElementById('dry-run-button');
      button.focus();
      return {
        disabled: button.disabled,
        focused: document.activeElement === button,
        targetIds: Array.from(document.querySelectorAll('#job-targets-body .source-row'))
          .map((row) => row.dataset.queueTrackId),
      };
    })()`,
  );
  await pressKey(session, "Enter");
  const completedDryRun = await waitForExpression(
    session,
    `(async () => {
      const response = await fetch('/api/tasks/current', {
        headers: { 'X-MMT-Token': window.__MMT_CONFIG__.token },
      });
      const payload = await response.json();
      const task = payload && payload.data;
      if (!task || task.kind !== 'dry-run' || !['succeeded', 'failed'].includes(task.state)) {
        return null;
      }
      const result = task.result || {};
      return {
        operationId: task.operation_id,
        kind: task.kind,
        state: task.state,
        stage: task.stage,
        batch: result.batch || null,
        resultTargetIds: Array.isArray(result.targets)
          ? result.targets.map((item) => item.target_id)
          : [result.prepared && result.prepared.target_id].filter(Boolean),
        resultTargetStates: Array.isArray(result.targets)
          ? result.targets.map((item) => item.state)
          : [],
      };
    })()`,
    45000,
  );
  const dryRunBeforeNavigation = await waitForExpression(
    session,
    `(() => {
      const rows = Array.from(document.querySelectorAll('#task-target-progress .task-target-row'));
      if (rows.length !== ${dryRunBefore.targetIds.length}) return null;
      return {
        appState: document.body.dataset.appState,
        taskRows: rows.map((row) => row.textContent.replace(/\\s+/g, ' ').trim()),
        progressHidden: document.getElementById('task-target-progress').hidden,
        reviewMastersVisible: !document.getElementById('review-masters-button').hidden,
        validationBadge: document.getElementById('validation-badge').textContent.trim(),
      };
    })()`,
    15000,
  );
  await screenshot(session, "master-dry-run-complete-before-new-page-1440x1000.png");

  await session.call("Page.navigate", {
    url: `${launchUrl.split("#")[0]}#master-job`,
  });
  await waitForReady(session, 30000);
  const dryRunAfterNavigation = await waitForExpression(
    session,
    `(async () => {
      document.getElementById('tab-master-job').click();
      const rows = Array.from(document.querySelectorAll('#task-target-progress .task-target-row'));
      const response = await fetch('/api/tasks/current', {
        headers: { 'X-MMT-Token': window.__MMT_CONFIG__.token },
      });
      const payload = await response.json();
      const task = payload && payload.data;
      if (!task || task.operation_id !== ${JSON.stringify(completedDryRun.operationId)} ||
          rows.length !== ${dryRunBefore.targetIds.length}) return null;
      return {
        appState: document.body.dataset.appState,
        activePanel: Array.from(document.querySelectorAll('.tab-panel')).find((panel) => !panel.hidden)?.id,
        operationId: task.operation_id,
        kind: task.kind,
        state: task.state,
        queueTargetCount: document.querySelectorAll('#job-targets-body .source-row').length,
        progressHidden: document.getElementById('task-target-progress').hidden,
        taskRows: rows.map((row) => ({
          text: row.textContent.replace(/\\s+/g, ' ').trim(),
          status: row.querySelector('.state-badge')?.textContent.trim() || null,
        })),
        reviewMastersVisible: !document.getElementById('review-masters-button').hidden,
        commandTaskState: document.getElementById('command-task-state').textContent.replace(/\\s+/g, ' ').trim(),
        validationBadge: document.getElementById('validation-badge').textContent.trim(),
      };
    })()`,
    15000,
  );
  await screenshot(session, "master-dry-run-reconstructed-new-page-1440x1000.png");

  const rawDom = await evaluate(session, "document.documentElement.outerHTML");
  const token = new URL(launchUrl).searchParams.get("token");
  const redactedDom = token ? rawDom.replaceAll(token, "[REDACTED]") : rawDom;
  writeFileSync(join(outputRoot, "rendered-dom.html"), redactedDom, "utf8");

  const exceptionEvents = session.events
    .filter((event) => event.method === "Runtime.exceptionThrown")
    .map((event) => event.params?.exceptionDetails?.exception?.description || event.params?.exceptionDetails?.text);
  const errorLogEvents = session.events
    .filter((event) => event.method === "Log.entryAdded" && event.params?.entry?.level === "error")
    .map((event) => event.params.entry.text);
  const checks = {
    primaryActionsReachableAt1280:
      Object.values(responsiveActions['100percent']).every(layout => layout.allReachable && layout.documentOverflow === 0),
    primaryActionsReachableAt200Percent:
      Object.values(responsiveActions['200percent']).every(layout => layout.allReachable && layout.documentOverflow === 0),
    populatedMasterZoomFixture:
      responsiveActions['200percent']['master-job'].mixCount === 2 &&
      responsiveActions['200percent']['master-job'].referenceCount === 1,
    caderKeyboardFocusDistinct:
      keyboardFocusEnteredField && themeNormal.settled && themeNormal.documentHasFocus &&
      themeNormal.activeIsField && themeNormal.focusMatches && themeNormal.focusVisible &&
      themeNormal.outlineStyle === 'solid' &&
      parseFloat(themeNormal.outlineWidth) >= 3 &&
      themeNormal.outlineColor === 'rgb(255, 120, 212)' &&
      themeNormal.primaryFill === 'rgb(118, 255, 83)',
    forcedColorsKeepFocusAndStatusLabels:
      themeForced.settled && themeForced.forcedColors && themeForced.documentHasFocus &&
      themeForced.activeIsField && themeForced.focusMatches && themeForced.focusVisible &&
      themeForced.outlineStyle === 'solid' && parseFloat(themeForced.outlineWidth) >= 3 &&
      themeForced.statuses.map(item => item.text).join(',') === 'Ready,Warning,Error' &&
      themeForced.statuses.every(item => item.borderStyle === 'solid' && parseFloat(item.borderWidth) >= 1),
    appReady: layout1440.appState === "ready" && layout1280.appState === "ready",
    catalogActive: layout1440.activePanel === "panel-catalog",
    populatedSources: layout1440.sourceRowCount >= 6,
    selectedDetailPopulated:
      layout1440.selectedSource?.includes("Neon Current") &&
      layout1440.detailTitle?.includes("Neon Current") &&
      layout1440.detailEmptyHidden &&
      !layout1440.detailContentHidden,
    directPickerCandidates:
      layout1440.originalCandidateCount >= 3 && layout1440.masterCandidateCount >= 9,
    libraryAudioGroupNamedAccurately:
      layout1440.optionGroups.some((group) => group.label === "Library audio") &&
      !layout1440.optionGroups.some((group) => group.label === "Originals"),
    compactTrackId:
      Boolean(layout1440.trackId?.compact) && Boolean(layout1280.trackId?.compact),
    libraryRowEnterRestoresFocus:
      rowKeyboardBefore.activeIsRow &&
      !rowKeyboardAfter.oldConnected &&
      rowKeyboardAfter.activeConnected &&
      rowKeyboardAfter.activeIsReplacementRow &&
      rowKeyboardAfter.activeTrackId === rowKeyboardBefore.trackId &&
      rowKeyboardAfter.selectedTrackId === rowKeyboardBefore.trackId,
    nestedSourceSpacePreservesControlFocus:
      sourceCheckboxBefore.activeIsCheckbox &&
      sourceCheckboxAfter.checked !== sourceCheckboxBefore.checked &&
      !sourceCheckboxAfter.oldConnected &&
      sourceCheckboxAfter.replacementConnected &&
      sourceCheckboxAfter.activeIsReplacementCheckbox &&
      sourceCheckboxAfter.activeTag === "input" &&
      sourceCheckboxAfter.selectedTrackId === sourceCheckboxBefore.selectedTrackId,
    versionOutputKeyboardPreservesFocus:
      versionOutputBefore.activeIsOutput &&
      versionOutputAfter.value !== versionOutputBefore.value &&
      !versionOutputAfter.oldConnected &&
      versionOutputAfter.replacementConnected &&
      versionOutputAfter.activeIsReplacementOutput &&
      versionOutputAfter.activeControl === "output" &&
      versionOutputAfter.activeVersionId === versionOutputBefore.versionId,
    appMenuEscapeReturnsFocus:
      menuOpen.activeIsSummary &&
      menuCommandFocused.open &&
      menuCommandFocused.activeIsCommand &&
      !menuAfterEscape.open &&
      menuAfterEscape.activeIsSummary,
    customPairSurvivesLibrarySelection:
      pairBeforeRowChange.aValue === pickerSetup.originalValue &&
      pairBeforeRowChange.bValue === pickerSetup.masterValue &&
      pairAfterRowChange.aValue === pairBeforeRowChange.aValue &&
      pairAfterRowChange.bValue === pairBeforeRowChange.bValue &&
      pairAfterRowChange.dockVisible &&
      pairAfterRowChange.selectedTitle.includes("Afterimage") &&
      pairAfterRowChange.focusedTrackId === pairAfterRowChange.selectedTrackId,
    swapExecuted:
      beforeSwap.aValue === afterSwap.bValue && beforeSwap.bValue === afterSwap.aValue,
    swapPreservedAudibleMedia:
      beforeSwap.trackId === afterSwap.trackId && beforeSwap.src === afterSwap.src,
    swapPreservedPlayhead: Math.abs(beforeSwap.currentTime - afterSwap.currentTime) < 0.2,
    activeSlotFollowedAudibleMedia:
      beforeSwap.activeA === "true" && afterSwap.activeB === "true",
    blankPickerClearsA:
      Boolean(clearABefore.aValue) &&
      clearAAfter.aValue === "" &&
      clearAAfter.bValue === clearABefore.bValue &&
      clearAAfter.aLabel === "Empty" &&
      clearAAfter.dockVisible &&
      clearAAfter.activeB === "true" &&
      clearAAfter.pickerStillFocused,
    sideBySideAt1440: layout1440.sideBySide && layout1440.overlapPixels === 0,
    sideBySideAt1280: layout1280.sideBySide && layout1280.overlapPixels === 0,
    versionActionsReachableAt1440:
      layout1440.versionLayout.horizontalOverflow === 0 &&
      layout1440.versionLayout.allControlsRendered &&
      layout1440.versionLayout.allControlsHorizontallyReachable,
    versionActionsReachableAt1280:
      layout1280.versionLayout.horizontalOverflow === 0 &&
      layout1280.versionLayout.allControlsRendered &&
      layout1280.versionLayout.allControlsHorizontallyReachable,
    masterQueueKeyboardPreservesFocus:
      queueAddFirst.targetCount === 1 &&
      queueAddSecond.targetCount === 2 &&
      queueControlBefore.activeIsControl &&
      !queueControlAfter.oldConnected &&
      queueControlAfter.activeConnected &&
      queueControlAfter.activeTag === "button" &&
      queueControlAfter.activeTrackId === queueControlBefore.trackId &&
      queueControlAfter.targetCount === 2 &&
      queueControlAfter.order[1] === queueControlBefore.trackId &&
      queueControlAfter.order[0] === queueControlBefore.order[1],
    dialogEnterSubmitsAndRefreshesLibrary:
      renameDialogBeforeEnter.dialogOpen &&
      renameDialogBeforeEnter.confirmType === "submit" &&
      renameDialogBeforeEnter.inputFocused &&
      renameDialogBeforeEnter.inputValue === renamedLabel &&
      !renameDialogAfterEnter.dialogOpen &&
      renameDialogAfterEnter.title === renamedLabel &&
      renameDialogAfterEnter.selectedTrackId === renameDialogLaunch.selectedTrackId &&
      renameDialogAfterEnter.selectedText.includes(renamedLabel),
    activityRunEnterRestoresFocus:
      activityRunBefore.focused &&
      !activityRunAfter.oldConnected &&
      activityRunAfter.activeConnected &&
      activityRunAfter.activeIsReplacementRow &&
      activityRunAfter.artifactCount > 0,
    activityNoHorizontalOverflowAt1440:
      activity1440.appState === "ready" &&
      activity1440.activePanel === "panel-runs" &&
      activity1440.runCount >= 3 &&
      activity1440.selectedRunCount === 1 &&
      activity1440.detailPopulated &&
      activity1440.documentLayout.horizontalOverflow === 0 &&
      activity1440.workspace.horizontalOverflow === 0 &&
      activity1440.workspace.horizontallyContained &&
      activity1440.workspace.allCardsHorizontallyContained &&
      activity1440.runList.horizontalOverflow === 0 &&
      activity1440.runList.allRowsContained &&
      activity1440.detail.horizontallyContained &&
      activity1440.detail.artifactViewportHorizontallyContained,
    activityNoHorizontalOverflowAt1280:
      activity1280.appState === "ready" &&
      activity1280.activePanel === "panel-runs" &&
      activity1280.runCount >= 3 &&
      activity1280.selectedRunCount === 1 &&
      activity1280.detailPopulated &&
      activity1280.documentLayout.horizontalOverflow === 0 &&
      activity1280.workspace.horizontalOverflow === 0 &&
      activity1280.workspace.horizontallyContained &&
      activity1280.workspace.allCardsHorizontallyContained &&
      activity1280.runList.horizontalOverflow === 0 &&
      activity1280.runList.allRowsContained &&
      activity1280.detail.horizontallyContained &&
      activity1280.detail.artifactViewportHorizontallyContained,
    activityToastStackBoundedAt1440:
      activityToastSetup.slotFocused &&
      activity1440.toast.count >= 10 &&
      activity1440.toast.overflowY === "auto" &&
      activity1440.toast.regionWithinViewport &&
      activity1440.toast.aboveAuditionDock,
    activityToastStackBoundedAt1280:
      activity1280.toast.count >= 10 &&
      activity1280.toast.overflowY === "auto" &&
      activity1280.toast.regionWithinViewport &&
      activity1280.toast.aboveAuditionDock,
    completedTaskTargetsReconstructedOnNewPage:
      reloadFixtureSetup.targetCount === 2 &&
      reloadFixtureSetup.referenceCount === 1 &&
      dryRunBefore.focused &&
      !dryRunBefore.disabled &&
      completedDryRun.kind === "dry-run" &&
      completedDryRun.state === "succeeded" &&
      completedDryRun.resultTargetIds.length === dryRunBefore.targetIds.length &&
      completedDryRun.resultTargetIds.every((trackId) => dryRunBefore.targetIds.includes(trackId)) &&
      !dryRunBeforeNavigation.progressHidden &&
      dryRunAfterNavigation.appState === "ready" &&
      dryRunAfterNavigation.activePanel === "panel-master-job" &&
      dryRunAfterNavigation.operationId === completedDryRun.operationId &&
      dryRunAfterNavigation.state === "succeeded" &&
      dryRunAfterNavigation.queueTargetCount === 0 &&
      !dryRunAfterNavigation.progressHidden &&
      dryRunAfterNavigation.taskRows.length === dryRunBefore.targetIds.length &&
      dryRunAfterNavigation.taskRows.every((row) => row.status === "Complete") &&
      !dryRunAfterNavigation.reviewMastersVisible,
    noRuntimeExceptions: exceptionEvents.length === 0,
    noBrowserErrorLogs: errorLogEvents.length === 0,
  };
  audit = {
    passed: Object.values(checks).every(Boolean),
    checks,
    responsiveActions,
    browserVersion,
    keyboardFocusEnteredField,
    themeNormal,
    themeForced,
    rowKeyboard: { before: rowKeyboardBefore, after: rowKeyboardAfter },
    sourceCheckboxKeyboard: { before: sourceCheckboxBefore, after: sourceCheckboxAfter },
    versionOutputKeyboard: { before: versionOutputBefore, after: versionOutputAfter },
    appMenuKeyboard: { open: menuOpen, commandFocused: menuCommandFocused, afterEscape: menuAfterEscape },
    pickerSetup,
    pairBeforeRowChange,
    pairAfterRowChange,
    beforeSwap,
    afterSwap,
    clearA: { before: clearABefore, after: clearAAfter },
    masterQueueKeyboard: {
      addFirst: queueAddFirst,
      addSecond: queueAddSecond,
      before: queueControlBefore,
      after: queueControlAfter,
    },
    renameDialogKeyboard: {
      launch: renameDialogLaunch,
      beforeEnter: renameDialogBeforeEnter,
      afterEnter: renameDialogAfterEnter,
    },
    activityKeyboard: {
      before: activityRunBefore,
      after: activityRunAfter,
    },
    activityToastSetup,
    activity1440,
    activity1280,
    taskReloadReconstruction: {
      fixture: reloadFixtureSetup,
      beforeSubmission: dryRunBefore,
      completed: completedDryRun,
      beforeNavigation: dryRunBeforeNavigation,
      afterNavigation: dryRunAfterNavigation,
    },
    layout1440,
    layout1280,
    runtimeExceptions: exceptionEvents,
    browserErrorLogs: errorLogEvents,
  };
  let serializedAudit = `${JSON.stringify(audit, null, 2)}\n`;
  if (token) serializedAudit = serializedAudit.replaceAll(token, "[REDACTED]");
  writeFileSync(join(outputRoot, "browser-audit.json"), serializedAudit, "utf8");
  if (!audit.passed) throw new Error("one or more populated browser audit checks failed");
} catch (error) {
  const token = new URL(launchUrl).searchParams.get("token");
  let safeError = String(error.stack || error);
  if (token) safeError = safeError.replaceAll(token, "[REDACTED]");
  writeFileSync(join(outputRoot, "browser-failure.log"), `${safeError}\n`, "utf8");
  console.error(safeError);
  process.exitCode = 1;
  audit ||= { passed: false, checks: {} };
} finally {
  if (session) {
    try {
      await session.call("Browser.close");
    } catch {
      // The browser may close the transport before acknowledging Browser.close.
    }
  }
  const exitDeadline = Date.now() + 5000;
  while (chrome.exitCode === null && Date.now() < exitDeadline) await sleep(100);
  if (chrome.exitCode === null) chrome.kill();
  browserLog.end();
  await sleep(100);
  const token = new URL(launchUrl).searchParams.get('token');
  if (token) {
    const logPath = join(outputRoot, 'browser.log');
    writeFileSync(logPath, readFileSync(logPath, 'utf8').replaceAll(token, '[REDACTED]'));
  }
  const resolvedProfile = resolve(profile);
  if (resolvedProfile.startsWith(`${outputRoot}\\`) || resolvedProfile.startsWith(`${outputRoot}/`)) {
    rmSync(resolvedProfile, { recursive: true, force: true });
  }
}

console.log(JSON.stringify({ passed: audit.passed, checks: audit.checks }));
