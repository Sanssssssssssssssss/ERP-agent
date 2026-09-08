import { contextBridge, ipcRenderer } from "electron";
import type {
  WorkbenchBridge,
  WorkbenchEvent,
  WorkbenchMethod,
} from "../shared/protocol";

const bridge: WorkbenchBridge = {
  call(method: WorkbenchMethod, params = {}) {
    return ipcRenderer.invoke("workbench:call", { method, params });
  },
  subscribe(listener: (event: WorkbenchEvent) => void) {
    const handler = (_event: Electron.IpcRendererEvent, payload: WorkbenchEvent) => {
      listener(payload);
    };
    ipcRenderer.on("workbench:event", handler);
    return () => ipcRenderer.removeListener("workbench:event", handler);
  },
  windowControl(action: "minimize" | "maximize" | "close") {
    return ipcRenderer.invoke("workbench:window-control", action);
  },
};

contextBridge.exposeInMainWorld("workbench", bridge);
