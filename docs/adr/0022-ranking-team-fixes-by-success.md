# 0022. Ranking the team's fixes by success

## Context

ADR 0021 let a node keep several fixes per error, tried from the most recently found to the oldest. The time a fix was found says nothing about whether it works. Section 6.4 of the plan proposes that nodes report whether reusing a solution worked, and that the knowledge base be ordered by observed success rate in addition to similarity. ADR 0021 also left the outbox with one fix per error, which could lose a fix while the broker was down.

## Decision

- **A node tells the team what each fix of the team did there.** When `healer run` tests a suggestion and its tests run, the node records a `FixResult`: its node name, the time, the error signature, the fix id and whether the tests passed. A fix that did not apply to this code, or that was declined, tells nothing. The node's own fixes are never reported, so a node cannot vouch for itself.
  - With sharing on, the result is also sent, signed with the team key. With sharing off, it only counts on this node.
  - A result never carries code, a project, a path or an error message: the fix id and the signature are hashes.
- **Results travel on their own topic, `MQTT_TOPIC` plus `/results`.** Nodes from before this decision subscribe only to the team topic, so they never receive a result they could not read. The broker's rules already allow every subtopic of the team topic. `healer listen` subscribes to both.
- **What is signed includes the kind.** A result is sealed as `{"mac", "result"}`, with the HMAC over `{"result": ...}`. A signed result therefore cannot be presented as a fix, and the schema of fixes did not change.
- **One vote per node and fix, its latest.** Results are kept in a SQLite file, `RESULTS_PATH`, with one row per topic, fix and node. A newer result from the same node replaces its earlier one, and an older one is ignored. SQLite was chosen over Chroma because `healer run` and `healer listen` both write results, and Chroma's collections have one writer each.
- **A result counts only for a fix this node keeps.** One about an unknown fix is ignored. So the results stored are bounded by the fixes stored, whatever arrives. Results of a fix that was dropped stay, so the fix keeps its record if it is received again.
- **The order: the share of nodes where a fix worked, then how many, then the newest.** The share counts one vote for and one against from the start, `(worked + 1) / (worked + failed + 2)`, so one result never decides alone. A fix nobody tried scores 0.5: above one that failed once, below one that worked once. With no results at all, the order is the one of ADR 0021.
  - `healer run` offers the fixes for the same error in this order, each with its record: `results    worked on 2 nodes, failed on 1 node`.
  - When an error is full, the fix ranked last makes room, instead of the oldest. A new fix that ranks below all of them is ignored (`OUTRANKED`). On a tie the fix that just arrived loses, so replaying old messages still cannot push out newer fixes.
- **The outbox keeps one entry per fix, and results too.** Its fixes are now keyed by topic and fix id, so different fixes for one error wait side by side. Results the broker did not take wait in a table of their own, one per fix, and are sent after the fixes. An outbox from before is read as it is. The first change moves its entries into the new table, so a read-only reader such as the panel never changes the file.
- **What people see:**
  - `healer listen` prints `result     laptop-sol: the fix from laptop-ana for KeyError: 'db' worked there`.
  - `healer team` adds a `results` line.
  - `healer outbox` and `healer doctor` count the results waiting.
  - In the panel, each Team card shows its record and its place in the order. Recent activity shows `Result received`, and the Outbox card counts results.
  - `./panel.sh demo` has a new event: a third teammate tells that a fix worked there.

## Consequences

- The more nodes use a fix, the better its place reflects how often it works, and a fix that fails often sinks below untried ones.
- The ranking is only an order. Every fix is still tested here, in the sandbox, before it is offered, and applying it still needs a yes.
- Anyone with the team key can send results under any node name, so a leaked key could move a bad fix up. The worst outcome is a failed attempt that is then skipped. This is listed as a known limitation.
- A node's own fixes have no results on that node: it ignores results about fixes it does not keep, and it does not keep its own.
- Measured with `./panel.sh demo`. `healer run` tried laptop-eva's fix first, the newest, which failed, then applied laptop-ana's, and told the team both results. From then on laptop-ana's fix went first. A third node reported it as working too, and the panel showed it as worked on 2 nodes. All 10 events changed the panel as described.
