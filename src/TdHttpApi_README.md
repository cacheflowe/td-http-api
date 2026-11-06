# TouchDesigner HTTP Bridge — Agent Quick Start

Welcome to `TdHttpApi`, the HTTP bridge that gives **AI agents autonomous read/write access** to your running TouchDesigner project. Agents can inspect, build, and debug networks directly via a REST API and open-ended Python scripting, mirroring everything that's possible with traditional manual editing in the TouchDesigner UI. This allows your coding agent (Claude/Copilot/etc) to **see the network, understand it, and modify it**, which gives you the power of agentic coding tools that are commonly used in traditional software development, but now applied to the TouchDesigner IDE.

> ⚠️ Any agentic interaction can change the network immediately. Most edits can be undone with
> Ctrl+Z (each agent call is one undo step), but file writes and extension state can't. Use caution
> with destructive operations, and back up your project often.

This tool is a work-in-progress, and the API is still evolving. If you have questions or suggestions, please reach out or open an issue on GitHub. The goal is to make this a robust and reliable tool for agentic TouchDesigner development.

## What You Can Do

### 🔍 **Inspect & Debug Networks**
- **See the full network structure** — all nodes, wires, and parameters as JSON
- **Find errors instantly** — list all broken operators and their error messages
- **Verify what's running** — inspect cook times, memory, and performance metrics
- **Read any DAT** — pull Python script or CSV content without opening files

### 🛠️ **Find & Fix Problems**
- **Identify parameter mistakes** — discover the exact param names instead of guessing (`radx` vs `rad0`)
- **Check operator signatures** — know if a node expects wired inputs or parameter references
- **Trace references** — see which nodes depend on which, spot broken chains
- **Understand errors in context** — read the full error message and the surrounding network

### 📖 **Explain Networks**
- **Generate network diagrams** — export as Mermaid (`/network.mmd`) for visualization
- **List all connections** — see data flow without looking at the UI
- **Describe patterns** — understand what each part of the network does

### 📚 **Learn from Official Examples**
- **Search official example networks** — Derivative's ~1400 OP Snippets, read from your TD install (via `/examples`)
- **Ask about node types** — get explanations and use cases for any TouchDesigner operator
- **Learn techniques** — understand how to wire feedback loops, manage state, compose effects, etc.

### 🏗️ **Build Networks Programmatically**
- **Create nodes** — specify type, position, and parameters
- **Wire them up** — connect inputs/outputs with full control
- **Batch operations** — build entire sub-networks in one script
- **Hot-reload Python** — edit your scripts and reload without restarting TD

### 🎯 **Other Powers**
- **Set parameters** — read and write any parameter value or expression
- **Run Python** — execute one-off scripts and capture output
- **Move & organize** — reposition nodes, add comments, group with annotations
- **Compare snapshots** — diff two network states to see exactly what changed

---

## Getting Started

### Step 1: Install the TdHttpApi Component

Drop the `TdHttpApi.tox` component into your TouchDesigner project. It runs a local HTTP server on
port 3031, exposing the entire network and Python API.

**Running several TD apps?** Each bridge takes the first free port from **Port** to Port + **Port
Range** (3031–3040 by default). The COMP's viewer shows the port it got and its **App Label**. Give
each app a label so you can tell your agent which one to use.

### Step 2: Verify the Bridge Is Live

Run `curl http://127.0.0.1:3031/server-info` (use the port shown in the COMP viewer). You should see
a JSON response with the app's label. Browsers can't talk to the bridge: it refuses any request a
web page sends, so a malicious site can't run Python in your project.

If this fails, check:
1. Is TouchDesigner running and the project open?
2. Does the COMP viewer show a port, or "stopped"? Pulse **Restart Server** if it says stopped.
3. Are you using `127.0.0.1` (not `localhost`)?

### Step 3: Connect Your Agent

Pulse **Copy Prompt** on the COMP and paste the result as your agent's first message. That copies
the full orientation prompt with this app's port filled in. The read-only **Agent Prompt** parameter
is a one-line alternative that points the agent at this app's `/init` route, which serves the same
prompt. The source is the `prompt_agent_init` DAT.

**Watch your agent work:** pulse **Open Dashboard** (or open `http://127.0.0.1:3031/ui`). It's a
live feed of every agent call: what it changed, what failed and why, and which undo step it
became. Click a node to jump to it in TD. It also explains how the bridge works and lists every
route. Click **Try** on any read-only route to run it and see exactly what it returns.

**Undo:** each agent edit is one named undo step ("Agent /par …", "Agent /run"), so Ctrl+Z in TD
reverts it.

**Agent page** (on the COMP):
- **Flash Changed Nodes** (on): each node an agent changes briefly turns amber, so you can watch
  it work. **Pan To Changes** (off) also moves your network editor to it.
- **Confirm Agent Deletes** (off): ask before an agent deletes anything. You get a Delete,
  Bypass instead or Keep choice. Nothing inside **Sandbox Path** asks.
- **Slow Call Threshold / Cooldown**: after a very slow call, the bridge briefly refuses new ones
  so TD stays responsive.

**No registration needed:** agents talk to the bridge over plain HTTP, so the pasted prompt is all
the setup there is. **MCP is optional:** the bridge also speaks MCP at
`http://127.0.0.1:<port>/mcp`, where every route is a `td_*` tool. Register it when you want
images returned straight to the model, per-tool permissions in your harness, or a client without
a shell (Claude Desktop). For Claude Code:
`claude mcp add --transport http td-bridge http://127.0.0.1:3031/mcp`.

**TouchDesigner docs are built in:** agents read TD's own offline documentation (for your exact TD
build) through the bridge's `/help` route. No separate docs server to install.

**Version:** the read-only **Version** parameter shows which release this is.

### What the bridge writes to disk

Nothing in your project unless you ask:
- **A cache, outside your project** (~11 MB, one folder per TD build) in your user TD preferences
  folder: `%LOCALAPPDATA%/Derivative/TouchDesigner099/TdHttpApi/cache/<build>/` on Windows.
  - `op_snippets_catalog.tsv`: the official example networks list, built the first time an agent
    searches the examples (~2 s).
  - `help_index.json.gz`: the docs search index, built in the background after an agent's first
    docs request (~40 s, without slowing TD down), then loaded in ~2 s in later sessions.
  - Nothing is built at startup. `GET /cache` lists it with what each file is;
    `POST /cache?clear=true` deletes it, and it's rebuilt when needed.
- **Anything an agent explicitly saves** (`/save`, `/dat` file edits, `/run` scripts).

---

## Optional Additions (Unlocks More 🤖 Power)

These aren't required, but they'll make your work smarter and faster, with fewer hallucinations and mistakes:

### **Skills & Documentation**
The bridge carries its own agent docs and serves them to the agent (`/init`, `/docs`), so there's nothing to copy into your project. They provide **high-level guidance** for your agent, including:
- `td-http-api.md` — friction points and best practices
- `td-network-craft.md` — idiomatic network-building technique
- `td-common-mistakes.md` — common TD Python traps to avoid

### **TouchDesigner docs**
Built in: agents ask the bridge (`/help`), which reads TD's own offline documentation for your
build. For example "What parameters does a `poptoCHOP` have?" becomes
`/help?op=poptoCHOP&section=Parameters`.

---

Good luck! 🚀
