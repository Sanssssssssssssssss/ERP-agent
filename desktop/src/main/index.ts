import { app, BrowserWindow, ipcMain, session } from "electron";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { HostClient } from "./host";
import { publicSettings, saveSettings } from "./settings";
import { assertRequest, canChangeSettings, METHODS } from "./ipc-security";
import { runSelfCheck } from "./self-check";
import type { SettingsInput, WorkbenchMethod } from "../shared/protocol";

const host = new HostClient();
let settingsChanging = false;
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
    minWidth: 1024,
    minHeight: 640,
    frame: false,
    autoHideMenuBar: true,
    title: "Odoo 业务工作台",
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
