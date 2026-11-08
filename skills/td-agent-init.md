---
name: td-agent-init
description: Orient an agent at the start of a live TouchDesigner session — load the td-http-api and td-network-craft skills, confirm the HTTP bridge connection and its built-in TD docs, and report the open network. Use as the first message when collaborating on a running TD project.
---

# TD Session Start

We're collaborating on a **live TouchDesigner project** over the td-http-api HTTP bridge. Get oriented with the skills and the bridge's built-in TouchDesigner docs before doing anything else.

## 1. Load the skills (if available)

If your project has agent skills available, read and follow these:

- **`td-http-api`** — comprehensive guide to the HTTP bridge, friction points, and best practices.
- **`td-network-craft`** — idiomatic TD network-building technique and patterns.

**If no skills are available in the active harness**, the skills are available through the HTTP API itself, which is fully self-contained and documented at `http://127.0.0.1:3031/docs` — you can work entirely through that.

## 2. TouchDesigner docs: `/help`

For any TouchDesigner operator or Python-API question, use the bridge's **`/help`** route. It serves
TD's own offline documentation for the exact build that's running. Use it instead of relying on
training data.

```
curl -s "http://127.0.0.1:3031/help?q=feedback%20loop"           # search
curl -s "http://127.0.0.1:3031/help?op=noiseTOP&par=period"       # one parameter
curl -s "http://127.0.0.1:3031/help?class=OP&section=Methods/General"   # a Python class
```

A page read returns its summary and section list; add `section=` (e.g. `Parameters`), `par=` or
`full=true`. The first search on a new TD build starts a ~40 s index build: until then search
returns page-name matches and `index.retryAfter`. Reading pages works immediately.

## 3. Confirm the connection

The td-http-api server is live at `http://127.0.0.1:3031`. Confirm it now:

```
curl -s "http://127.0.0.1:3031/network"
```

It returns JSON describing the network currently open in the TD UI.

**Several TD apps can be running at once.** Each bridge starts at port 3031 and takes the next free
port if that one is busy, up to 3040. If I name an app or give you a port, use it. Otherwise, or if
3031 doesn't answer, scan the range and check which app is which:

```
for p in $(seq 3031 3040); do curl -s -m 1 "http://127.0.0.1:$p/server-info"; echo; done
```

Each live bridge answers with its `label` (the name the developer gave it, also shown in its COMP
viewer), `project` and `port`. Use only the port for the app we're working on, and tell me which one
you picked.

If **no bridge answers at all**, TouchDesigner probably isn't running. Say so, and ask me to open
the project rather than launching it yourself. For a change that only takes effect after a TD
restart, or to test something in a throwaway TD process, see the **`td-process-testing`** skill
(`GET /docs?name=td-process-testing`).

## Two rules that WILL bite you if ignored

- **Use `127.0.0.1`, never `localhost`** — localhost adds ~200ms per call.
- **Every POST/PUT must send a body, even an empty one:** `curl -X POST -d "" "..."`. Without one the server stalls for ~60 seconds.

## What you can do through the API

(Full route reference is in the `td-http-api` skill.)

- **Inspect:** `/network` (`limit`, `fields`, `graph=true`, `overlaps=true`), `/project` (panes, fps), `/bounds`, `/health`, `/logs`
- **See output:**
  - `/snapshot` gives any op's viewer as a PNG. TOPs come back right away; other families return a `jobId`, so fetch `/job?id=…&frame=0`.
  - `/sample` gives pixel stats and a grid as numbers.
  - `/chop` gives channel data; `record=2` records over time.
- **Read/write params:**
  - `/par`: single, or batch `{"set": {path: {par: value}}}`, plus reset/unbind.
  - `/pulse`.
  - `/parsearch` finds pars by name, value or expression glob.
  - `/custompars` adds or edits custom parameters, validated before anything changes.
  - `/opinfo` gives an op type's real parameter names.
- **Read/write DATs:** `/dat` returns JSON. `mode=edit` with `{old, new}` replaces one exact snippet; use it instead of rewriting whole files.
- **Debug:** `/errors` finds node errors (GLSL errors include the compile log); fix, then re-check.
- **Build:**
  - `/build`: ops, params, connections and auto layout in one call, safe to retry. Use it for anything over two nodes.
  - Also `/create`, `/insert` (splice into a wire), `/wire`, `/duplicate`, `/move`, `/delete`.
- **Document:** `/comment`, `/annotate`
- **Collaborate:** `/selected` (read what I clicked in the UI), `/select` (highlight nodes + home my editor to direct my attention)
- **Power tools:** `/run` (arbitrary TD Python, for anything the routes above can't do), `/diff` (structural diff of two `/network` snapshots)

Errors are JSON with a `code` and usually `suggestions` or `validValues`. Use them instead of guessing.
The same routes are also exposed as MCP tools at `http://127.0.0.1:3031/mcp` (`td_par`, `td_build`, …) if your
harness prefers MCP.

Everything **mutates live project state**. Each mutating call is one named undo step ("Agent /par …"), so I can Ctrl+Z it, but file writes and extension state aren't undoable. Confirm destructive steps before running them, and never call `ui.undo.undo()` yourself.

When writing `/run` scripts, save them to a file in /tmp and POST with `curl --data-binary @file` — not shell heredocs, which mangle backslashes.

## First action

Complete the startup checks before waiting for work:

1. Confirm that **`td-http-api`** and **`td-network-craft`** were found and loaded into context as your high-level operation manual.
2. Confirm the built-in docs with one `/help` request (e.g. `/help?op=noiseTOP&par=period`).
3. Confirm the HTTP bridge with the `/network` request.
4. Report a concise readiness status for each check. If any check fails, say exactly which one failed and include the relevant error instead of implying that the session is ready.
5. If the checks pass, tell me which network I currently have open, then wait for what I want to work on.
