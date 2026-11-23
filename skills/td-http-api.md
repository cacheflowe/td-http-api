---
name: td-http-api
description: HTTP bridge into a running TouchDesigner project via a Web Server DAT — read/write network structure, params, and DAT scripts, hot-reload Python modules, and create/wire nodes, all from outside TD. Use when an AI agent or external tool needs live access to TD project state.
---

# td_http_api

A local HTTP bridge into a running TouchDesigner project: an AI agent (or any tool) can inspect and modify a live `.toe` without the Textport or the UI. Routes, an MCP endpoint (`/mcp`) and a dashboard (`/ui`) all come from one Web Server DAT callback module.

This is the short core: the rules that keep the project safe and the habits that save time. The full reference is **`td-http-api-reference`**. Fetch only the section you need: `GET /docs?name=td-http-api-reference&toc=true` lists the sections with sizes, and `GET /docs?name=td-http-api-reference&section=<id>` returns one. Don't load the whole reference. `GET /routes` is the live list of routes; `GET /schema` has parameters and examples.

## Mandatory Agent Checklist

- **Prioritize dedicated routes over monolithic scripts.** Prefer `/build`, `/par` (batch), `/pulse`, `/custompars`, `/dat mode=edit`, `/create`, `/wire`, `/move`, `/comment`, `/flag` and `/annotate` over `/run`. Use `/run` only when the operation can't be expressed through those.
- **Build multi-node networks with `/build`.** One call creates, parameterizes, wires and lays out the nodes (longest-path columns, so no stacking), and it's safe to retry. With single `/create` calls, `_auto_place` puts each node somewhere reasonable, but run `/tidy` when you're done; don't compute coordinates by hand.
- **Tidy first, always: `POST /tidy`.** Any network you create gets tidied when you finish building it, and any layout cleanup starts with `/tidy`. Only then fix what it can't infer (a manual `/move`, a `/annotate` box). Don't hand-place nodes or reach for `/layout` (Shift+L) first. Agent-placed networks are spatially poor. After creating, wiring or moving ops, tidy the network you changed, unless the developer has asked to keep their layout. In a network the developer arranged by hand, scope it with `paths=` to the ops you touched, or ask once before tidying the whole thing. `/tidy` already groups a 3D scene: each material under its geometry, and the rig (camera, lights, Render TOP) to the right of the geometry. See section `tidy-graph-aware-layout`. The bridge COMP's **Layout Mode** par decides this per project (`/server-info` → `layoutMode`, also stated in `/init`):
  - `agent` (default): you tidy.
  - `auto`: the bridge tidies after `/create`, `/build`, `/insert`, `/duplicate` and `/wire`, and reports `autoTidy`.
  - `keep`: hands off. `/tidy` and `/layout` refuse with `layout_kept` unless the developer asks and you pass `force=true`.
- **Never place a node on top of another.** The bridge enforces this for new ops: after `/create`, `/build`, `/insert`, `/duplicate` and `/run`, any new op whose tile (with its docked ops) overlaps a sibling is moved straight down to free space and listed in `unstacked`. A bare `COMP.create()` in a `/run` script lands at 0,0, often on the bridge COMP itself. Still give positions in free space. `/move` doesn't correct an explicit position, but it flags `overlapsWith`: fix those.
- **Read the project's own docs: `GET /context`.** It indexes instruction files, skills, rules and prompts in every common harness layout in the TD project's folder (up to its repo root), plus README-style DATs inside the `.toe`. `/init` lists them. Read the ones that matter for the task. They override generic guidance here.
- **Read error `code`s and use their `suggestions` / `validValues`** (see Error contract) instead of guessing again.
- **Viewer hygiene.** Ensure user-visible terminal/output nodes are viewable immediately (`viewer=true` at create time, or set viewer/display/render state explicitly via deterministic routes).
- **No shell heredocs for script payloads.** When `/run` is required, write a real file and send with `--data-binary @file` to avoid escaping/backslash corruption.
- **Spend tokens like they cost something.** The whole conversation is re-read every turn.
  - Filter before it reaches you: pipe curl through `python -c` or `jq` and print only the fields you need.
  - Orient with `/network.mmd`, `/network?fields=...` or the compact `/network` (`detail=full` only when you need evaluated values or `customPars`). `/pars?path=` and `/opinfo` are for one op's parameters.
  - Trust what a write returns (`moved`, `overlaps`, `unstacked`, `created`, `failed`). Don't re-read the whole network after each edit; check once at the end.
  - Prefer `/sample` numbers and `/errors` over `/snapshot` images, and pass `size=` when you need a picture.
  - Fetch one doc section, one `/schema?route=`, one `/help?op=`, not the whole index.
- **Resolve first, mutate second.** Confirm the active network root via `/network` (and `/selected` only as supporting signal) before any write call.

