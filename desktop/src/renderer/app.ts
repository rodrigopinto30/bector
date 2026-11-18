import type {
  DockerStatus,
  FixKind,
  FixOutcome,
  FixResult,
  HealerApi,
  KeyCheck,
  KeySave,
  KeyStatus,
  ProjectChoice,
  ProjectInfo,
  Section,
  StackResult,
  StackStatus,
} from "../shared/api"

declare global {
  interface Window {
    healer: HealerApi
  }
}

export type Tone = "good" | "warn" | "bad" | "idle"

export interface Card {
  tone: Tone
  title: string
  lines: string[]
}

/** What an action said, shown so that it is clear whether it worked. */
export interface Notice {
  tone: Tone
  title: string
  text: string
}

/**
 * The icon of each state, drawn as lines. Each has its own shape, so a state is never told by
 * color alone, and none of them is a cross: the only cross on the page is the one that closes.
 */
export const STATE_ICONS: Record<Tone, readonly string[]> = {
  good: ["M5 12.5l4.5 4.5L19 7.5"],
  warn: ["M12 4.5L3.5 19.5h17z", "M12 10v4", "M12 17h.01"],
  bad: ["M12 3.5a8.5 8.5 0 1 0 0 17a8.5 8.5 0 0 0 0-17z", "M12 8v5", "M12 16.5h.01"],
  idle: ["M6 12h.01", "M12 12h.01", "M18 12h.01"],
}

export const STATE_LABELS: Record<Tone, string> = {
  good: "Done",
  warn: "Warning",
  bad: "Error",
  idle: "In progress",
}

/** The sections of the menu. More can be added here: each needs its icon and its content. */
export const SECTIONS: ReadonlyArray<{ id: Section; label: string }> = [
  { id: "panel", label: "Panel" },
  { id: "project", label: "Project" },
  { id: "fix", label: "Fix" },
  { id: "settings", label: "Settings" },
]

const CONSOLE_URL = "https://console.anthropic.com/settings/keys"
const DOCKER_GUIDE_URL = "https://docs.docker.com/engine/install/"
const SIDEBAR_KEY = "healer.sidebar"

export function dockerCard(status: DockerStatus | null): Card {
  if (status === null) {
    return { tone: "idle", title: "Checking Docker…", lines: [] }
  }
  if (status.ok) {
    return {
      tone: "good",
      title: "Docker is ready",
      lines: [status.version ? `Version ${status.version}.` : "It answers without sudo."],
    }
  }
  return { tone: "bad", title: status.message, lines: [status.hint] }
}

export function checkMessage(check: KeyCheck): { tone: Tone; text: string } {
  switch (check.state) {
    case "valid":
      return { tone: "good", text: "Your key works." }
    case "limited":
      return {
        tone: "good",
        text: "Your key works, but Anthropic is limiting its requests right now.",
      }
    case "invalid":
      return {
        tone: "bad",
        text: "Anthropic does not accept this key. Check that you copied all of it.",
      }
    case "forbidden":
      return { tone: "bad", text: "Anthropic refused this key: it may not be used here." }
    case "unavailable":
      return {
        tone: "warn",
        text: "Anthropic is not answering well right now. Try again in a moment.",
      }
    case "offline":
      return {
        tone: "warn",
        text: "No connection to Anthropic. Check your internet and try again.",
      }
    case "malformed":
      return {
        tone: "bad",
        text: "This does not look like an Anthropic key. It starts with sk-ant-.",
      }
  }
}

/**
 * What to tell after a key was sent to be saved. It says whether the key was saved, which is
 * not the same as whether it works: a key can work and still not be saved.
 */
export function saveNotice(
  outcome: KeySave,
  servicesRunning: boolean,
  before: KeyStatus | null = null,
): Notice {
  const works = outcome.check.state === "valid" || outcome.check.state === "limited"
  const status = outcome.status ?? before
  if (outcome.saved) {
    const limited =
      outcome.check.state === "limited"
        ? " Anthropic is limiting its requests right now, but the key works."
        : ""
    const next = servicesRunning
      ? " Restart the services below to start using it."
      : " Start the services below to use it."
    return {
      tone: "good",
      title: "Key saved",
      text: `Anthropic accepts it, and it is stored encrypted in this computer's secret store as ${status?.masked ?? ""}.${limited}${next}`,
    }
  }
  if (works) {
    return {
      tone: "bad",
      title: "The key works, but it was not saved",
      text: status?.reason || "This computer's secret store refused it.",
    }
  }
  const said = checkMessage(outcome.check)
  const kept = status?.stored ? " The key you had saved is unchanged." : ""
  return { tone: said.tone, title: "The key was not saved", text: said.text + kept }
}

