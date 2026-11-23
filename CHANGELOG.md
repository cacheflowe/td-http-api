# Changelog

Changes to the TdHttpApi bridge. Add notes under **Unreleased** as you go;
`python scripts/release.py prepare <patch|minor|major|X.Y.Z>` turns that section into the version
heading and uses it for the GitHub release notes.

## Unreleased

- **Lower token use, same capability.**
  - `/network` nodes are compact by default (path, type, position, comment, `pars` of non-default
    values with strings over 120 chars cut, expressions as `{mode, expr}`). `detail=full` gives the
    old entry. An 18-op COMP went from 23 KB to 8 KB. Responses are no longer pretty-printed.
  - The `td-http-api` skill is now a short core (~8 KB: checklist, undo, error contract, and a
    guide to what to read when). The rest moved, unchanged, to `td-http-api-reference`.
  - `/docs?name=<doc>&toc=true` lists a doc's sections with sizes and `&section=<id>` returns one.
    `/init` and the docs preamble no longer send agents to plain `/docs` (every doc, 150 KB+).
  - `/context` index descriptions are shortened (`full=true` for the whole text).
  - `/examples` defaults to 20 rows with text cut to 140 chars (`fulltext=true`, or `/examples.tsv`,
    for the whole text), 25 KB down to 5 KB.
  - `/schema` with no arguments is a compact index (21 KB to 7 KB); `?route=/x` is one route's full
    spec and `?examples=true` is every route's.
  - `/tidy` lists moved ops with positions only when 12 or fewer moved (`nodes=true` for all);
    otherwise it returns counts, overlaps and `nodesOmitted`.
  - The core skill has a token-habits checklist item (filter output, trust write responses, prefer
    numbers over images).
## v0.4.0 (2026-11-21)

- **`/tidy` groups 3D scenes.** A material used by one geometry sits under it (with its shader DATs).
  A Render TOP and the geometry, camera and lights it names are laid out as one scene: geometry on
  the left, then camera and lights, then the Render TOP. `renderRigs` in the response lists them.
- **Agent docs: tidy first, always.** The skills, `/init` and the MCP instructions now say to
  tidy every network you create and to start any layout cleanup with `/tidy`, not `/layout` or
  hand-placed coordinates.

## v0.3.0 (2026-11-19)

- **New: `/tidy` (MCP `td_tidy`), graph-aware layout for existing networks.**
  - Wired chains become left-to-right columns with fewer crossings and straightened wires, separate
    chains stack, and unwired ops go into rows by family. Network boxes are refit.
  - Each op keeps its satellites in rows below it, with room reserved so they don't overlap
    neighbours. Satellites are docked ops (a GLSL TOP's shader/info/compute DATs, a Ramp's keys)
    and unwired DATs tied to exactly one op by an OP parameter or `op()` expression.
  - `paths=` tidies part of a network in place. `dryRun=true` previews. One undo step.
  - Agent docs, `/init` and the MCP instructions now say to tidy after editing a network unless the
    developer wants their layout kept. Inspired by the Network Tidy tox.
  - Columns are compacted so sources and dead ends sit next to what they connect to. Crossings
    are reduced with barycenter passes and sifting, and each column's heights are fit exactly to
    its wires, so chains come out straight. Long wires keep to their lane, and wiring loops are
    handled.
  - A network box keeps its ops together and is refit around them; a text note stays beside the
    op nearest to it. Unwired ops go in rows by family. Tidying a tidy network changes nothing.
- **New: Layout Mode par** (Agent page): Agent Tidies (default), Auto Tidy After Edits (the bridge
  tidies after `/create`, `/build`, `/insert`, `/duplicate`, `/wire`, in the same undo step), or
  Keep My Layout (`/tidy` and `/layout` refuse without `force=true`). Shown in `/server-info`,
  `/init` and the MCP instructions.
- **New: new ops never land on top of existing ones.** After `/create`, `/build`, `/insert`,
  `/duplicate` and `/run`, a new op that overlaps a sibling is moved down to free space (listed in
  `unstacked`). `/move` flags `overlapsWith` instead of correcting an explicit position.
- **New: `/context` (MCP `td_context`), the project's own agent docs.** Indexes instruction files,
  skills, rules and prompts in every common harness layout (Claude, Codex/AGENTS.md, Copilot,
  Cursor, Windsurf, Cline, Roo, Kiro, Gemini, Continue, Junie, `.ai/`), plus a fuzzy pass over
  folders named skills/rules/prompts/agents, in the project folder up to its repo root, and
  README/AGENTS DATs inside the `.toe`. `/init` and the MCP instructions list what's there.
- `/build`'s auto layout now uses the `/tidy` algorithm, so GLSL DATs and callbacks no longer
  overlap the next column. `/create`, `/duplicate` and `/insert` now flash the ops they make.

## v0.2.2 (2026-11-09)