## Essentials

### Undo

Mutating routes run inside a named `ui.undo` block (`Agent /par /project1/foo`, and so on), so
**Ctrl+Z in TD reverts an agent's change**, one route call per step.
- The wrapped routes are listed in `_UNDO_ROUTES`: `/par`, `/dat`, `/create`,
  `/wire`, `/insert`, `/duplicate`, `/comment`, `/bypass`, `/flag`,
  `/annotate`, `/move`, `/layout`, `/tidy`, `/delete`, `/run`, `/pulse`, `/custompars`, and `/build`.
- TD drops blocks that captured nothing, so read-only `/run` probes don't clutter the stack.
- Only changes the editor itself can undo are captured. Extension state, `store()` and file writes
  aren't.
- `ui.undo.undoStack` is **newest first**: `undoStack[0]` is the last action.
- **Never call `ui.undo.undo()` from an agent** unless you've just confirmed that `undoStack[0]`
  is your own entry. Otherwise you revert the user's work.

### Error contract (all routes)

Every error response is JSON: `{"error": "<message>", "code": "<stable_slug>", ...details}`.
Branch on `code`, and read the details before retrying:

| code | When | Details to use |
|---|---|---|
| `op_not_found` | bad path | `suggestions` (similar sibling paths) |
| `par_not_found` | bad par name | `suggestions` (closest names, labels matched too: `Harmonics` → `harmon`) |
| `invalid_menu_value` | strict menu got an unknown value | `validValues`, `validLabels`, `suggestions` |
| `invalid_op_type` | `/build`/`/opinfo` type typo | `suggestions` (`noizeTOP` → `noiseTOP`) |
| `input_out_of_range`, `type_mismatch`, `not_a_pulse`, `wrong_family`, `not_editable` | structural | `numInputs`, `existingType`, `style`, `family` |
| `no_match`, `ambiguous_match` | `/dat mode=edit` | `count`, `hint` |
| `invalid_actions` | `/custompars` batch | `errors: [{at, code, error, validValues?}]` (nothing was changed) |
| `unknown_route` | typo'd route | `suggestions`, `routes` |
| `cooldown` (429) | after a call slower than **Slow Call Threshold** | `retryAfter` — retry the exact call |
| `capture_busy` (429) | 4 background jobs running | `retryAfter` |
| `forbidden_origin` (403) | browser request | — |
| `bad_request` | anything else invalid | message |
| `internal_error` (500) | bug or TD exception | last lines of `traceback` |


## Where to read more

Section ids are in `td-http-api-reference` (`/docs?name=td-http-api-reference&section=<id>`). Read the one that matches what you're about to do:

| If you're about to... | Read section |
|---|---|
| Find a route, its parameters or an example call | `read-inspect`, `write-mutate`, `execute-utility`, `example-calls` (or `GET /routes`, `GET /schema`) |
| Lay out or tidy a network (`/tidy`, `/layout`) | `tidy-graph-aware-layout`, `layout-shift-l-and-its-pitfalls`, `layout-branches-that-rejoin` |
| Clean up a scattered network | `organizing-scattered-experiment-networks`, `deterministic-rules-for-smaller-models` |
| Resolve which network the user means | `resolving-the-user-s-current-network` |
| Add notes or boxes (`/annotate`, `/comment`) | `annotate-and-annotation-boxes`, `what-the-user-means-by-annotation-box`, `standardizing-comments-annotation-scales` |
| Create nodes or rely on auto-placement | `create-and-auto-placement` |
| Check errors, flags, a blank Render TOP | `errors-flags-and-render-checks`, `errors-freshness-and-diff-noise` |
| See pixels (`/snapshot`, `/sample`) | `snapshot-seeing-pixels` |
| Set transform/position parameters | `how-it-works-modules-live-state-references-units` (units) |
| Duplicate ops | `duplicate-and-outside-references` |
| Save the project or the tox | `saving-save-vs-save-external-tox` |
| Look up a TD operator or Python class | `touchdesigner-docs-help` (use `GET /help`) |
| Edit this bridge's own code or `/run` scripts | `editing-this-bridge-s-own-code-dat-run`, `picking-up-code-changes` |
| Run several apps, change ports, security, follow mode | `ports-labels-and-several-running-apps`, `security-browser-requests-are-refused`, `follow-mode-agent-page` |
| Use MCP instead of HTTP | `http-or-mcp` |
| Hit a surprise | `friction-points-mitigations-lessons-from-the-field`, `known-gaps` |
| Release or re-export the tox | `releasing-and-re-exporting-the-tox`, `what-the-tox-writes-to-disk` |

Fetch `###` ids when you can: a `##` id (for example `concepts`, 29 KB) returns all its subsections.