export function removeNotice(): Notice {
  return { tone: "good", title: "Key removed", text: "This app no longer stores it." }
}

export const SAVE_FAILED_NOTICE: Notice = {
  tone: "bad",
  title: "The key was not saved",
  text: "The app could not check it. Try again.",
}

/** Shown when the main process fails in a way nobody planned for, so the page is never stuck. */
export const KEY_CHECK_FAILED: KeyStatus = {
  vault: "unavailable",
  reason: "The app could not check the secret store.",
  stored: false,
  masked: "",
}

export const ACTION_FAILED: StackResult = {
  ok: false,
  message: "The app could not do that. Try again.",
}

export const CHECKING_NOTICE: Notice = {
  tone: "idle",
  title: "Checking your key…",
  text: "Asking Anthropic whether it accepts it.",
}

export const EMPTY_NOTICE: Notice = {
  tone: "warn",
  title: "There is no key to check",
  text: "Paste your Anthropic key in the field, then press the button.",
}

/**
 * The message about the last thing done with the key, or with the project. It stays while it is
 * the latest news, and goes as soon as it no longer is: when the user moves to another section
 * or starts to type another key. While something is being checked it cannot be dismissed,
 * because its answer is due.
 */
export class Message {
  notice: Notice | null = null
  checking = false

  begin(): void {
    this.checking = true
    this.notice = CHECKING_NOTICE
  }

  show(notice: Notice): void {
    this.checking = false
    this.notice = notice
  }

  /** True if there was a message and it went. */
  dismiss(): boolean {
    if (this.notice === null || this.checking) {
      return false
    }
    this.notice = null
    return true
  }
}

/**
 * ``systemSeesKey``: the services already have a key. With none saved here, that key was not
 * saved by this app, as when it comes from the .env file.
 */
export function keyCard(status: KeyStatus | null, systemSeesKey = false): Card {
  if (status === null) {
    return { tone: "idle", title: "Checking your key…", lines: [] }
  }
  if (status.vault === "unavailable") {
    return {
      tone: "warn",
      title: "Your key cannot be saved safely here",
      lines: [status.reason, "You can still use the app without a key."],
    }
  }
  if (status.stored) {
    return {
      tone: "good",
      title: "Your Anthropic key is saved",
      lines: [status.masked, "It is kept in this computer's secret store."],
    }
  }
  if (systemSeesKey && !status.reason) {
    return {
      tone: "good",
      title: "The services already have a key",
      lines: [
        "This app did not save it: it comes, for example, from your .env file.",
        "Paste one below to keep it in this computer's secret store instead.",
      ],
    }
  }
  return {
    tone: "warn",
    title: "No Anthropic key yet",
    lines: [
      ...(status.reason ? [status.reason] : []),
      "Without a key the system reuses fixes from its cache and from your team, but it cannot ask Claude for a new one.",
    ],
  }
}

/** What a restart of the services is waiting for. */
export type Waiting = "key" | "project"

const WAITING_TEXT: Record<Waiting, string> = {
  key: "A new key is waiting: restart the services to use it.",
  project: "The project folder chosen here is not in use yet: restart the services to use it.",
}

export function stackCard(
  status: StackStatus | null,
  busy: boolean,
  message: string,
  waiting: readonly Waiting[] = [],
): Card {
  if (busy) {
    return {
      tone: "idle",
      title: "Starting the services…",
      lines: ["The first time it builds the images, which can take a few minutes."],
    }
  }
  if (status === null) {
    return { tone: "idle", title: "The services", lines: message ? [message] : [] }
  }
  const extra = message ? [message] : []
  if (status.running) {
    const key =
      status.keySeen === true
        ? "The system sees your Anthropic key."
        : "The system does not see an Anthropic key."
    const pending = waiting.map((reason) => WAITING_TEXT[reason])
    return { tone: "good", title: "The services are running", lines: [key, ...pending, ...extra] }
  }
  const up = status.services.length ? status.services.join(", ") : "none"
  return { tone: "warn", title: "The services are stopped", lines: [`Running: ${up}.`, ...extra] }
}