- The COMP viewer is now a rebrandable 16:13 SVG card with dynamic app and port rows, and
  `/docs?name=td-app-icon` gives agents a reusable guide for creating the same layout in other
  TouchDesigner projects.

## v0.2.1 (2026-11-08)

- **Fix: exports and restarts no longer rearrange the nodes inside the bridge COMP.**
  `setup_component()`, which `/export-release` runs on every release, moved `execute_start`,
  `parexec_server` and `dashboard_html` back to hard-coded spots each time. Nodes are now placed
  only when they're created.
- **Fix: a downloaded tox could fail to start properly.** The bridge's "restart pending" flag lives
  in COMP storage, which is saved with the tox. If it shipped set, every copy skipped its startup:
  the server still listened on the port saved in the tox, but never picked a free one, set Active
  Port, or redrew its viewer. The flag is now a frame stamp that goes stale after 30 frames, and
  `/export-release` leaves it out of the release.
- **Fix: the COMP viewer has a black background and fills the frame in any project.** The palette
  SVG component applies its background color and fit-to-size with JavaScript after load, which
  didn't reliably run in a freshly loaded tox (transparent, or a small image in the corner). The
  viewer SVG now paints its own background and scales to the Web Render TOP's resolution.
- The release tox no longer ships the dev project's port and label in its viewer; it shows
  "stopped" until the bridge starts.

## v0.2.0 (2026-11-08)

- **The COMP viewer shows the bridge version** (e.g. `v0.2.0`, small in the top-right corner), so you
  can tell which release a project has without opening the parameters.

## v0.1.1 (2026-11-08)

- **New agent doc, `td-process-testing`**, embedded and served at `/docs`. It covers when a TD
  restart is really needed and how to hand it to the user safely, testing a `.tox` in a throwaway
  TD process that reports JSON and quits, and stopping only your own TD processes. A separate
  section covers Custom OP (C++ plugin) development: temporary plugin installs, the
  `Plugins.json` allow-list and locked DLLs. It covers Windows and macOS. There's no bundled launcher
  script: the skill says what a launcher must do and gives PowerShell and bash sketches, so the
  agent writes one for the platform it's on.
- **Release toxes drop the development-only parts.** `/export-release` now removes the Save Tox
  With Project toggle and its post-save hook from the release tox, instead of just turning the
  toggle off, and also clears the leftover expression on the external tox path. A released bridge
  has no way to re-save itself over the downloaded file.
- **Fix:** `GET /docs?name=td-http-api` (a skill's plain name) now finds its doc. The embedded DATs
  are named `docs_skill_*`, so only the full DAT name used to match.

## v0.1.0 (2026-11-06)

First standalone release, after the bridge moved out of haxlib into its own repo.

- **Drop-in tox**: everything the bridge needs (route code, dashboard, agent docs, init prompt) is
  embedded. The release tox has no file syncs or external paths, so it works in any project.
- **Bridge**: read and write the network, parameters and DATs; batch `/build` with auto layout;
  `/snapshot`, `/sample`, `/chop`; `/diff`, `/errors`, `/run`, `/reload`.
- **Agents**: an MCP endpoint (`POST /mcp`, every route as a `td_*` tool), `/init` and `/docs`,
  JSON errors with suggestions, and every change as one named undo step.
- **Several apps at once**: each bridge takes the first free port from 3031; `/server-info` says
  which app is which.
- **Dashboard** at `/ui`: live agent activity, how the bridge works, and a route reference with
  Try panels.
- **OP Snippets catalog** (`/examples`) built from the local TD install on first use and cached
  per TD build, instead of a committed snapshot.
- **TouchDesigner docs built in** (`/help`, MCP `td_help`): search and read TD's own offline docs
  for the running build, down to a single parameter. Replaces the separate td-docs-mcp server.
- `/cache` lists the per-machine cache (snippets catalog, docs index) with what each file is, and clears it.
- `/server-info` reports the version, the TD install folders the bridge relies on, and warnings.
- **Save Tox With Project** toggle: a project save also saves the COMP's external tox (on in the
  dev project, off in release toxes).
- Every COMP parameter has a tooltip explaining what it does (hover its name). The help text lives in
  one `PAR_HELP` table in the route code.
- The route-code DAT is now `td_http_api` (was `callbacks`); the startup DATs follow whatever it's named.
- The COMP viewer is an SVG: a logo with the port and app label under it (`text_logo_svg` + the `SVG` COMP).
  The default logo is a `<••>` bracket face (code + agent). A thin rule separates it from a larger,
  more legible port and label.
- `/snapshot?format=png` works without the leading dot.
- `/help?class=OP` (and `COMP`) reads the full `OP_Class` page; it used to land on a near-empty stub.
  `section=Methods` now includes its subsections, `section=Methods/General` picks one nested heading,
  and search results give that path.
- `scripts/release.py prepare` refuses to run with uncommitted changes, and the release commit
  contains only the files it changes.
- Removed network templates (`/create-from-template`): `/build` covers it.
