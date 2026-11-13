# 0025. Actions from the panel

## Context

The panel of ADR 0020 only reads. Sending what waits in the outbox, or discarding it, needed `healer outbox`. A suggestion from the team that should never come back could not be dropped at all. ADR 0020 left actions for later, because a page that can change things must be protected from requests that other pages open in the same browser send to `localhost`.

## Decision

- **Three actions, on this node's own data only:**
  - **Send now:** what waits in the outbox for this topic, as `healer outbox --send`.
  - **Discard:** one fix waiting.
  - **Forget:** a suggestion from the team, never offered or kept again.

  Applying a fix to the workspace stays in the terminal. The panel's container never sees the workspace, and a flaw in a web page must not become a change to the developer's code.
- **Only the panel's own pages can act.** An action is a `POST` to `/api/actions/<name>`, and the server checks, besides the Host of ADR 0020:
  - its `Origin` is this server (`http://` plus the Host it was asked for), or one given with `--allow-origin`, which only `./panel.sh dev` uses;
  - its body is `application/json`, at most 4 KB, with exactly the fields the action takes, fix ids as 64 hex characters;
  - it carries `X-Healer-Token`, compared in constant time with a token made when the panel starts.

  `GET /api/session` gives that token only to a Host of this computer. The server never sends CORS headers, so a page of another site cannot read the token. It cannot send the custom header or a JSON body either, because a browser first asks the server, which never agrees. Each check alone would stop such a page; together they also stop a plain form post, which can set neither.
- **The token is not a password against this computer.** Any program running here can read `/api/session`, as it can run `healer` itself. It protects against web pages, not against local programs.
- **Forgetting writes no Chroma collection.** Each collection keeps one writer (ADR 0011): the team's is written only by `healer listen`. The panel lists the fix as forgotten in the results file (`forgotten` table, per topic), with SQLite's locking for several processes.
  - `healer run` never offers a forgotten fix.
  - `healer listen` ignores it if it is sent again, and removes it from the collection the next time a fix for that error arrives.
  - The panel and `healer team` leave it out. `healer team --forget FIX` does the same from the terminal, by the start of the fix id that `healer team` now shows.
- **Every action is recorded.** Each change is kept in an `actions` table in the history file, the latest 100, and shown in the recent activity. An action that changes nothing, such as discarding a fix already gone, is told back but not recorded.
- **Each action asks first and says what it did.** The page shows a confirm button, and then the server's answer. Failures are answered, never raised: `Sharing is off`, `That fix is no longer waiting`, or why the broker did not take what waited. An error of the action log answers 500 with its reason.
- **The panel's container writes the data volume.** It needs that for the outbox, the results file and the history file. The code and the sandbox stay read only, and the workspace is still not mounted.

## Consequences

- The outbox and the team's suggestions can be managed from the browser, with the same rules as the commands.
- The panel is no longer read only, so its container gets the data volume with write access. A flaw in it could change the node's data, never the workspace or the code.
- A forgotten fix still takes space in Chroma until a fix for its error arrives; it is never offered meanwhile.
- `./panel.sh dev` passes `--allow-origin http://localhost:3000`, so actions work from the development server too.
- Measured with `./panel.sh demo`, sending exactly what the page sends:
  - laptop-eva's fix was forgotten, and `healer run` then offered only laptop-ana's: 1 attempt.
  - With the broker down, Send now answered that nothing was sent and why. Discard dropped the waiting fix, and a second Discard answered that it was no longer waiting.
  - Each change showed up in the recent activity.
  - Against the node's own panel, a request without Origin, from another site, without the token, or as a form was refused with its reason.
