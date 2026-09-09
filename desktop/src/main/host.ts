import { app } from "electron";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createInterface } from "node:readline";
import { join } from "node:path";
import type { WorkbenchEvent, WorkbenchMethod, WorkbenchResponse } from "../shared/protocol";
import { secretEnvironment } from "./settings";

type Pending = { resolve: (value: unknown) => void; reject: (error: Error) => void; timer: NodeJS.Timeout };
type EventSink = (event: WorkbenchEvent) => void;

const DEFAULT_TIMEOUT_MS = 120_000;
const GRACEFUL_STOP_TIMEOUT_MS = 3_000;
const FORCED_STOP_TIMEOUT_MS = 5_000;

function error(code: string, message: string): Error & { code: string } {
  const result = new Error(message) as Error & { code: string };
  result.code = code;
  return result;
}

export class HostClient {
  private child: ChildProcessWithoutNullStreams | undefined;
  private pending = new Map<string, Pending>();
  private sinks = new Set<EventSink>();
  private stopping = new WeakSet<ChildProcessWithoutNullStreams>();
  private starting: Promise<void> | undefined;
  private stoppingPromise: Promise<void> | undefined;
  private lifecycleGeneration = 0;

  subscribe(sink: EventSink): () => void {
    this.sinks.add(sink);
    return () => this.sinks.delete(sink);
  }

  isBusy(): boolean {
    return this.pending.size > 0 || Boolean(this.starting) || Boolean(this.stoppingPromise);
  }

  isRunning(): boolean {
    return Boolean(this.child || this.starting);
  }

  async call(method: WorkbenchMethod, params: Record<string, unknown>): Promise<unknown> {
    await this.ensureStarted();
    const child = this.child;
    if (!child?.stdin.writable) throw error("HOST_UNAVAILABLE", "Workbench host is unavailable.");
    const id = randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(error("REQUEST_TIMEOUT", `Workbench request '${method}' timed out.`));
      }, DEFAULT_TIMEOUT_MS);
      this.pending.set(id, { resolve, reject, timer });
      child.stdin.write(`${JSON.stringify({ id, method, params })}\n`, "utf8", (writeError) => {
        if (!writeError) return;
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error("HOST_WRITE_FAILED", "Could not send the request to the workbench host."));
      });
    });
  }

  async stop(): Promise<void> {
    if (this.stoppingPromise) return this.stoppingPromise;
    const generation = ++this.lifecycleGeneration;
    const startup = this.starting;
    const child = this.child;
    this.stoppingPromise = (async () => {
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timer);
        pending.reject(error("HOST_STOPPED", "Workbench host stopped."));
      }
      this.pending.clear();
      if (child) {
        this.child = undefined;
        this.stopping.add(child);
        await this.terminate(child);
      }
      if (startup) {
        try {
          await startup;
        } catch {
          // The generation cancellation and pending rejection are the expected stop path.
        }
      }
      if (generation !== this.lifecycleGeneration) return;
    })().finally(() => {
      this.stoppingPromise = undefined;
    });
    return this.stoppingPromise;
  }

  private async ensureStarted(): Promise<void> {
    if (this.stoppingPromise) {
      await this.stoppingPromise;
      return this.ensureStarted();
    }
    if (this.child) return;
    if (this.starting) return this.starting;
    const generation = this.lifecycleGeneration;
    const promise = this.start(generation);
    let tracked!: Promise<void>;
    tracked = promise.finally(() => {
      if (this.starting === tracked) this.starting = undefined;
    });
    this.starting = tracked;
    return tracked;
  }

  private async start(generation: number): Promise<void> {
    const env = {
      PATH: process.env.PATH ?? "",
      SystemRoot: process.env.SystemRoot ?? "",
      TEMP: process.env.TEMP ?? "",
      TMP: process.env.TMP ?? "",
      PYTHONUTF8: "1",
      ...(await secretEnvironment()),
    };
    if (generation !== this.lifecycleGeneration) throw error("HOST_START_CANCELLED", "Workbench host start was cancelled.");
    const dataDir = join(app.getPath("userData"), "data");
    const packagedHost = join(process.resourcesPath, "host");
    const python = app.isPackaged
      ? join(packagedHost, "python", process.platform === "win32" ? "python.exe" : "python")
      : process.env.WORKBENCH_PYTHON || "python";
    const hostRoot = app.isPackaged ? join(packagedHost, "app") : process.env.WORKBENCH_HOST_ROOT || process.cwd();
    const child = spawn(python, ["-m", "workbench.host", "--data-dir", dataDir], {
      cwd: hostRoot,
      env: { ...env, PYTHONPATH: hostRoot },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    this.child = child;
    const lines = createInterface({ input: child.stdout });
    lines.on("line", (line) => this.handleLine(line));
    child.stderr.on("data", () => undefined);
    child.once("error", (cause) => {
      if (this.child === child) {
        this.child = undefined;
        this.failHost(error("HOST_START_FAILED", `Could not start workbench host: ${cause.message}`));
      }
    });
    child.once("close", (code, signal) => {
      if (this.child === child) {
        this.child = undefined;
        this.failHost(error("HOST_EXITED", `Workbench host exited${code === null ? ` (${signal ?? "unknown signal"})` : ` with code ${code}`}.`));
      }
      if (!this.stopping.has(child)) this.emit({ event: "host_status", data: { status: "crashed", code, signal } });
    });
    try {
      await this.call("health", {});
    } catch (cause) {
      if (this.child === child) this.child = undefined;
      this.stopping.add(child);
      await this.terminate(child).catch(() => undefined);
      throw cause;
    }
    this.emit({ event: "host_status", data: { status: "ready" } });
  }

  private async terminate(child: ChildProcessWithoutNullStreams): Promise<void> {
    if (child.exitCode !== null || child.signalCode !== null) return;
    const closed = new Promise<boolean>((resolve) => {
      child.once("close", () => resolve(true));
    });
    const delay = (milliseconds: number) => new Promise<boolean>((resolve) => setTimeout(() => resolve(false), milliseconds));
    child.stdin.end();
    if (await Promise.race([closed, delay(GRACEFUL_STOP_TIMEOUT_MS)])) return;
    if (process.platform === "win32" && child.pid) {
      const taskkill = join(process.env.SystemRoot || "C:\\Windows", "System32", "taskkill.exe");
      const killer = spawn(taskkill, ["/pid", String(child.pid), "/t", "/f"], { windowsHide: true });
      killer.once("error", () => undefined);
    } else {
      child.kill();
    }
    if (await Promise.race([closed, delay(FORCED_STOP_TIMEOUT_MS)])) return;
    throw error("HOST_STOP_TIMEOUT", "Workbench host did not exit after stop.");
  }

  private handleLine(line: string): void {
    if (!line.trim()) return;
    let message: WorkbenchResponse | WorkbenchEvent;
    try {
      message = JSON.parse(line) as WorkbenchResponse | WorkbenchEvent;
    } catch {
      this.emit({ event: "host_protocol_error", data: { message: "Workbench host returned invalid JSON." } });
      return;
    }
    if ("event" in message) {
      this.emit(message);
      return;
    }
    const pending = this.pending.get(message.id);
    if (!pending) return;
    clearTimeout(pending.timer);
    this.pending.delete(message.id);
    if (message.error) pending.reject(error(message.error.code, safeErrorMessage(message.error.code, message.error.message)));
    else pending.resolve(message.result);
  }

  private failHost(reason: Error): void {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(reason);
    }
    this.pending.clear();
  }

  private emit(event: WorkbenchEvent): void {
    for (const sink of this.sinks) sink(event);
  }
}

