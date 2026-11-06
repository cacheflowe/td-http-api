# Releasing TdHttpApi

A release is a version tag in git plus a GitHub Release
([cacheflowe/td-http-api/releases](https://github.com/cacheflowe/td-http-api/releases)) with the
drop-in tox attached and the changelog section as its notes. Users download
`TdHttpApi-vX.Y.Z.tox` from there; the repo itself doesn't contain release toxes.

## What gets released

| File | What it is |
|---|---|
| `build/TdHttpApi-vX.Y.Z.tox` | **The release.** A clean copy of the bridge COMP: every file sync, the external tox path, Save Tox With Project, Active Port and Agent Prompt cleared, so it refers to none of this repo's files. Attached to the GitHub Release; `build/` is gitignored. |
| `build/TdHttpApi.tox` | The same file without the version in its name, for local testing. |
| `build/release-notes-vX.Y.Z.md` | The changelog section, used as the release notes. |
| `src/TdHttpApi.tox` | **Not a release.** The dev tox, committed, with this repo's file syncs still set. Re-saved on every project save. Don't hand it to users. |

## Version numbers

`VERSION` at the top of `src/td_http_api.py` is the only place the version lives. It shows in
`/server-info`, the MCP `serverInfo`, the COMP's read-only **Version** parameter, the release file
name. `scripts/release.py` bumps it; don't edit it by hand.

Semantic versioning, read for a tox:
- **major**: something a user's agent prompts, scripts or MCP registrations depend on changes
  incompatibly (a route or parameter renamed or removed, a response reshaped).
- **minor**: new routes, parameters or features.
- **patch**: fixes and doc changes.

## One-time setup

- `td-http-api.toe` opens in TouchDesigner and its bridge is running. `release.py` finds it by
  scanning ports 3031–3040 for the bridge whose `projectFolder` is this repo, so other TD apps can be
  open too.
- Python 3 to run `scripts/release.py` (standard library only; TD's own `bin/python.exe` works).
- `git`, with `origin` pointing at `https://github.com/cacheflowe/td-http-api.git`.
- The GitHub CLI, logged in: `gh auth status`.

## As you work

Add a line under **Unreleased** in `CHANGELOG.md` for anything a user or agent would notice. Ctrl+S
in TD keeps `src/TdHttpApi.tox` current.

## Making a release

### 1. Prepare (local)

```
python scripts/release.py prepare patch      # or minor, major, or an exact X.Y.Z
```

Everything you want in the release must already be committed: `prepare` refuses to run while any
tracked file has uncommitted changes (untracked files are ignored). That keeps the release commit
down to the files `prepare` itself changes.

It:
1. Checks that `CHANGELOG.md` has notes under **Unreleased** (and stops if not).
2. Writes the new `VERSION` into `src/td_http_api.py` and waits until the running bridge reports it
   (TD reloads the file-synced module within a fraction of a second).
3. Calls `POST /export-release`, which writes `build/TdHttpApi-vX.Y.Z.tox`, and copies it to
   `build/TdHttpApi.tox`.
4. Re-saves the dev tox, `src/TdHttpApi.tox`.
5. Renames **Unreleased** to `## vX.Y.Z (date)`, adds a fresh empty **Unreleased** above it, and
   writes that section to `build/release-notes-vX.Y.Z.md`.

For the first release, pass the current version: `python scripts/release.py prepare 0.1.0`.

### 2. Check the release tox

In a new, empty TouchDesigner project, drag in `build/TdHttpApi.tox`, then:

- The COMP viewer shows a port and the app label.
- `curl http://127.0.0.1:<port>/smoketest` returns `"passed": true`.
- `/help?op=noiseTOP&par=period` and `/examples?q=feedback` return results.
- `/ui` opens the dashboard; **Copy Prompt** gives a prompt an agent can start from.
- `/server-info` shows the new `version` and no unexpected `warnings`.

Quicker, inside the dev project: `loadTox` it into `/project1/_agent_sandbox` (it starts on the next
free port), run the same checks there, then delete it.

### 3. Commit and push

Commit only the files `prepare` changed (it warns if anything else changed while it ran):

```
git commit -m "Release vX.Y.Z" -- CHANGELOG.md src/td_http_api.py src/TdHttpApi.tox
git push origin main
```

### 4. Publish to GitHub

```
python scripts/release.py publish
```

It refuses to run with uncommitted changes or a missing `build/` file, prints the commands, asks
for confirmation (`--yes` skips the question), then runs:

```
git tag -a vX.Y.Z -m vX.Y.Z
git push origin vX.Y.Z
gh release create vX.Y.Z build/TdHttpApi-vX.Y.Z.tox --title vX.Y.Z --notes-file build/release-notes-vX.Y.Z.md
```

Check the release page afterwards: the tox should be attached and the notes should match the
changelog.

## Fixing a release

Prefer releasing a new patch version. If a release has to be withdrawn:

```
gh release delete vX.Y.Z --yes
git push --delete origin vX.Y.Z
git tag -d vX.Y.Z
```

Then fix, and release under a new version number rather than reusing the old one: anyone who
downloaded it has that version in their project.
