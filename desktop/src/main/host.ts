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
    const child = spawn(python, ["-m", "erp_harness.app.host", "--data-dir", dataDir], {
      cwd: hostRoot,
      env,
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
  ODOO_BUSINESS_CONNECTION_MISSING: "当前 Odoo 连接设置不完整，请先配置 URL、数据库和账号。",
  ODOO_BUSINESS_CONNECTION_MISMATCH: "当前业务属于其他 Odoo 连接，请切回原连接或新建业务。",
  ODOO_BUSINESS_CONNECTION_LEGACY: "该历史业务缺少连接归属，历史内容仍可查看；请新建业务继续操作。",
  RECORD_NOT_OBSERVED: "该记录未被当前业务读取，无法打开。",
  MATERIAL_UNAVAILABLE: "业务绑定的材料已缺失或内容已变化，请重新导入后再执行。",
};

const SAFE_LOCAL_ERROR_MESSAGES: Record<string, string> = {
  "VALUEERROR\u0000当前业务尚无已保存的会话记录。": "当前业务尚无已保存的会话记录。",
  "VALUEERROR\u0000会话记录不完整或格式无效，未生成快照；请保留原始日志。": "会话记录不完整或格式无效，未生成快照；请保留原始日志。",
  "VALUEERROR\u0000业务会话快照路径不在当前业务目录。": "快照路径归属不符，无法打开。",
  "KEYERROR\u0000'unknown proposal'": "未找到可处理的业务提案，可能已处理或已过期。",
  "KEYERROR\u0000'unknown session'": "未找到当前会话，请重新选择会话。",
  "KEYERROR\u0000'business does not belong to session'": "业务不属于当前会话，请重新选择会话。",
  "KEYERROR\u0000'unknown run'": "未找到当前运行记录，请刷新业务状态。",
  "KEYERROR\u0000'unknown method'": "主机不支持当前请求，请刷新桌面应用。",
  "VALUEERROR\u0000proposal already decided": "该业务提案已经处理，请刷新会话状态。",
  "RUNTIMEERROR\u0000only one active run is allowed on this host": "主机已有运行中的业务，请等待当前运行结束。",
  "RUNTIMEERROR\u0000当前任务正在执行或等待审批，请完成当前任务后再发送；修改待审批动作请使用审批卡上的修改入口。": "当前任务尚未结束。修改待审批动作请使用审批卡上的修改入口。",
  "RUNTIMEERROR\u0000请等待本轮提案生成完成后再确认。": "请等待本轮提案生成完成后再确认。",
  "VALUEERROR\u0000本轮提案未正常完成，请重新说明需求以生成完整提案。": "本轮提案未完成，请重新说明需求。",
  "VALUEERROR\u0000这份提案已有更新版本，请确认最新提案。": "这份提案已有更新版本，请确认最新提案。",
  "VALUEERROR\u0000需求已有补充，请使用最新需求重新生成提案。": "需求已有补充，请使用最新提案。",
  "VALUEERROR\u0000审批已过期，请先结束旧运行，再重新提出需求。": "审批已过期，请先结束旧运行，再重新提出需求。",
  "VALUEERROR\u0000approval scope is invalid": "当前审批已变化，请读取最新状态。",
  "VALUEERROR\u0000action scope or state is invalid": "当前动作已变化，请读取最新状态。",
  "RUNTIMEERROR\u0000business is blocked by an unresolved write; refresh and reconcile first": "存在结果不确定的写入，请先核对实际落库状态。",
  "VALUEERROR\u0000content_base64 is required": "材料内容为空，请重新选择文件。",
  "VALUEERROR\u0000content_base64 is invalid": "材料编码无效，请重新选择文件。",
  "VALUEERROR\u0000session material limit exceeded": "当前会话已达到材料数量上限。",
  "VALUEERROR\u0000material name is required": "材料文件名不能为空。",
  "VALUEERROR\u0000material name must be a simple file name": "材料文件名无效，请选择 CSV 或 TXT 文件。",
  "VALUEERROR\u0000only UTF-8 CSV and TXT materials are supported": "仅支持 UTF-8 CSV 或 TXT 材料。",
  "VALUEERROR\u0000material must be valid UTF-8": "材料必须是有效的 UTF-8 文本。",
  "VALUEERROR\u0000material exceeds the 2 MiB limit": "材料超过 2 MiB 大小限制。",
  "VALUEERROR\u0000material exceeds the 20000 character limit": "材料超过文本长度限制。",
  "VALUEERROR\u0000material exceeds the 200 row limit": "材料超过 200 行限制。",
  "VALUEERROR\u0000document is not observed in this business": "该单据未被当前业务观测，无法下载。",
  "VALUEERROR\u0000document scope is invalid": "单据下载参数无效。",
  "VALUEERROR\u0000DOCUMENT_PDF_UNAVAILABLE": "当前单据没有可下载的正式 PDF，请改用 CSV 或先在 Odoo 生成正式报表。",
  "VALUEERROR\u0000DOCUMENT_INVOICE_PDF_NOT_GENERATED": "发票已过账，但尚未生成正式 PDF，请先生成发票文件后再下载。",
  "VALUEERROR\u0000DOCUMENT_LINES_TOO_MANY": "单据明细超过 100 行，暂不支持导出。",
  "VALUEERROR\u0000DOCUMENT_LINES_NOT_READ": "单据明细读取不完整，无法生成 CSV。",
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