/** The last part of a path: the name of the folder. */
export function folderName(path: string): string {
  const parts = path.split("/").filter(Boolean)
  return parts[parts.length - 1] ?? path
}

/** What the services do about the project folder, told next to the folder. */
function servicesAndFolder(stack: StackStatus | null, chosen: boolean): string[] {
  if (stack === null || !stack.running) {
    return ["The services are stopped."]
  }
  if (stack.workspaceMatches === true) {
    return ["The services are working on it."]
  }
  if (stack.workspaceMatches === false) {
    return [
      chosen
        ? "The services still use another folder: restart them to use this one."
        : "The services still use another folder: restart them to go back to this one.",
    ]
  }
  return []
}

export function projectCard(info: ProjectInfo | null, stack: StackStatus | null): Card {
  if (info === null) {
    return { tone: "idle", title: "Checking your project…", lines: [] }
  }
  if (info.problem !== "") {
    return {
      tone: "bad",
      title: "The project folder cannot be used",
      lines: [info.folder, info.problem, "Choose it again, or go back to the default folder."],
    }
  }
  const chosen = info.chosen !== null
  const services = servicesAndFolder(stack, chosen)
  const mismatch = stack?.running === true && stack.workspaceMatches === false
  if (!chosen) {
    return {
      tone: mismatch ? "warn" : "idle",
      title: "The default folder",
      lines: [
        info.folder,
        "Put your project there, or choose the folder where it already is.",
        ...services,
      ],
    }
  }
  return {
    tone: info.hints.length > 0 || mismatch ? "warn" : "good",
    title: `Your project: ${folderName(info.folder)}`,
    lines: [info.folder, ...info.hints, ...services],
  }
}

/** What to tell after the folder dialog. Nothing when it was closed without a choice. */
export function projectNotice(choice: ProjectChoice, servicesRunning: boolean): Notice | null {
  if (choice.changed) {
    const next = servicesRunning
      ? " Restart the services to use it."
      : " Start the services to use it."
    return {
      tone: "good",
      title: "Project chosen",
      text: `${choice.project.folder} is now the project.${next}`,
    }
  }
  if (choice.message !== "") {
    return { tone: "bad", title: "That folder was not chosen", text: choice.message }
  }
  return null
}

export const PROJECT_RESET_NOTICE: Notice = {
  tone: "good",
  title: "Back to the default folder",
  text: "The services use the default folder again once they restart.",
}

export const PROJECT_CHECK_FAILED: Notice = {
  tone: "bad",
  title: "That folder was not chosen",
  text: "The app could not do that. Try again.",
}

/** Whether the tests can be run now: Docker, the services and the folder are all in order. */
export function canFix(
  docker: DockerStatus | null,
  stack: StackStatus | null,
  project: ProjectInfo | null,
): boolean {
  return (
    docker?.ok === true &&
    stack?.running === true &&
    stack.workspaceMatches !== false &&
    project !== null &&
    project.problem === ""
  )
}

export function fixCard(
  docker: DockerStatus | null,
  stack: StackStatus | null,
  project: ProjectInfo | null,
  running: FixKind | null,
): Card {
  if (running !== null) {
    return {
      tone: "idle",
      title: running === "test" ? "Running the tests…" : "Looking for a fix…",
      lines: [
        "It can take a few minutes. What the system prints is shown below.",
        "Your files are not changed.",
      ],
    }
  }
  if (docker !== null && !docker.ok) {
    return {
      tone: "warn",
      title: "Docker is not working",
      lines: ["Open Settings to see why."],
    }
  }
  if (docker === null || project === null) {
    return { tone: "idle", title: "Checking your project…", lines: [] }
  }
  if (project.problem !== "") {
    return {
      tone: "bad",
      title: "The project folder cannot be used",
      lines: [project.problem, "Choose it again in Project."],
    }
  }
  if (stack?.running !== true) {
    return {
      tone: "warn",
      title: "The services are stopped",
      lines: ["Start them in Settings to run the tests of your project."],
    }
  }
  if (stack.workspaceMatches === false) {
    return {
      tone: "warn",
      title: "The services use another folder",
      lines: [`Restart them in Settings to work on ${folderName(project.folder)}.`],
    }
  }
  return {
    tone: "good",
    title: `Ready: ${folderName(project.folder)}`,
    lines: [
      project.folder,
      "The tests run on a copy of the project. Looking for a fix does not change your files either: the fix is only saved.",
    ],
  }
}

