# td-http-api: agent bridge for TouchDesigner

This repo is where the **TdHttpApi** bridge is developed: an HTTP server inside a TouchDesigner
COMP that gives AI agents read/write access to a live TD project. It offers REST routes, an MCP
endpoint (`/mcp`), and a dashboard (`/ui`). It moved here from haxlib in November 2026. haxlib (and
any other project) consumes the packaged `.tox`.

Read **[skills/td-http-api.md](skills/td-http-api.md)** before changing anything. It's the full route
reference and the record of what was learned building it. **[TODO_http_bridge.md](TODO_http_bridge.md)**
is the roadmap.

## Layout

| Path | What |
|---|---|
| `src/td_http_api.py` | **All route code**, one self-contained module (no imports from other project files) |
| `src/TdHttpApi.tox` | The dev tox (file paths still set). Re-saved on every project save (Save Tox With Project), or with `/save-external-tox?path=/project1/TdHttpApi` |
| `src/TdHttpApi_dashboard.html` | The `/ui` dashboard (vanilla JS, no external resources) |
| `src/TdHttpApi_README.md` | The user-facing README embedded in the tox |
| `skills/` | Agent docs embedded in the tox and served at `/docs`: `td-http-api.md`, `td-network-craft.md`, `td-common-mistakes.md`; `td-agent-init.md` is the prompt served at `/init` |
| `scripts/release.py` | Release: bump `VERSION`, export `build/TdHttpApi-vX.Y.Z.tox`, roll the changelog, tag + `gh release` |
| `CHANGELOG.md` | Add notes under **Unreleased** as you change things |
| `docs/RELEASING.md` | The release process, local and GitHub |
| `build/` | Release output (gitignored; attached to GitHub releases) |
| `td-http-api.toe` | Dev project: `/project1/TdHttpApi` with every DAT file-synced to the files above |

## Working on the live bridge

- **Find the right port first.** Several TD apps can run a bridge at once (3031–3040). This dev project's
  bridge reports `"label": "td-http-api"` from `GET /server-info`. While haxlib is also open, haxlib usually
  has 3031 and this one 3032. Never test against another app's bridge.
- **Edits to `src/td_http_api.py` go live immediately.** The callbacks DAT is file-synced, so the module
  reloads on save, including the server you're talking through.
  - Keep each edit syntactically valid.
  - Run `python -m py_compile src/td_http_api.py`.
  - Define functions before anything at module level uses them. `_ROUTES` is built mid-file; later
    routes register with `_ROUTES[...] = ...`.
  - A reload resets in-memory state: the activity log and background jobs.
- **Never push route code through `/dat`** on the synced callbacks DAT. Edit the file.
- **`/run` scripts**: write them to `tmp/` with the Write tool (shell heredocs mangle backslashes), POST with
  `curl --data-binary @tmp/x.py`, and delete them afterwards.
- **Test in a sandbox COMP** (e.g. `/project1/_agent_sandbox`, the default Sandbox Path) and delete it when done.
- **Undo rules** (learned the hard way, details in the skill):
  - Never call `ui.undo.undo()`.
  - Never open `ui.undo.startBlock(..., enable=False)` from a deferred `run()`.
  - Don't toggle `ui.undo.globalState`.
- **After changing behaviour**, update `skills/td-http-api.md` (route tables and prose), the README, the
  TODO and `CHANGELOG.md` (Unreleased). Then re-save the dev tox so its embedded copies match.
- **Nothing machine-specific gets committed or embedded.** Anything read from the local TD install
  (the OP Snippets catalog, later the `/help` docs index) is built on first use and cached per TD
  build under `app.preferencesFolder/TdHttpApi/cache/<build>/` (`_cache_dir()`; `GET /cache`).
- **No Python threads for heavy work inside TD.** A background indexing thread deadlocked TD on
  Nov 6, 2026. Slice the work across frames with `run(..., delayFrames=1)` (see `_help_step`). If the tox gains a new
  way of writing files, add it to "What the tox writes to disk" in the skill.
- **Drop-in check**: `POST /export-release`, then `loadTox` the result into `/project1/_agent_sandbox`
  (it starts on the next free port), check `/smoketest` and `/examples` there, and destroy it.

## Checks

- `GET /smoketest` must pass.
- Dashboard JS: extract the `<script>` and run `node --check`. Screenshot with headless Chrome:
  `chrome --headless=new --window-size=1280,800 --virtual-time-budget=3000 --screenshot=out.png "http://127.0.0.1:<port>/ui#routes"`.
- MCP: `curl -X POST -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' http://127.0.0.1:<port>/mcp`.
- In Git Bash, `curl -o /dev/null -w "%{size_download}"` reports 0 bytes for fine responses. Save binary
  responses to a real file to check them.

## TouchDesigner docs

Ask the running bridge: `GET /help?q=...`, `/help?op=noiseTOP&par=period`, `/help?class=OP`. It serves
TD's own offline docs for the running build (see "TouchDesigner docs: `/help`" in the skill). Query it
rather than relying on memory.

## Style

Tabs, PascalCase classes / snake_case functions, f-strings, `[td_http_api]` log prefix, comments only for a
non-obvious *why*. Errors raised to agents use `BridgeError(message, code=..., **details)`, never bare text.
