import { BrowserWindow, session } from "electron";
import { randomUUID } from "node:crypto";
import { realpath, stat } from "node:fs/promises";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

export async function snapshotPath(receipt: unknown, businessId: string, dataDir: string): Promise<string> {
  if (!receipt || typeof receipt !== "object" || !/^[A-Za-z0-9_-]{1,128}$/.test(businessId)) throw new Error("SNAPSHOT_SCOPE_INVALID");
  const value = receipt as { scope?: unknown; path?: unknown; name?: unknown };
  const expected = join(await realpath(dataDir), "exports", "session-snapshots", `${businessId}.html`);
  if (value.scope !== "business_session" || value.name !== `${businessId}.html` || typeof value.path !== "string" || resolve(value.path) !== expected) throw new Error("SNAPSHOT_SCOPE_INVALID");
  if (await realpath(expected) !== expected || !(await stat(expected)).isFile()) throw new Error("SNAPSHOT_FILE_INVALID");
  return expected;
}

export async function openSessionSnapshot(path: string, show = true): Promise<BrowserWindow> {
  // A separate ephemeral session has no workbench preload, storage, permissions or network.
  const isolated = session.fromPartition(`snapshot-${randomUUID()}`);
  const entry = pathToFileURL(path).href;
  const sameDocument = (url: string) => url.split("#", 1)[0] === entry;
  isolated.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  isolated.setPermissionCheckHandler(() => false);
  isolated.webRequest.onBeforeRequest((details, callback) => callback({ cancel: !sameDocument(details.url) && !details.url.startsWith("blob:") }));
  isolated.webRequest.onHeadersReceived((details, callback) => callback({ responseHeaders: {
    ...details.responseHeaders,
    "Content-Security-Policy": ["default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"],
  } }));
  isolated.on("will-download", (event, item) => {
    if (!item.getURL().startsWith("blob:") || !item.getFilename().endsWith(".jsonl")) { event.preventDefault(); return; }
    item.setSaveDialogOptions({ title: "保存公开业务会话 JSONL", filters: [{ name: "会话记录", extensions: ["jsonl"] }] });
  });
  const window = new BrowserWindow({ title: "业务会话快照", width: 1240, height: 900, show: false,
    autoHideMenuBar: true, webPreferences: { session: isolated, contextIsolation: true,
      sandbox: true, nodeIntegration: false, webviewTag: false } });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-attach-webview", event => event.preventDefault());
  window.webContents.on("will-navigate", (event, url) => { if (!sameDocument(url)) event.preventDefault(); });
  window.webContents.on("will-redirect", (event, url) => { if (!sameDocument(url)) event.preventDefault(); });
  try {
    await window.loadFile(path);
    if (show) window.show();
    return window;
  } catch (error) {
    window.destroy();
    throw error;
  }
}
