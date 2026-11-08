---
name: td-process-testing
description: Restarting TouchDesigner and testing in a fresh TD process, on Windows or macOS — when a restart is really needed and how to avoid one, handing a restart of the user's project to the user safely, writing a launcher that starts a throwaway TD on a test .tox which reports a JSON result and quits, and identifying and stopping only your own TD processes. Also covers Custom OP (C++ plugin) development: temporary plugin installs, the Plugins.json allow-list, and locked DLLs. Use when a change only takes effect after a TD restart, or to test a .tox in a clean TD.
---

# Restarting TouchDesigner and testing in a fresh process

Two different jobs, with different rules:

1. **Restarting the user's working TD** (their open `.toe`) so it picks something up. That's
   the user's process: **ask them to do it**. Never kill it, and save before asking.
2. **Launching your own throwaway TD** on a test `.tox` that runs a check, writes JSON and quits.
   That process is yours: launch it, time it out, kill it by PID, and clean up after it.

Most of the incidents below came from blurring the two. The fixes are cheap once you know them.

Everything here works on **Windows and macOS**. There's no launcher script to install: you write a
short one for the machine you're on (see "Writing the launcher"). Where the platforms differ, both
are given.

If you're developing **Custom OPs** (C++ plugins), also read the last section. Otherwise you can
skip it.

---

## Do you actually need a restart?

Usually not:

| Change | Restart? | Instead |
|---|---|---|
| A Python module imported by the project | No | `GET /reload` (bridge), or `importlib.reload` |
| A file-synced Text DAT | No | File sync picks it up |
| A generated `.tox` (rebuilt from a script) | No | Rebuild it in place via `/run` |
| An external-tox COMP's source `.tox` | No | Pulse **Re-Init Network** / reload the tox |
| A Python package with compiled parts (numpy, torch, opencv, …) installed or upgraded | **Yes** | — |
| Environment variables TD reads at startup | **Yes** | Set them from TD Python (`os.environ[...]`) if the code reads them later |
| A Custom OP plugin | **Yes** | See "Custom OP (C++) plugins" |

**Compiled Python packages:** `importlib.reload` re-runs Python code but can't swap a compiled
extension (`.pyd` / `.so`) that's already loaded. On Windows, `pip` often can't even overwrite it
while TD has it open. Install with TD closed, or install and then ask for a restart.

---

## Restarting the user's TD

1. **Save first, to the file the user will reopen.** With TD's incremental saving, the open
   project's *name* carries a counter (`project.7.toe`), and `project.save()` without a path, or
   a "Save" dialog, writes the next number (`project.8.toe`), not `project.toe`.
   - Save explicitly: `project.save(project.folder + '/project.toe')`.
   - Then confirm the file's timestamp on disk.
   - A big project can take minutes to save, so run the save in the background and wait for it.
   - Don't commit a `.toe` while a save is still writing.
