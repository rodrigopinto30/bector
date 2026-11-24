import { contextBridge, ipcRenderer } from "electron"

import type { HealerApi } from "../shared/api"

/** Listens to one event of the main process; returns a function that stops listening. */
function subscribe<T>(channel: string, read: (value: unknown) => T, listener: (value: T) => void) {
  const receive = (_event: unknown, value: unknown) => listener(read(value))
  ipcRenderer.on(channel, receive)
  return () => {
    ipcRenderer.removeListener(channel, receive)
  }
}

/**
 * The only thing the page can reach: one function per channel, nothing of Node or of Electron.
 * The key travels to the main process and is never given back.
 */
const api: HealerApi = {
  dockerCheck: () => ipcRenderer.invoke("docker:check"),
  keyStatus: () => ipcRenderer.invoke("key:status"),
  keySave: (key) => ipcRenderer.invoke("key:save", key),
  keyRemove: () => ipcRenderer.invoke("key:remove"),
  stackStatus: () => ipcRenderer.invoke("stack:status"),
  stackStart: (restart) => ipcRenderer.invoke("stack:start", restart === true),
  stackStop: () => ipcRenderer.invoke("stack:stop"),
  projectStatus: () => ipcRenderer.invoke("project:status"),
  projectChoose: () => ipcRenderer.invoke("project:choose"),
  projectReset: () => ipcRenderer.invoke("project:reset"),
  fixStatus: () => ipcRenderer.invoke("fix:status"),
  fixTest: (options) => ipcRenderer.invoke("fix:test", options),
  fixFind: (options) => ipcRenderer.invoke("fix:find", options),
  fixReview: () => ipcRenderer.invoke("fix:review"),
  fixApply: (id, options) => ipcRenderer.invoke("fix:apply", id, options),
  fixCancel: () => ipcRenderer.invoke("fix:cancel"),
  showSection: (section) => ipcRenderer.invoke("section:show", section),
  setContentBounds: (bounds) => ipcRenderer.invoke("layout:content", bounds),
  windowControl: (action) => ipcRenderer.invoke("window:control", action),
  openLink: (url) => ipcRenderer.invoke("link:open", url),
  onLine: (listener) => subscribe("stack:line", String, listener),
  onFixLine: (listener) => subscribe("fix:line", String, listener),
  onWindowState: (listener) =>
    subscribe(
      "window:state",
      (value) => ({ maximized: (value as { maximized?: unknown }).maximized === true }),
      listener,
    ),
  onPanelFailed: (listener) =>
    subscribe(
      "panel:failed",
      () => undefined,
      () => listener(),
    ),
  onShortcut: (listener) => subscribe("shortcut", () => "settings" as const, listener),
}

contextBridge.exposeInMainWorld("healer", api)