const FIX_TITLES: Record<FixOutcome, { tone: Tone; title: string } | null> = {
  passed: { tone: "good", title: "The tests pass" },
  failed: { tone: "warn", title: "The tests fail" },
  fixed: { tone: "good", title: "A fix was found" },
  nothing: { tone: "good", title: "Nothing to fix" },
  unfixed: { tone: "warn", title: "No fix was found" },
  cancelled: { tone: "warn", title: "Stopped" },
  declined: null,
  refused: { tone: "warn", title: "It was not started" },
  error: { tone: "bad", title: "It could not run" },
}

/** What to tell after a run. Nothing when the user said no in the dialog. */
export function fixNotice(result: FixResult): Notice | null {
  const said = FIX_TITLES[result.outcome]
  return said === null ? null : { tone: said.tone, title: said.title, text: result.message }
}

export const FIX_FAILED: FixResult = {
  kind: "test",
  outcome: "error",
  message: "The app could not do that. Try again.",
}

/** Shown in place of the panel when it cannot be loaded. */
export const PANEL_DOWN: Card = {
  tone: "warn",
  title: "The panel is not running",
  lines: ["The services are stopped or still starting. Open Settings to start them."],
}

/** Starting needs Docker to work and nothing else going on. */
export function canStart(docker: DockerStatus | null, busy: boolean): boolean {
  return docker?.ok === true && !busy
}

export function controlLabel(maximized: boolean): string {
  return maximized ? "Restore" : "Maximize"
}

// ---- the page ----

const SVG_NS = "http://www.w3.org/2000/svg"

/** Simple line icons, drawn here so that the page needs nothing to be loaded. */
export const ICONS = {
  logo: ["M5 19c0-8 5-13 14-14 0 9-5 14-14 14z", "M5 19l7-7"],
  menu: ["M4 6h16", "M4 12h16", "M4 18h16"],
  project: ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
  fix: [
    "M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z",
  ],
  panel: ["M4 4h7v9H4z", "M13 4h7v5h-7z", "M13 11h7v9h-7z", "M4 15h7v5H4z"],
  settings: [
    "M4 7h9",
    "M17 7h3",
    "M15 4.5v5",
    "M4 12h3",
    "M11 12h9",
    "M9 9.5v5",
    "M4 17h11",
    "M19 17h1",
    "M17 14.5v5",
  ],
  minimize: ["M5 12h14"],
  maximize: ["M6 6h12v12H6z"],
  restore: ["M9 9h9v9H9z", "M6 15V6h9"],
  close: ["M6 6l12 12", "M18 6L6 18"],
} as const

function icon(paths: readonly string[], className = "icon"): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg")
  svg.setAttribute("viewBox", "0 0 24 24")
  svg.setAttribute("fill", "none")
  svg.setAttribute("stroke", "currentColor")
  svg.setAttribute("stroke-width", "2")
  svg.setAttribute("stroke-linecap", "round")
  svg.setAttribute("stroke-linejoin", "round")
  svg.setAttribute("class", className)
  svg.setAttribute("aria-hidden", "true")
  for (const d of paths) {
    const path = document.createElementNS(SVG_NS, "path")
    path.setAttribute("d", d)
    svg.append(path)
  }
  return svg
}

/** The icon of a state, in a round chip. It is not a button. */
function symbol(tone: Tone): HTMLElement {
  const element = document.createElement("span")
  element.className = `symbol ${tone}`
  element.setAttribute("role", "img")
  element.setAttribute("aria-label", STATE_LABELS[tone])
  element.append(icon(STATE_ICONS[tone], "icon state"))
  return element
}

function node<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className: string,
  text = "",
  ...children: Node[]
): HTMLElementTagNameMap[K] {
  const element = document.createElement(tag)
  element.className = className
  element.textContent = text
  element.append(...children)
  return element
}

function button(
  label: string,
  onClick: () => void,
  kind: "primary" | "secondary" | "ghost" = "secondary",
  disabled = false,
): HTMLButtonElement {
  const element = node("button", `button ${kind}`, label)
  element.type = "button"
  element.disabled = disabled
  element.addEventListener("click", onClick)
  return element
}

/** The buttons of a card, in one row. Nothing if there are none. */
function actions(...buttons: Node[]): Node[] {
  return buttons.length ? [node("div", "actions", "", ...buttons)] : []
}

