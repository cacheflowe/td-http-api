# Changelog

Changes to the TdHttpApi bridge. Add notes under **Unreleased** as you go;
`python scripts/release.py prepare <patch|minor|major|X.Y.Z>` turns that section into the version
heading and uses it for the GitHub release notes.

## Unreleased

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
