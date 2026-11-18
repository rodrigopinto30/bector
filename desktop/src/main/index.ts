import { existsSync } from "node:fs"
import { readFile } from "node:fs/promises"
import { homedir } from "node:os"
import { join } from "node:path"

import {
  app,
  dialog,
  ipcMain,
  Menu,
  net,
  safeStorage,
  session,
  shell,
  type IpcMainInvokeEvent,
} from "electron"

import { checkDocker } from "./docker"
import { Fixer } from "./fix"
import { createHandlers, type Channel } from "./handlers"
import type { Fetch } from "./key"
import { isShellPage } from "./policy"
import { run } from "./process"
import { findProjectRoot, readPanelPort } from "./project"
import { Stack } from "./stack"
import { KeyVault, vaultFile } from "./vault"
import { defaultFolder, folderProbe, textFile, Workspace } from "./workspace"
import { createMainWindow, type MainWindow } from "./window"

/** The app: one window, with its own title bar and menu, that shows the settings or the panel. */
async function start(): Promise<void> {
  const root = findProjectRoot(process.env["HEALER_HOME"], __dirname, existsSync)
  if (root === null) {
    dialog.showErrorBox(
      "Healer was not found",
      "This app needs the Healer project folder. Set HEALER_HOME to it, or run the app from inside the project.",
    )
    app.quit()
    return
  }
  const readEnv = () => readFile(join(root, ".env"), "utf8").catch(() => null)
  const port = readPanelPort(await readEnv())
  const shellFile = join(__dirname, "..", "..", "renderer", "index.html")
  const shellUrl = `file://${shellFile}`
  const preload = join(__dirname, "..", "preload", "index.js")

  const vault = new KeyVault(
    safeStorage,
    vaultFile(join(app.getPath("userData"), "anthropic-key.bin")),
  )
  const workspace = new Workspace({
    store: textFile(join(app.getPath("userData"), "project.json")),
    probe: folderProbe(),
    home: homedir(),
    defaultFolder: async () => defaultFolder(root, await readEnv()),
  })
  const fetchFn: Fetch = (url, init) => net.fetch(url, init)
  let main: MainWindow | null = null

  const stack = new Stack({
    root,
    run,
    readEnv,
    key: () => vault.load(),
    workspace: () => workspace.plan(),
    sameFolder: (a, b) => workspace.same(a, b),
    ping: async (url) => {
      try {
        return (await net.fetch(url, { signal: AbortSignal.timeout(3_000) })).status === 200
      } catch {
        return false
      }
    },
    sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    onLine: (line) => main?.send("stack:line", line),
  })

  const fix = new Fixer({
    root,
    run,
    stack: () => stack.status(),
    confirm: (question) => main?.confirm(question) ?? Promise.resolve(false),
    sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    onLine: (line) => main?.send("fix:line", line),
  })

  const handlers = createHandlers({
    docker: () => checkDocker(run),
    vault,
    stack,
    workspace,
    fix,
    fetchFn,
    window: {
      show: (section) => main?.show(section) ?? Promise.resolve(),
      setBounds: (bounds) => main?.setBounds(bounds),
      control: (action) => main?.control(action),
      chooseFolder: () => main?.chooseFolder() ?? Promise.resolve(null),
    },
    openExternal: (url) => shell.openExternal(url),
  })

  // The page may ask only through these channels, and only if it is the page of the window.
  for (const channel of Object.keys(handlers) as Channel[]) {
    ipcMain.handle(channel, (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      const frame = event.senderFrame
      if (frame === null || frame.parent !== null || !isShellPage(frame.url, shellUrl)) {
        throw new Error("this page may not ask that")
      }
      const handler = handlers[channel] as (...values: unknown[]) => Promise<unknown>
      return handler(...args)
    })
  }

  session.defaultSession.setPermissionRequestHandler((_contents, _permission, answer) =>
    answer(false),
  )
  session.defaultSession.setPermissionCheckHandler(() => false)

  // The window has its own menu on the page, so the system's menu bar is not used.
  Menu.setApplicationMenu(null)
  main = createMainWindow({ preload, shellFile, port })
}

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on("window-all-closed", () => app.quit())
  void app.whenReady().then(start)
}
