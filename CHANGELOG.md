# Changelog

Changes to the TdHttpApi bridge. Add notes under **Unreleased** as you go;
`python scripts/release.py prepare <patch|minor|major|X.Y.Z>` turns that section into the version
heading and uses it for the GitHub release notes.

## Unreleased

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
