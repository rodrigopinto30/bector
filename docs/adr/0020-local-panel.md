# 0020. A local panel to see the whole node

## Context

Everything the system knows was spread over several commands: `doctor`, `savings`, `team`, `outbox` and the logs of the listener. Seeing the health of a node, what it saved, its incidents and the fixes it keeps meant running each one. A graphical view was asked for, with Next.js, TypeScript, Tailwind and shadcn, in a soft pastel design with a dark mode. Section 7.5 of the plan asks for no unneeded ports, every input from the network treated as untrusted, and every dependency pinned and audited.

## Decision

- **A web panel, not an editor extension.** A page in the browser works with any editor. A VS Code extension can come later, on top of the same data.
- **Next.js built as static files, served by Python.** `panel/` is a Next.js application with `output: "export"`. The Dockerfile builds it in a Node stage, and only the files of `out/` reach the image, at `/opt/healer/panel`. `healer panel` serves them with uvicorn, which was already locked through chromadb, together with the data at `/api/snapshot`. Node never runs in the containers, there is one server and one origin, and the data is read by the same Python code that writes it. The page asks for the data every 5 seconds, and again when the tab comes back into view.
- **The data is one read-only snapshot,** `PanelSnapshot` in `domain/panel.py`, built by `services/panel.PanelReader`:
  - health: the checks of `doctor`, plus the node name, the listener and the outbox;
  - savings: totals, 30 days split by level, and incidents per level;
  - the last 50 incidents;
  - the projects, with the size of their index and their fixes;
  - the team's suggestions and the outbox.

  The changes are sent as removed, added and context lines, computed in Python with `difflib`. Each section is read on its own: one that fails is left empty and its error is shown, so a corrupt file never hides the rest. An unexpected error, such as one from Chroma, is also logged with its traceback.
- **Recent activity, newest first,** so a change is seen where it happens. It mixes:
  - the incidents;
  - the suggestions received;
  - the fixes left waiting;
  - the last 20 events of the listener, with their time: connection changes, and messages ignored or rejected.

  Each event comes with its time. The page lights up for one refresh the events that were not there before. A first demo with counts only, and a refresh every 10 seconds, left the changes too easy to miss.
- **Reading never creates anything.** `ChromaPanelStore` opens only the collections that exist. The ledger and the outbox are not created when missing. The broker is checked with a timeout of 3 seconds at most, so a broker that is down does not make the panel wait.
- **The listener reports itself.** `healer listen` keeps a small JSON file, `LISTENER_STATUS_PATH`, replaced whole on every change:
  - its connection;
  - what it received, ignored and rejected, and its last 20 events, each text cut to 300 characters;
  - a sign of life every 30 seconds.

  The panel shows a listener silent for 90 seconds as not answering. A file that cannot be written warns once and never stops the listener.
- **Only this computer, and only reading:**
  - The port is published on `127.0.0.1` only.
  - The server answers only GET and HEAD, and only to the names `localhost`, `127.0.0.1` and `[::1]`, so a page of another site that resolves its own name to `127.0.0.1` (DNS rebinding) cannot read the data.
  - Every page carries a Content-Security-Policy:
    - scripts only from the server, plus its own inline scripts, each allowed by its SHA-256, computed when the page is served;
    - requests only to the server;
    - no frames, forms or base URL.

    Inline styles are allowed, because the chart library sets them; data shown on the page is always text, never markup.
  - Files are served only from the built pages: no `..`, no hidden files, no symbolic link that leads outside.
  - The panel container gets the code, the data volume and the sandbox volume read only, never the workspace, and runs with a read-only filesystem, no capabilities and `no-new-privileges`.
- **The Node dependencies follow the rules of ADR 0017.**
  - `panel/package-lock.json` pins each package with its hash. `npm ci --ignore-scripts` installs exactly that, in the image and in `./panel.sh`.
  - `./panel.sh audit` fails on any known vulnerability.
  - The Node image is pinned, to `node:26.13.0-trixie-slim`.
  - The command line of shadcn brought two vulnerable packages, so the CSS it provides is copied into `panel/src/styles/shadcn.css` and the command line is not a dependency.
  - The linter brought a vulnerable `minimatch` 10; an `overrides` entry moves only that use to 11.0.5, which is fixed.
- **The Node tools run in a container,** through `./panel.sh`, so the host does not need Node: `check` (Prettier, ESLint, TypeScript, Vitest and the build), `quick` without the build, `format`, `audit`, `dev` and `npm`. CI runs `check` and `audit`. The pre-commit hook runs `quick` when the commit has files of `panel/`.
- **`./panel.sh dev` keeps the panel strict.** The Next.js dev server sends the container's name as the host. So `dev` starts a second panel server, reachable only inside Docker's network, that also answers to its own container name through `healer panel --allow-host`. The panel at port 8765 never answers to that name.
- **One contract, checked from both sides.** `panel/src/lib/types.ts` mirrors `PanelSnapshot` by hand. The sample `panel/src/lib/fixtures/snapshot.json` must equal what the model produces from it, field by field: a Python test checks it, so a field added on one side only makes a test fail.
- **The colors of the charts were checked with a color-vision validator.** The four levels, local cache, team, Claude and unresolved, keep one color everywhere: sky, peach, mint and butter. Neighbors stay apart for color-blind readers, in both modes. These pastel tones have low contrast on the light background, so the bars carry visible percentages and every chart can be shown as a table. A state is never shown by color alone: it carries an icon and a word.

## Consequences

- One address shows the whole node. Measured with the data of this machine, and with read-only mounts:
  - the panel read the ledger, the fixes and the sandbox;
  - it reported no problems;
  - the browser ran the pages with the policy on and blocked nothing.
- The image build has a Node stage. On a clean build it took about 30 seconds: 23 to install the packages and 9 to build the pages. It is cached until `panel/` changes.
- A second ecosystem of dependencies, npm, must be kept locked and audited. Its audit has no exception list. A vulnerability without a fix must be handled with an `overrides` entry, or by removing the package.
- The page refreshes by asking again every 5 seconds. Each refresh asks the broker and the sandbox once.
- The days of the chart are UTC days, like `healer savings`, while the times are shown in the reader's time zone.
- `./panel.sh demo` (`scripts/panel-demo.sh`) makes the panel change with real events: a suggestion from a teammate, a forged message, a run fixed by the team, the broker going down, a fix waiting in the outbox and sent when the broker returns, and a run fixed by the local cache. It uses a volume and a topic of its own and a panel at port 8766, so the node's data and its team are never touched. Claude is never asked, so it costs no tokens: the only fix this node "found" is written by the demo itself. Measured: each of the eight events changed the panel as described, and `./panel.sh demo clean` left no container or volume behind.
- The panel does not change anything. Accepting, sending or discarding fixes from it would need protection against requests forged by other pages, and is left for later.