const SAFE_ERROR_MESSAGES: Record<string, string> = {
  CONFIG_READ_FAILED: "配置文件读取失败，请检查桌面配置文件。",
  CONFIG_INPUT_INVALID: "配置输入无效，请检查字段。",
  CONFIG_BUSY: "配置正在使用中，请等待当前操作结束。",
  SECURE_STORAGE_UNAVAILABLE: "系统安全存储不可用，无法读取密钥。",
  ODOO_NOT_CONFIGURED: "Odoo 尚未配置，请先填写连接设置。",
  RECORD_NOT_OBSERVED: "该记录未被当前业务读取，无法打开。",
};

const SAFE_LOCAL_ERROR_MESSAGES: Record<string, string> = {
  "KEYERROR\u0000'unknown proposal'": "未找到可处理的业务提案，可能已处理或已过期。",
  "KEYERROR\u0000'unknown session'": "未找到当前会话，请重新选择会话。",
  "KEYERROR\u0000'business does not belong to session'": "业务不属于当前会话，请重新选择会话。",
  "KEYERROR\u0000'unknown run'": "未找到当前运行记录，请刷新业务状态。",
  "KEYERROR\u0000'unknown method'": "主机不支持当前请求，请刷新桌面应用。",
  "VALUEERROR\u0000proposal already decided": "该业务提案已经处理，请刷新会话状态。",
  "RUNTIMEERROR\u0000only one active run is allowed on this host": "主机已有运行中的业务，请等待当前运行结束。",
};

function safeErrorCode(code: string): string {
  const normalized = String(code || "").toUpperCase();
  return /^[A-Z][A-Z0-9_-]{0,63}$/.test(normalized) ? normalized : "HOST_ERROR";
}

export function safeErrorMessage(code: string, message: string): string {
  const safeCode = safeErrorCode(code);
  const mapped = SAFE_ERROR_MESSAGES[safeCode] || SAFE_LOCAL_ERROR_MESSAGES[`${safeCode}\u0000${message}`];
  const text = mapped || "请求失败，请检查当前操作状态后重试。";
  return `[${safeCode}] ${text}`;
}