2. **Clean up scratch nodes before the save**, so they don't persist.
3. **Ask the user to restart and to reopen that exact file.** Say why (for example "the upgraded
   numpy only loads at startup") and anything they'll need to click.
4. **After they're back, verify before testing:**
   - `GET /server-info`: the `label` and `project` are the expected ones, and the bridge port is
     back.
   - **Check the restart actually happened:** the TD process started after your change (see
     "Finding and identifying processes").
   - **Check the project is the expected file.** They may have reopened an older numbered copy.
     The window title, `project.name`, or a node you know is new will tell you. Say so if it's the
     wrong one, rather than testing against it.

**Never** kill the user's TD, even "just to restart it". Unsaved work, other projects open in it,
and bridge sessions from other agents all live there.

---

## Testing in a fresh TD process

Worth it when the result depends on a clean start:
- a `.tox` meant to drop into any project, such as a release build, tested away from the dev
  project's state, extensions and file paths;
- startup behaviour: `onCreate` / `onStart`, extension init order, environment variables;
- anything that needs a restart to take effect (see the table above);
- a test you want to repeat unattended, with a result an agent can read.

The pattern: a small **driver `.tox`** that, when TD starts on it, runs your test across frames,
writes a JSON result to a path given in an environment variable, and quits. A launcher you write
on the spot starts TD on it, waits for the result or a timeout, and cleans up.

### The driver tox

Build it from the dev project with `/run`, then save it as `<build>/RunTest.tox`. Inside one
`baseCOMP`:

- `test_driver`, a Text DAT holding the test module, with `start()` as its entry point.
- `startup`, an Execute DAT with `create` and `start` on:

```python
import os
def onStart():
	# Only in a launched test (the env var), and only once (onCreate + onStart both fire).
	if os.environ.get('TD_TEST_RESULT') and not parent().fetch('started', False):
		parent().store('started', True)
		run("op(args[0]).module.start()", parent().op('test_driver').path, delayFrames=30)
def onCreate():
	onStart()
```

The test module, sliced across frames (never block, never use a background thread for TD work):

```python
import json, os
from pathlib import Path

def start():
	cases = json.loads(os.environ['TD_TEST_CASES'])	# e.g. [{"tox": ..., "input": ...}]
	me.parent().store('cases', cases)
	me.parent().store('report', {})
	_next()

def _next():
	container = me.parent()
	cases = container.fetch('cases')
	if not cases:
		Path(os.environ['TD_TEST_RESULT']).write_text(json.dumps(container.fetch('report'), indent=1, default=str))
		project.quit(force=True)	# force: no "save changes?" dialog
		return
	# load cases[0] with container.loadTox(...), wire an input, store a frame counter ...
	run('args[0]()', _poll, delayFrames=1)

def _poll():
	# cook the outputs you check (node.cook(force=True)); stop on success or after N frames,
	# record into the report, pop the case, then run(_next, delayFrames=1)
	...
```

Notes:
- **Opening a `.tox` makes it a project.** TD wraps it in a new project whose folder is the
  `.tox`'s folder, and it may name the session `RunTest.1.toe` there. So copy the driver and the
  toxes under test into a **fresh case folder** with nothing else nearby, which is what makes it
  a clean test.
- **Pass data in through environment variables** (`TD_TEST_CASES`, `TD_TEST_RESULT`), which the
  launched process inherits. Keep paths absolute, with forward slashes (they work on both
  platforms and survive JSON).
- **Allow for slow first frames:** delay `start()` 30 frames, and poll for a generous number of
  frames. A first model load, a TensorRT engine build or an audio model warming up can take
  seconds to minutes.
- **Report what you'll need for a diagnosis,** not just pass/fail: errors and warnings of every
  node, node types, key output values, and the parameter pages.

### Writing the launcher

Write it yourself for the platform you're on: PowerShell on Windows, bash/zsh on macOS. Save it in
the case folder (with the Write tool, not a heredoc, if it has backslashes), run it, and delete it
with the case folder afterwards. It must:

1. **Find the TD executable.** If a bridge is running, ask it for the exact build:
   `/run` with `print(app.binFolder)`. Otherwise use the default install:

   | | Executable |
   |---|---|
   | Windows | `C:/Program Files/Derivative/TouchDesigner/bin/TouchDesigner.exe` (versioned installs are `TouchDesigner.<build>/bin/...`) |
   | macOS | `/Applications/TouchDesigner.app/Contents/MacOS/TouchDesigner` |

   If there are several builds, test on the one the user runs, and say which one you used.
2. **Set the environment variables** (`TD_TEST_RESULT`, `TD_TEST_CASES`, anything else) in the
   launching shell, so the child inherits them. Delete any old result file first.
3. **Start the executable directly with the target as its argument, and keep the PID.** On macOS
   don't use `open -a`: it goes through Launch Services, which doesn't reliably pass the
   environment and doesn't give you the PID.
4. **Wait** until the result file appears, the process exits, or the timeout passes (poll every
   0.5 s).
5. **Report:** print the result, or say why there's none: TD exited (with its code), or timed out.
   The usual cause of a timeout is a dialog TD is waiting on.
6. **Clean up, even on failure** (`try/finally` in PowerShell, `trap ... EXIT` in bash): give TD
   up to ~15 s to quit on its own, then stop **only that PID**.
7. **Exit 0 only if a result was written.**

Run it with a tool timeout longer than the launcher's own.

**Windows (PowerShell):**

```powershell
$td = 'C:/Program Files/Derivative/TouchDesigner/bin/TouchDesigner.exe'
$case = 'C:/tmp/case'; $result = "$case/result.json"
Remove-Item $result -ErrorAction SilentlyContinue
$env:TD_TEST_RESULT = $result
$env:TD_TEST_CASES = ConvertTo-Json -Compress @(@{ tox = "$case/Foo.tox" })
$p = Start-Process $td -ArgumentList "`"$case/RunTest.tox`"" -WorkingDirectory $case -PassThru
$ok = $false
try {
	$deadline = (Get-Date).AddSeconds(300)
	while (-not (Test-Path $result) -and -not $p.HasExited -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
	if (Test-Path $result) { Start-Sleep -Milliseconds 500; Get-Content -Raw $result; $ok = $true }
	elseif ($p.HasExited) { Write-Warning "No result: TD exited ($($p.ExitCode))" }
	else { Write-Warning 'No result: timed out (a blocking dialog?)' }
	if (-not $p.HasExited) { $p.WaitForExit(15000) | Out-Null }
} finally {
	if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }	# our PID only
}
exit ([int](-not $ok))
```

**macOS (bash/zsh):**

```bash
TD=/Applications/TouchDesigner.app/Contents/MacOS/TouchDesigner
CASE=/tmp/case; RESULT="$CASE/result.json"
rm -f "$RESULT"
export TD_TEST_RESULT="$RESULT"
export TD_TEST_CASES='[{"tox": "/tmp/case/Foo.tox"}]'
cd "$CASE"
"$TD" "$CASE/RunTest.tox" >/dev/null 2>&1 &
PID=$!
cleanup() {
	kill -0 "$PID" 2>/dev/null && kill "$PID"	# our PID only
}
trap cleanup EXIT
for _ in $(seq 600); do	# 600 x 0.5 s = 300 s
	[ -f "$RESULT" ] && break
	kill -0 "$PID" 2>/dev/null || break
	sleep 0.5
done
if [ -f "$RESULT" ]; then
	sleep 0.5; cat "$RESULT"
	for _ in $(seq 30); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
	exit 0
fi
kill -0 "$PID" 2>/dev/null && echo "No result: timed out (a blocking dialog?)" >&2 || echo "No result: TD exited" >&2
exit 1
```

These are sketches. Adjust the paths, cases and timeout.

---

## Finding and identifying processes

**Windows (PowerShell):**

```powershell
Get-Process TouchDesigner | ForEach-Object {
	"{0} {1} {2}" -f $_.Id, $_.StartTime, $_.MainWindowTitle	# the title shows the open project path
}
```

**macOS:**

```bash
pgrep -fl TouchDesigner                     # PIDs, with the file each was launched on (if any)
ps -o pid,lstart,command -p <pid>           # start time
```

A TD the user opened from the Finder shows no file in its command line. Use its bridge's
`/server-info` (`project`, `projectFolder`) to tell which project it is.

- **Match before you stop anything:** the PID you launched, *and* its window title (Windows) or
  command line (macOS) containing your case folder. Never stop TD by name (`taskkill /IM`,
  `killall`, `pkill`), and never stop "all TouchDesigner".
- On Windows, a process with an empty title is usually still starting, or showing a dialog.

---

## Custom OP (C++) plugins

Only for developing Custom OPs: C++ operators compiled to a `.dll` (Windows) or a `.plugin` bundle
(macOS) and loaded from a Plugins folder. TD registers Custom OP types only at startup, so most of
the restarts and fresh-process tests in this skill came from this work.

### Why a rebuild needs a restart

A loaded plugin stays loaded.
- **Windows** locks loaded DLLs, so a rebuild can't overwrite one while TD runs. Build scripts
  usually *rename* the old one aside (`Foo.dll.old-<time>`) and copy the new one in. TD keeps
  running the renamed file, and **the module path that tools show doesn't change after a rename**:
  the process still lists `...\Foo.dll` while it's actually the old file.
- **macOS** doesn't lock loaded libraries, so the rebuild overwrites or deletes the file without
  complaint, and TD keeps running the old code it already mapped. Nothing on disk tells you that.

Either way, judge by the **process start time against the build time**, not by the path. Adding or
removing plugins in a Plugins folder needs a restart too.

**Dev shortcut, no restart:** load a *renamed copy* of the new plugin through a generic
CPlusPlus TOP/CHOP/SOP (`par.plugin = '.../build/dev/Foo_dev3.dll'`, a new name each time, since TD
caches by path). The Custom OP type stays on the old plugin, but this node runs the new code.
- If the plugin loads sibling libraries "from next to itself", the copy's folder won't have them.
  Give the plugin an environment-variable override for that folder and set it from TD Python
  before creating the node.
- Destroy the scratch node afterwards (`_`-prefixed COMP, verified gone).
- Don't use this for anything that must be the real Custom OP type, such as building wrappers that
  `create('FooTOP')`. Those need the restart.

**When asking the user to restart for a plugin,** tell them to expect an **Allow** prompt for any
plugin whose path or hash isn't in `Plugins.json` yet. Afterwards, check that the process loaded the
plugin from the expected place (below).

### Which plugins a process has loaded

That's how you tell "the test never loaded the plugin" from "it loaded but failed", and see where
it came from.

```powershell
# Windows
Get-Process TouchDesigner | ForEach-Object {
	"{0} {1}" -f $_.Id, $_.MainWindowTitle
	$_.Modules | Where-Object ModuleName -match 'MyPlugin' | ForEach-Object { '   ' + $_.FileName }
}
```

```bash
# macOS
lsof -p <pid> | grep -i MyPlugin
```

### Plugins in a fresh process

The test TD only has the Custom OPs it finds in its Plugins folders, and when TD opens a `.tox`
directly, a project-local `Plugins/` folder isn't scanned. For a realistic test, install the
plugins where users install them, in a subfolder named for the package:

| | Shared Plugins folder |
|---|---|
| Windows | `Documents/Derivative/Plugins/<package>/` (`.dll` files) |
| macOS | `~/Library/Application Support/Derivative/TouchDesigner099/Plugins/<package>/` (`.plugin` bundles) |

(From TD's own docs: `/help?page=Custom_Operators`.) Add to the launcher: copy the plugin folder
there before starting TD, and in cleanup, after stopping TD, restore `Plugins.json` and then remove
the folder.

- **Install every plugin the toxes need.** A missing plugin doesn't fail loudly: the Custom OP shows
  "plugin not found", nothing cooks, and the test just times out. List the files explicitly, and
  re-check the list whenever a plugin is added. Also report node types, to confirm the Custom OP
  loaded at all.
- **`Plugins.json` is TD's allow-list,** keyed by the plugin's absolute path plus a hash. A new path
  or a rebuilt plugin brings up an **Allow** prompt that blocks the test until someone clicks it.
  - On Windows it sits in `Documents/Derivative/Plugins/`. On macOS, look for it under
    `~/Library/Application Support/Derivative/` and confirm where it is before relying on it.
  - Tell the user before launching: "click Allow when the fresh TD asks".
  - Save `Plugins.json` before the test and **restore it first in cleanup, before anything that
    can fail**.
  - If it's ever left changed, remove only the entries whose path contains the temporary install
    folder. Leave the user's other entries alone, and keep a backup of the file.
- **The install is global while it exists.** *Any* TD started meanwhile loads it, including the
  user's other projects. One user opened another project mid-test; it loaded both its own copies
  of the same Custom OPs and the test's, and then held the test's DLLs locked so cleanup couldn't
  delete them. So:
  - keep the window short;
  - warn the user if a TD is already running, and tell them not to start one during the test;
  - refuse to install over an existing folder (it would mask the test);
  - **Windows:** if removal fails because a TD holds the DLLs, **move the folder out of the
    Plugins path** (for example to `%TEMP%`) rather than leaving it: loaded DLLs can't be deleted
    but can be moved on the same drive. Running TDs keep working, and new ones won't load it.
  - **macOS:** removal succeeds even while a TD has the plugin loaded, so check first who holds it
    (`lsof +D <install folder>`) and report it: that TD is still running the test's code.
  - Either way, say which process held it and where the folder went. (A leftover install once got
    loaded by two more TDs the user opened later.)
- Kill stray helper processes the plugin may have spawned from the install folder (for example a
  TensorRT builder `.exe`) before deleting it.

---

## Checklist

- [ ] Is a restart really needed? Use `/reload` or a rebuild if not.
- [ ] For the user's TD: clean up scratch nodes, save explicitly to the main `.toe`, wait for the
      save, and ask them to restart (say why, and which file to open). Then verify the project.
- [ ] For a fresh-process test: use a fresh case folder, pass inputs and the result path through
      environment variables, and run the work across frames ending in `project.quit(force=True)`.
- [ ] Write the launcher for this platform: TD executable started directly, PID kept, timeout,
      cleanup that always runs. Stop only your own PID.
- [ ] Afterwards check that no test TD is still running, and the launcher and case folder are
      deleted.
- [ ] **Custom OPs only:** install every plugin the toxes need; warn the user about the Allow
      prompt and about not starting TD meanwhile; in cleanup restore `Plugins.json` first, then
      remove the install, and report what holds it on failure; leave no install or allow-list
      entry behind.