function draw(into: HTMLElement, card: Card, ...controls: Node[]): void {
  into.className = `card ${card.tone}`
  into.replaceChildren(
    node("h2", "title", "", symbol(card.tone), document.createTextNode(card.title)),
    ...card.lines.map((line) => node("p", "line", line)),
    ...controls,
  )
}

/** A message about what was done. With ``onDismiss`` it has a button to close it. */
function banner(notice: Notice, onDismiss?: () => void): HTMLElement {
  const element = node(
    "div",
    `banner ${notice.tone}`,
    "",
    symbol(notice.tone),
    node(
      "div",
      "banner-body",
      "",
      node("p", "banner-title", notice.title),
      node("p", "banner-text", notice.text),
    ),
  )
  element.setAttribute("role", "status")
  if (onDismiss !== undefined) {
    const close = node("button", "banner-close")
    close.type = "button"
    close.setAttribute("aria-label", "Dismiss this message")
    close.title = "Dismiss"
    close.append(icon(ICONS.close))
    close.addEventListener("click", onDismiss)
    element.append(close)
  }
  return element
}

function remembered(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_KEY) !== "collapsed"
  } catch {
    return true
  }
}

function remember(expanded: boolean): void {
  try {
    window.localStorage.setItem(SIDEBAR_KEY, expanded ? "expanded" : "collapsed")
  } catch {
    // The choice is only not kept.
  }
}

