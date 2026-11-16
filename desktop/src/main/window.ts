import { BrowserWindow, nativeTheme, shell, WebContentsView } from "electron"

import type { Bounds, Section, WindowAction } from "../shared/api"
import { fitBounds } from "./layout"
import { PanelLoad } from "./panel-load"
import { isAllowedExternal, isPanelUrl, panelUrl } from "./policy"

export interface MainWindowOptions {
  /** The script that gives the page its bridge to the app. */
  preload: string
  /** The page of the window: its title bar, its menu and its settings. */
  shellFile: string
  port: number
}

export interface MainWindow {
  show(section: Section): Promise<void>
  setBounds(bounds: Bounds): void
  control(action: WindowAction): void
  /** Sends an event to the page of the window. */
  send(channel: string, value?: unknown): void
}

const PREFERENCES = {
  contextIsolation: true,
  nodeIntegration: false,
  sandbox: true,
  webSecurity: true,
} as const

/**
 * One window, as big as the screen, with no frame of the system: the page draws its own title
 * bar, with the window buttons on the right, and its menu on the left. The panel is another
 * view on top of the page's content area. It is a separate web page with no bridge to the app,
 * because it shows text that comes from other computers, and it can only go to the panel's own
 * address.
 */
export function createMainWindow(options: MainWindowOptions): MainWindow {
  const background = nativeTheme.shouldUseDarkColors ? "#1b1a26" : "#fbf9f4"
  const window = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    show: false,
    frame: false,
    backgroundColor: background,
    title: "Healer",
    webPreferences: { ...PREFERENCES, preload: options.preload },
  })
  const panel = new WebContentsView({ webPreferences: { ...PREFERENCES } })
  panel.setBackgroundColor(background)
  panel.setVisible(false)
  window.contentView.addChildView(panel)

  let section: Section = "settings"
  const load = new PanelLoad()
  let area: Bounds = { x: 0, y: 0, width: 0, height: 0 }

  const place = () => {
    const content = window.getContentBounds()
    panel.setBounds(fitBounds(area, { width: content.width, height: content.height }))
  }
  const send = (channel: string, value?: unknown) => {
    if (!window.isDestroyed()) {
      window.webContents.send(channel, value)
    }
  }
  const sendState = () => send("window:state", { maximized: window.isMaximized() })

  // The page of the window can go nowhere and open nothing.
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }))
  window.webContents.on("will-navigate", (event) => event.preventDefault())
  window.webContents.on("will-attach-webview", (event) => event.preventDefault())

  // The panel can only go to its own address, and open in the browser only what is allowed.
  panel.webContents.setWindowOpenHandler(({ url }) => {
    if (isAllowedExternal(url)) {
      void shell.openExternal(url)
    }
    return { action: "deny" }
  })
  panel.webContents.on("will-navigate", (event, url) => {
    if (!isPanelUrl(url, options.port)) {
      event.preventDefault()
    }
  })
  panel.webContents.on("did-start-loading", () => load.started())
  panel.webContents.on("did-stop-loading", () => load.stopped())
  panel.webContents.on("did-fail-load", (_event, code, _text, _url, isMainFrame) => {
    if (isMainFrame && code !== -3) {
      load.failedToLoad()
      panel.setVisible(false)
      send("panel:failed")
    }
  })

  // Ctrl+, opens the settings from anywhere, and Ctrl+R reloads the panel.
  for (const contents of [window.webContents, panel.webContents]) {
    contents.on("before-input-event", (event, input) => {
      if (input.type !== "keyDown" || !(input.control || input.meta)) {
        return
      }
      if (input.key === ",") {
        event.preventDefault()
        send("shortcut", "settings")
      } else if (input.key.toLowerCase() === "r" && section === "panel" && load.loaded) {
        event.preventDefault()
        panel.webContents.reload()
      }
    })
  }

  window.on("maximize", sendState)
  window.on("unmaximize", sendState)
  window.on("resize", place)
  window.webContents.on("did-finish-load", sendState)
  window.once("ready-to-show", () => {
    window.maximize()
    window.show()
  })
  void window.loadFile(options.shellFile)

  return {
    async show(next) {
      section = next
      if (next === "settings") {
        panel.setVisible(false)
        return
      }
      if (load.needsLoad()) {
        load.requested()
        panel.webContents.loadURL(panelUrl(options.port)).catch(() => {
          // did-fail-load reports it.
        })
      }
      panel.setVisible(true)
    },
    setBounds(bounds) {
      area = bounds
      place()
    },
    control(action) {
      if (action === "minimize") {
        window.minimize()
      } else if (action === "toggleMaximize") {
        if (window.isMaximized()) {
          window.unmaximize()
        } else {
          window.maximize()
        }
      } else {
        window.close()
      }
    },
    send,
  }
}
