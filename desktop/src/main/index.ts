import { app, BrowserWindow, dialog, ipcMain, session, shell } from "electron";
import { randomUUID } from "node:crypto";
import { rename, stat, unlink, writeFile } from "node:fs/promises";
import { basename, extname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { HostClient } from "./host";
import { publicSettings, saveSettings } from "./settings";
import { assertRequest, businessScope, canChangeSettings, METHODS, materialSessionId, observedRecordUrl, recordedArtifactPath, safeMaterialName, strictBase64 } from "./ipc-security";
import { runSelfCheck } from "./self-check";
import type { BusinessDetail, SettingsInput, WorkbenchMethod } from "../shared/protocol";

const host = new HostClient();
let settingsChanging = false;
let exportInProgress = false;
let mainWindow: BrowserWindow | undefined;
function configureUserDataDir(): void {
  const inline = process.argv.find((value) => value.startsWith("--user-data-dir="));
  const index = process.argv.indexOf("--user-data-dir");
  const value = inline?.slice("--user-data-dir=".length) || (index >= 0 ? process.argv[index + 1] : undefined);
  if (value) app.setPath("userData", resolve(value));
}

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 640,
    frame: false,
    autoHideMenuBar: true,
    title: "Odoo Agent",
    icon: app.isPackaged ? join(process.resourcesPath, "workbench.png") : join(app.getAppPath(), "assets/workbench.png"),
    backgroundColor: "#f4f7f8",
    webPreferences: {
      preload: join(__dirname, "../preload/index.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-attach-webview", (event) => event.preventDefault());
  const localEntry = allowedRendererUrl();
  const isLocal = (url: string) => url === localEntry;
  window.webContents.on("will-navigate", (event, url) => {
    if (!isLocal(url)) event.preventDefault();
  });
  window.webContents.on("will-redirect", (event, url) => {
    if (!isLocal(url)) event.preventDefault();
  });
  const unsubscribe = host.subscribe((event) => {
    if (!window.isDestroyed()) window.webContents.send("workbench:event", event);
  });
  window.once("closed", unsubscribe);
  if (!app.isPackaged && process.env.ELECTRON_RENDERER_URL) void window.loadURL(process.env.ELECTRON_RENDERER_URL);
  else void window.loadFile(join(__dirname, "../renderer/index.html"));
  return window;
}

const DOCUMENT_MODELS = new Set(["sale.order", "purchase.order", "account.move"]);
const DOCUMENT_FORMATS = new Set(["pdf", "csv"]);
const MAX_DOCUMENT_BYTES = 32 * 1024 * 1024;

function documentRequest(params: Record<string, unknown>): { scope: ReturnType<typeof businessScope>; model: string; record_id: number; format: "pdf" | "csv" } {
  const scope = businessScope(params);
  if (typeof params.model !== "string" || !DOCUMENT_MODELS.has(params.model)) throw new Error("DOCUMENT_MODEL_NOT_ALLOWED");
  if (typeof params.record_id !== "number" || !Number.isSafeInteger(params.record_id) || params.record_id < 1) throw new Error("DOCUMENT_RECORD_INVALID");
  if (typeof params.format !== "string" || !DOCUMENT_FORMATS.has(params.format)) throw new Error("DOCUMENT_FORMAT_INVALID");
  return { scope, model: params.model, record_id: params.record_id, format: params.format as "pdf" | "csv" };
}

function documentPayload(value: unknown, format: "pdf" | "csv"): { bytes: Buffer; name: string; mime: string } {
  if (!value || typeof value !== "object") throw new Error("DOCUMENT_EXPORT_INVALID");
  const candidate = value as { data_base64?: unknown; name?: unknown; mime?: unknown };
  if (typeof candidate.data_base64 !== "string") throw new Error("DOCUMENT_EXPORT_INVALID");
  const bytes = strictBase64(candidate.data_base64, MAX_DOCUMENT_BYTES);
  const expectedMime = format === "pdf" ? "application/pdf" : "text/csv";
  if (candidate.mime !== expectedMime || typeof candidate.name !== "string" || basename(candidate.name) !== candidate.name) throw new Error("DOCUMENT_EXPORT_INVALID");
  const extension = extname(candidate.name).toLowerCase();
  if (extension !== `.${format}` || (format === "pdf" && (bytes.length < 5 || bytes.subarray(0, 5).toString("ascii") !== "%PDF-"))) throw new Error("DOCUMENT_EXPORT_INVALID");
  return { bytes, name: candidate.name, mime: expectedMime };
}

function registerIpc(): void {
  ipcMain.handle("workbench:call", async (event, request: { method: WorkbenchMethod; params?: Record<string, unknown> }) => {
    assertTrustedFrame(event);
    assertRequest(request);
    if (request.method === "get_settings") return publicSettings();
    if (request.method === "save_settings") {
      if (!canChangeSettings(settingsChanging, host.isBusy())) throw new Error("CONFIG_BUSY");
      settingsChanging = true;
      try {
        // A run outlives its start_run RPC. Check the host while new requests
        // are excluded, so changing settings cannot interrupt active work.
        if (host.isRunning()) {
          const health = await host.call("health", {}) as { active_run_id?: string | null };
          if (health.active_run_id) throw new Error("CONFIG_BUSY");
        }
        const result = await saveSettings((request.params ?? {}) as SettingsInput);
        if (host.isRunning()) {
          await host.stop();
          await host.call("health", {});
        }
        return result;
      } finally {
        settingsChanging = false;
      }
    }
    if (settingsChanging) throw new Error("CONFIG_BUSY");
    if (request.method === "import_material") {
      const params = request.params ?? {};
      const session_id = materialSessionId(params.session_id);
      const name = safeMaterialName(params.name);
      const content = strictBase64(params.content_base64);
      return host.call("_import_material" as WorkbenchMethod, {
        session_id, name, content_base64: content.toString("base64"),
      });
    }
    if (["download_document", "export_business_report", "open_odoo_record", "open_business_artifact", "reveal_business_artifact"].includes(request.method)) {
      const params = request.params ?? {};
      const scope = businessScope(params);
      const detail = await host.call("get_business", scope) as BusinessDetail;
      if (detail.business.id !== scope.business_id || detail.business.session_id !== scope.session_id) throw new Error("BUSINESS_SCOPE_MISMATCH");
      if (request.method === "download_document") {
        const document = documentRequest(params);
        if (!detail.documents.some(item => item.model === document.model && String(item.id) === String(document.record_id))) throw new Error("RECORD_NOT_OBSERVED");
        if (exportInProgress) throw new Error("EXPORT_BUSY");
        exportInProgress = true;
        let temporary: string | undefined;
        try {
          const payload = documentPayload(await host.call("_export_document" as WorkbenchMethod, {
            ...document.scope, model: document.model, record_id: document.record_id, format: document.format,
          }), document.format);
          const window = BrowserWindow.fromWebContents(event.sender);
          if (!window) throw new Error("WINDOW_CLOSED");
          const result = await dialog.showSaveDialog(window, {
            title: document.format === "pdf" ? "下载 Odoo 单据 PDF" : "导出 Odoo 单据 CSV",
            defaultPath: payload.name,
            filters: [{ name: document.format === "pdf" ? "PDF 单据" : "CSV 明细", extensions: [document.format] }],
          });
          if (result.canceled || !result.filePath) return { cancelled: true };
          if (extname(result.filePath).toLowerCase() !== `.${document.format}`) throw new Error("ARTIFACT_FORMAT_INVALID");
          temporary = `${result.filePath}.${randomUUID()}.tmp`;
          await writeFile(temporary, payload.bytes, { flag: "wx", mode: 0o600 });
          await rename(temporary, result.filePath);
          temporary = undefined;
          const artifact = await host.call("_record_artifact" as WorkbenchMethod, {
            ...document.scope, path: result.filePath, name: basename(result.filePath),
            kind: document.format === "pdf" ? "odoo_pdf" : "odoo_csv", model: document.model,
            record_id: document.record_id, source: "odoo_report",
          }).catch(() => { throw new Error(`ARTIFACT_INDEX_FAILED: 文件已保存，但业务索引保存失败。`); });
          return { cancelled: false, path: result.filePath, artifact };
        } finally {
          exportInProgress = false;
          if (temporary) await unlink(temporary).catch(() => undefined);
        }
      }
      if (request.method === "open_odoo_record") {
        const settings = await publicSettings();
        await shell.openExternal(observedRecordUrl(settings.odoo_url, detail.documents, params.model, params.record_id));
        return { opened: true };
      }
      if (request.method === "open_business_artifact" || request.method === "reveal_business_artifact") {
        const path = recordedArtifactPath(detail.artifacts ?? [], params.artifact_id);
        const file = await stat(path).catch(() => undefined);
        if (!file?.isFile()) throw new Error("ARTIFACT_FILE_MISSING");
        if (request.method === "reveal_business_artifact") shell.showItemInFolder(path);
        else if (await shell.openPath(path)) throw new Error("ARTIFACT_OPEN_FAILED");
        return { opened: true };
      }
      if (exportInProgress) throw new Error("EXPORT_BUSY");
      exportInProgress = true;
      let temporary: string | undefined;
      try {
        const run = scope.run_id ? detail.runs.find(item => item.id === scope.run_id) : detail.runs[0];
        if (scope.run_id && !run) throw new Error("RUN_SCOPE_MISMATCH");
        const trace = run ? await host.call("get_trace", { ...scope, run_id: run.id }) : null;
        const window = BrowserWindow.fromWebContents(event.sender);
        if (!window) throw new Error("WINDOW_CLOSED");
        const result = await dialog.showSaveDialog(window, { title: "导出业务回执", defaultPath: `odoo-business-${scope.business_id}.json`,
          filters: [{ name: "业务回执 JSON", extensions: ["json"] }] });
        if (result.canceled || !result.filePath) return { cancelled: true };
        if (extname(result.filePath).toLowerCase() !== ".json") throw new Error("ARTIFACT_FORMAT_INVALID");
        temporary = `${result.filePath}.${randomUUID()}.tmp`;
        await writeFile(temporary, JSON.stringify({ schema_version: 1, exported_at: new Date().toISOString(),
          scope: { ...scope, run_id: run?.id ?? null }, business: detail, trace }, null, 2), { encoding: "utf8", flag: "wx", mode: 0o600 });
        await rename(temporary, result.filePath);
        temporary = undefined;
        const artifact = await host.call("_record_artifact" as WorkbenchMethod, {
          ...scope, ...(run ? { run_id: run.id } : {}), path: result.filePath, name: basename(result.filePath),
        }).catch(() => { throw new Error(`ARTIFACT_INDEX_FAILED: 回执已保存至 ${result.filePath}，但文件索引保存失败，请保留此路径。`); });
        return { cancelled: false, path: result.filePath, artifact };
      } finally {
        exportInProgress = false;
        if (temporary) await unlink(temporary).catch(() => undefined);
      }
    }
    if (!METHODS.has(request.method)) throw new Error("METHOD_NOT_ALLOWED");
    return host.call(request.method, request.params ?? {});
  });
  ipcMain.handle("workbench:window-control", (event, action: string) => {
    assertTrustedFrame(event);
    const window = BrowserWindow.fromWebContents(event.sender);
    if (!window || !["minimize", "maximize", "close"].includes(action)) throw new Error("WINDOW_ACTION_NOT_ALLOWED");
    if (action === "minimize") window.minimize();
    if (action === "maximize") window.isMaximized() ? window.unmaximize() : window.maximize();
    if (action === "close") window.close();
  });
}

function allowedRendererUrl(): string {
  if (!app.isPackaged && process.env.ELECTRON_RENDERER_URL) return process.env.ELECTRON_RENDERER_URL;
  return pathToFileURL(join(__dirname, "../renderer/index.html")).href;
}

function assertTrustedFrame(event: Electron.IpcMainInvokeEvent): void {
  if (event.senderFrame !== event.sender.mainFrame || event.senderFrame.url !== allowedRendererUrl()) {
    throw new Error("UNTRUSTED_RENDERER");
  }
}

configureUserDataDir();
const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.exit(0);
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });
  app.whenReady().then(() => {
    if (process.argv.includes("--self-check")) {
      void runSelfCheck().then(() => app.exit(0)).catch((cause: unknown) => {
        console.error(`desktop self-check failed: ${cause instanceof Error ? cause.message : "unknown error"}`);
        app.exit(1);
      });
      return;
    }
    session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
    session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
      if (app.isPackaged) {
        details.responseHeaders ??= {};
        details.responseHeaders["Content-Security-Policy"] = [
          "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'",
        ];
      }
      callback({ responseHeaders: details.responseHeaders });
    });
    registerIpc();
    mainWindow = createWindow();
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) mainWindow = createWindow();
    });
  });
}

app.on("before-quit", (event) => {
  event.preventDefault();
  void host.stop().finally(() => app.exit(0));
});

app.on("window-all-closed", () => app.quit());