function main(): void {
  const api = window.healer
  const byId = (id: string) => document.getElementById(id) as HTMLElement
  const shell = byId("shell")
  const content = byId("content")
  const views = {
    loading: byId("loading"),
    project: byId("project"),
    fix: byId("fix"),
    settings: byId("settings"),
    note: byId("panel-note"),
  }
  const sections = {
    project: byId("project-card"),
    fix: byId("fix-card"),
    fixLog: byId("fix-log"),
    docker: byId("docker"),
    key: byId("key"),
    stack: byId("stack"),
    log: byId("log"),
  }
  const keyMessage = new Message()
  const projectMessage = new Message()
  const fixMessage = new Message()
  const state = {
    view: "loading" as "loading" | Section,
    initial: true,
    panelFailed: false,
    maximized: false,
    expanded: remembered(),
    docker: null as DockerStatus | null,
    key: null as KeyStatus | null,
    project: null as ProjectInfo | null,
    choosing: false,
    /** What is running on the project now, and whether it was asked to stop. */
    fix: null as FixKind | null,
    stopping: false,
    stack: null as StackStatus | null,
    busy: false,
    message: "",
    restart: false,
  }

  // ---- the title bar ----
  byId("brand").replaceChildren(icon(ICONS.logo, "icon logo"), node("span", "brand-name", "Healer"))
  const control = (
    label: string,
    paths: readonly string[],
    action: Parameters<HealerApi["windowControl"]>[0],
    extra = "",
  ) => {
    const element = node("button", `control ${extra}`.trim())
    element.type = "button"
    element.setAttribute("aria-label", label)
    element.title = label
    element.append(icon(paths))
    element.addEventListener("click", () => void api.windowControl(action))
    return element
  }
  const maximize = control("Maximize", ICONS.maximize, "toggleMaximize")
  byId("controls").append(
    control("Minimize", ICONS.minimize, "minimize"),
    maximize,
    control("Close", ICONS.close, "close", "close"),
  )
  function renderControls(): void {
    const label = controlLabel(state.maximized)
    maximize.setAttribute("aria-label", label)
    maximize.title = label
    maximize.replaceChildren(icon(state.maximized ? ICONS.restore : ICONS.maximize))
  }

  // ---- the menu on the left ----
  function navButton(label: string, paths: readonly string[], onClick: () => void) {
    const element = node("button", "nav-item", "", icon(paths), node("span", "nav-label", label))
    element.type = "button"
    element.title = label
    element.setAttribute("aria-label", label)
    element.addEventListener("click", onClick)
    return element
  }
  const toggle = navButton("Menu", ICONS.menu, () => setExpanded(!state.expanded))
  const navigation = new Map<Section, HTMLButtonElement>()
  for (const item of SECTIONS) {
    navigation.set(
      item.id,
      navButton(item.label, ICONS[item.id], () => void go(item.id)),
    )
  }
  byId("sidebar").append(toggle, node("div", "nav-sep"), ...navigation.values())

  function setExpanded(expanded: boolean): void {
    state.expanded = expanded
    shell.classList.toggle("expanded", expanded)
    toggle.setAttribute("aria-expanded", String(expanded))
    remember(expanded)
  }
  setExpanded(state.expanded)

  // ---- the panel is shown by the app exactly over the content area ----
  let lastBounds = ""
  function sendBounds(): void {
    const area = content.getBoundingClientRect()
    const bounds = { x: area.left, y: area.top, width: area.width, height: area.height }
    const same = JSON.stringify(bounds)
    if (same !== lastBounds) {
      lastBounds = same
      void api.setContentBounds(bounds)
    }
  }
  new ResizeObserver(sendBounds).observe(content)
  window.addEventListener("resize", sendBounds)

  // ---- what each section shows ----
  /** Removes the message of the last attempt from the key card, without redrawing the field. */
  function dismissMessage(): void {
    if (keyMessage.dismiss()) {
      sections.key.querySelector(".banner")?.remove()
    }
  }

  /** The close button of the message: it goes, and the field is ready for another key. */
  function closeMessage(): void {
    dismissMessage()
    sections.key.querySelector("input")?.focus()
  }

  /** The messages about the last thing done go when the user moves to another section. */
  function dismissMessages(): void {
    dismissMessage()
    if (projectMessage.dismiss()) {
      sections.project.querySelector(".banner")?.remove()
    }
  }

  async function go(section: Section): Promise<void> {
    if (section !== state.view) {
      dismissMessages()
    }
    state.view = section
    if (section === "panel") {
      state.panelFailed = false
    }
    renderView()
    sendBounds()
    await api.showSection(section)
  }

  function renderView(): void {
    views.loading.hidden = state.view !== "loading"
    views.project.hidden = state.view !== "project"
    views.fix.hidden = state.view !== "fix"
    views.settings.hidden = state.view !== "settings"
    const down = state.view === "panel" && state.panelFailed
    views.note.hidden = !down
    if (down) {
      const card = node("section", "card")
      draw(
        card,
        PANEL_DOWN,
        button("Go to Settings", () => void go("settings"), "primary"),
      )
      views.note.replaceChildren(card)
    }
    for (const [id, element] of navigation) {
      if (id === state.view) {
        element.setAttribute("aria-current", "page")
      } else {
        element.removeAttribute("aria-current")
      }
    }
  }

  function render(): void {
    renderControls()
    renderProject()
    renderFix()
    renderDocker()
    renderKey()
    renderStack()
    renderView()
  }

  function renderProject(): void {
    const info = state.project
    const controls: Node[] = []
    if (projectMessage.notice !== null) {
      controls.push(
        banner(projectMessage.notice, () => {
          dismissMessages()
        }),
      )
    }
    if (info !== null) {
      controls.push(
        ...actions(
          button(
            state.choosing ? "Choosing…" : "Choose a folder…",
            () => void chooseProject(),
            "primary",
            state.choosing,
          ),
          ...(info.chosen !== null
            ? [button("Use the default folder", () => void resetProject(), "ghost", state.choosing)]
            : []),
        ),
      )
    }
    draw(sections.project, projectCard(info, state.stack), ...controls)
  }

  function renderFix(): void {
    const controls: Node[] = []
    if (fixMessage.notice !== null && state.fix === null) {
      controls.push(
        banner(fixMessage.notice, () => {
          fixMessage.dismiss()
          renderFix()
        }),
      )
    }
    const ready = canFix(state.docker, state.stack, state.project) && !state.busy
    if (state.fix !== null) {
      controls.push(
        ...actions(
          button(
            state.stopping ? "Stopping…" : "Stop",
            () => void stopFix(),
            "secondary",
            state.stopping,
          ),
        ),
      )
    } else {
      controls.push(
        ...actions(
          button("Run the tests", () => void runFix("test"), "primary", !ready),
          button("Look for a fix", () => void runFix("fix"), "secondary", !ready),
          ...(ready ? [] : [button("Open Settings", () => void go("settings"), "ghost")]),
        ),
      )
    }
    draw(sections.fix, fixCard(state.docker, state.stack, state.project, state.fix), ...controls)
  }

  /** What was run belonged to another project once the project changes. */
  function clearFix(): void {
    if (state.fix === null) {
      fixMessage.dismiss()
      sections.fixLog.textContent = ""
      sections.fixLog.hidden = true
    }
  }

  async function runFix(kind: FixKind): Promise<void> {
    if (state.fix !== null) {
      return
    }
    state.fix = kind
    state.stopping = false
    fixMessage.dismiss()
    sections.fixLog.textContent = ""
    sections.fixLog.hidden = true
    render()
    const stopListening = api.onFixLine((line) => append(sections.fixLog, line))
    let result: FixResult
    try {
      result = await (kind === "test" ? api.fixTest() : api.fixFind())
    } catch {
      result = { ...FIX_FAILED, kind }
    }
    stopListening()
    state.fix = null
    state.stopping = false
    const notice = fixNotice(result)
    if (notice !== null) {
      fixMessage.show(notice)
    }
    if (result.outcome === "refused") {
      await refreshStack()
    }
    render()
  }

  async function stopFix(): Promise<void> {
    if (state.fix === null || state.stopping) {
      return
    }
    state.stopping = true
    renderFix()
    await api.fixCancel().catch(() => undefined)
  }

  /** What a restart of the services would apply: a new key, and a folder they do not have yet. */
  function waiting(): Waiting[] {
    return [
      ...(state.restart ? (["key"] as const) : []),
      ...(state.stack?.running === true && state.stack.workspaceMatches === false
        ? (["project"] as const)
        : []),
    ]
  }

  /** The services may now have another folder than the chosen one: ask them again. */
  async function refreshStack(): Promise<void> {
    if (state.docker?.ok === true) {
      state.stack = await api.stackStatus().catch(() => state.stack)
    }
  }

  async function chooseProject(): Promise<void> {
    if (state.choosing) {
      return
    }
    state.choosing = true
    projectMessage.dismiss()
    renderProject()
    try {
      const choice = await api.projectChoose()
      state.project = choice.project
      if (choice.changed) {
        clearFix()
      }
      const notice = projectNotice(choice, state.stack?.running === true)
      if (notice !== null) {
        projectMessage.show(notice)
      }
    } catch {
      projectMessage.show(PROJECT_CHECK_FAILED)
    }
    state.choosing = false
    await refreshStack()
    render()
  }

  async function resetProject(): Promise<void> {
    if (state.choosing) {
      return
    }
    state.choosing = true
    renderProject()
    try {
      state.project = await api.projectReset()
      clearFix()
      projectMessage.show(PROJECT_RESET_NOTICE)
    } catch {
      projectMessage.show(PROJECT_CHECK_FAILED)
    }
    state.choosing = false
    await refreshStack()
    render()
  }

  function renderDocker(): void {
    const docker = state.docker
    draw(
      sections.docker,
      dockerCard(docker),
      ...(docker !== null && !docker.ok
        ? actions(
            button("Check again", () => void refresh(), "primary"),
            ...(docker.problem === "missing"
              ? [button("Install guide", () => void api.openLink(DOCKER_GUIDE_URL), "ghost")]
              : []),
          )
        : []),
    )
  }

  function renderKey(): void {
    const status = state.key
    const controls: Node[] = []
    if (keyMessage.notice !== null) {
      controls.push(banner(keyMessage.notice, keyMessage.checking ? undefined : closeMessage))
    }
    if (status !== null && status.vault === "ready") {
      const input = node("input", "input")
      input.type = "password"
      input.autocomplete = "off"
      input.spellcheck = false
      input.placeholder = "Paste your key, sk-ant-…"
      input.setAttribute("aria-label", "Anthropic key")
      input.disabled = keyMessage.checking
      const save = () => void saveKey(input)
      input.addEventListener("input", dismissMessage)
      input.addEventListener("keydown", (event) => event.key === "Enter" && save())
      const label = status.stored ? "Check and replace" : "Check and save"
      controls.push(
        input,
        ...actions(
          button(keyMessage.checking ? "Checking…" : label, save, "primary", keyMessage.checking),
          button("Get a key", () => void api.openLink(CONSOLE_URL), "ghost"),
          ...(status.stored
            ? [button("Remove it", () => void removeKey(), "ghost", keyMessage.checking)]
            : []),
        ),
      )
    }
    if (status !== null && status.vault === "unavailable") {
      controls.push(...actions(button("Check again", () => void checkKey(), "ghost")))
    }
    draw(sections.key, keyCard(status, state.stack?.keySeen === true), ...controls)
    sections.key.querySelector(".banner")?.scrollIntoView({ block: "nearest" })
  }

  function renderStack(): void {
    const docker = state.docker
    /** Whether the button would start or restart the services, which would cut a run short. */
    const restarts = waiting().length > 0 || state.stack?.running !== true
    const label =
      waiting().length > 0
        ? "Restart and open the panel"
        : state.stack?.running
          ? "Open the panel"
          : "Start and open the panel"
    draw(
      sections.stack,
      stackCard(state.stack, state.busy, state.message, waiting()),
      ...actions(
        button(
          label,
          () => void start(),
          "primary",
          !canStart(docker, state.busy) || (state.fix !== null && restarts),
        ),
        ...(state.stack?.services.length
          ? [
              button(
                "Stop the services",
                () => void stop(),
                "ghost",
                state.busy || state.fix !== null,
              ),
            ]
          : []),
      ),
    )
  }

  async function saveKey(input: HTMLInputElement): Promise<void> {
    if (keyMessage.checking) {
      return
    }
    const typed = input.value
    input.value = ""
    if (!typed.trim()) {
      keyMessage.show(EMPTY_NOTICE)
      renderKey()
      sections.key.querySelector("input")?.focus()
      return
    }
    keyMessage.begin()
    renderKey()
    let saved = false
    try {
      const outcome = await api.keySave(typed)
      if (outcome.status !== null) {
        state.key = outcome.status
      }
      keyMessage.show(saveNotice(outcome, state.stack?.running === true, state.key))
      state.restart ||= outcome.saved
      saved = outcome.saved
    } catch {
      keyMessage.show(SAVE_FAILED_NOTICE)
    }
    render()
    if (!saved) {
      sections.key.querySelector("input")?.focus()
    }
  }

  async function removeKey(): Promise<void> {
    state.key = await api.keyRemove()
    keyMessage.show(removeNotice())
    state.restart = state.stack?.running === true
    render()
  }

  /** Adds a line to the end of a log, keeping the last 200. */
  function append(log: HTMLElement, line: string): void {
    const before = log.textContent ?? ""
    log.hidden = false
    const lines = (before === "" ? [] : before.split("\n")).concat(line).slice(-200)
    log.textContent = lines.join("\n")
    log.scrollTop = log.scrollHeight
  }

  const show = (line: string): void => append(sections.log, line)

  /** The result of an action of the services, or a plain failure if it broke in the middle. */
  async function attempt(call: () => Promise<StackResult>): Promise<StackResult> {
    try {
      return await call()
    } catch {
      return ACTION_FAILED
    }
  }

  async function start(): Promise<void> {
    if (state.stack?.running && waiting().length === 0) {
      await go("panel")
      return
    }
    state.busy = true
    state.message = ""
    sections.log.textContent = ""
    render()
    const stopListening = api.onLine(show)
    const result = await attempt(() => api.stackStart(waiting().length > 0))
    stopListening()
    state.busy = false
    state.message = result.message
    state.restart = !result.ok && state.restart
    await refresh()
    if (result.ok) {
      await go("panel")
    }
  }

  async function stop(): Promise<void> {
    state.busy = true
    render()
    state.message = (await attempt(() => api.stackStop())).message
    state.busy = false
    await refresh()
  }

  /** A secret store that is locked can take long to answer: nothing else waits for it. */
  async function checkKey(): Promise<void> {
    state.key = null
    renderKey()
    state.key = await api.keyStatus().catch(() => KEY_CHECK_FAILED)
    render()
  }

  async function refresh(): Promise<void> {
    state.docker = null
    render()
    const key = checkKey()
    state.project = await api.projectStatus().catch(() => null)
    state.docker = await api.dockerCheck()
    state.stack = state.docker.ok ? await api.stackStatus() : null
    state.fix = (await api.fixStatus().catch(() => ({ running: null }))).running
    render()
    if (state.initial) {
      state.initial = false
      await go(state.docker.ok && state.stack?.running ? "panel" : "settings")
    }
    await key
  }

  api.onWindowState(({ maximized }) => {
    state.maximized = maximized
    renderControls()
  })
  api.onPanelFailed(() => {
    state.panelFailed = true
    renderView()
  })
  api.onShortcut(() => void go("settings"))

  sendBounds()
  void refresh()
}

if (typeof document !== "undefined" && "healer" in window) {
  main()
}
