import io
import json
import os
import re
import sys
import time
import traceback
import inspect
import base64
import csv
import shutil

# The bridge's release version: shown in /server-info, MCP serverInfo, the Version par, release file
# names. scripts/release.py bumps it; edit it nowhere else.
VERSION = '0.2.0'

# ---------------------------------------------------------------------------
# Network description (self-contained - no td_util dependency, so this file
# can be dropped into any project as a single Callbacks DAT).
# ---------------------------------------------------------------------------

def _get_current_network():
	"""Return the COMP currently displayed in a Network Editor pane (prefers the active pane), falling back to project root."""
	current = ui.panes.current
	if current is not None and current.type.name == 'NETWORKEDITOR':
		return current.owner
	for p in ui.panes:
		if p.type.name == 'NETWORKEDITOR':
			return p.owner
	return op('/')


def _get_network_editor_pane():
	"""Return the NetworkEditor pane object itself (not its owner) - prefers the active pane."""
	current = ui.panes.current
	if current is not None and current.type.name == 'NETWORKEDITOR':
		return current
	for p in ui.panes:
		if p.type.name == 'NETWORKEDITOR':
			return p
	return None


def _nodes_in_comp(comp):
	return comp.findChildren(depth=1, includeUtility=True)


def _par_json_value(par):
	"""Serialize a parameter's evaluated value into something JSON-safe (OPs become their path)."""
	if par.isOP:
		vals = par.eval()
		if vals is None:
			return None
		if isinstance(vals, list):
			return [v.path for v in vals]
		return vals.path
	try:
		return par.eval()
	except Exception:
		return str(par.val)


def _describe_par(par):
	"""JSON-safe snapshot of a single parameter's current state: name, mode, evaluated value, and expr if applicable."""
	entry = {'name': par.name, 'mode': par.mode.name, 'value': _par_json_value(par)}
	if par.mode.name == 'EXPRESSION':
		entry['expr'] = par.expr
	return entry


def _get_customized_pars(target):
	"""Return {parName: {mode, value, [expr]}} for params that differ from default, mirroring the params dialog's 'Show Custom Only' view."""
	result = {}
	for par in target.pars('*'):
		if par.isDefault or par.hidden:
			continue
		entry = _describe_par(par)
		del entry['name']
		result[par.name] = entry
	return result


_OP_EXPR_PATTERN = re.compile(r"\bop(?:ex)?\(\s*['\"]([^'\"]+)['\"]")


def _as_ref_op(candidate):
	"""Normalize a reference target to an OP. Despite Par.bindMaster being documented as OP-only,
	a parameter bound directly to another parameter (e.g. bindExpr = "op('other1').par.tx") returns
	that Par, not its owning OP - so any bare candidate.path lookup on it fails. Fall back to
	.owner when the candidate looks like a Par (has .owner, no .path of its own)."""
	if candidate is not None and not hasattr(candidate, 'path') and hasattr(candidate, 'owner'):
		candidate = candidate.owner
	return candidate


def _get_op_references(target):
	"""Return dashed-line style references from target to other ops: OP-type params, binds, exports,
	and op()/opex() calls found inside expression-mode parameters."""
	refs = []
	seen = set()

	def add(candidate, kind, par_name):
		candidate = _as_ref_op(candidate)
		if candidate is None or not candidate.valid or candidate is target:
			return
		key = (candidate.path, kind, par_name)
		if key in seen:
			return
		seen.add(key)
		refs.append({'from': target.path, 'to': candidate.path, 'kind': kind, 'par': par_name})

	for par in target.pars('*'):
		if par.hidden or par.isDefault:
			continue
		if par.isOP:
			vals = par.eval()
			for candidate in (vals if isinstance(vals, list) else ([vals] if vals else [])):
				add(candidate, 'opParam', par.name)
		if par.bindMaster:
			add(par.bindMaster, 'bind', par.name)
		if par.exportOP:
			add(par.exportOP, 'export', par.name)
		if par.mode.name == 'EXPRESSION' and par.expr:
			expr_root = target.parent() or target
			for m in _OP_EXPR_PATTERN.finditer(par.expr):
				add(expr_root.op(m.group(1)), 'expression', par.name)

	return refs


def _get_wires(nodes):
	"""Return wired (solid line) connections among the given nodes as {from, to, toInput} dicts."""
	node_paths = {n.path for n in nodes}
	wires = []
	for node in nodes:
		for idx, src in enumerate(node.inputs):
			if src is not None and src.path in node_paths:
				wires.append({'from': src.path, 'to': node.path, 'toInput': idx})
	return wires


def describe_network(comp=None, recursive=False):
	"""Build a lightweight structural map of a network: nodes, wired connections, dashed-line
	references, and customized (non-default) parameters. Node/wire/reference lists are sorted
	deterministically so repeated calls diff cleanly."""
	if comp is None:
		comp = _get_current_network()
	nodes = comp.findChildren(includeUtility=True) if recursive else _nodes_in_comp(comp)

	node_entries = []
	references = []
	for node in nodes:
		entry = {
			'path': node.path,
			'name': node.name,
			'opType': node.opType,
			'family': node.family,
			'nodeX': node.nodeX,
			'nodeY': node.nodeY,
			'nodeWidth': node.nodeWidth,
			'nodeHeight': node.nodeHeight,
		}
		if node.comment:
			entry['comment'] = node.comment
		if node.opType == 'annotateCOMP':
			entry['enclosedOPs'] = sorted(n.path for n in node.enclosedOPs)
		custom = _get_customized_pars(node)
		if custom:
			entry['customPars'] = custom
		node_entries.append(entry)
		references.extend(_get_op_references(node))

	node_entries.sort(key=lambda e: e['path'])
	references.sort(key=lambda r: (r['from'], r['to'], r['kind'], r['par']))
	wires = _get_wires(nodes)
	wires.sort(key=lambda w: (w['to'], w['toInput']))

	return {
		'root': comp.path,
		'nodes': node_entries,
		'wires': wires,
		'references': references,
	}


def _mermaid_id(path):
	return re.sub(r'\W', '_', path)


def network_to_mermaid(comp=None, recursive=False):
	"""Render describe_network() as a Mermaid flowchart: solid arrows for wires, dashed arrows for references."""
	data = describe_network(comp, recursive)
	lines = ['flowchart TD']
	for node in data['nodes']:
		node_id = _mermaid_id(node['path'])
		label = f"{node['name']}\\n[{node['opType']}]"
		if 'customPars' in node:
			label += f"\\nΔ{len(node['customPars'])}"
		lines.append(f'\t{node_id}["{label}"]')
	for wire in data['wires']:
		lines.append(f"\t{_mermaid_id(wire['from'])} --> {_mermaid_id(wire['to'])}")
	for ref in data['references']:
		lines.append(f"\t{_mermaid_id(ref['from'])} -.->|{ref['kind']}| {_mermaid_id(ref['to'])}")
	return '\n'.join(lines)


def _diff_nodes(before_nodes, after_nodes):
	before_by_path = {n['path']: n for n in before_nodes}
	after_by_path = {n['path']: n for n in after_nodes}
	added = sorted(set(after_by_path) - set(before_by_path))
	removed = sorted(set(before_by_path) - set(after_by_path))
	changed = []
	for path in sorted(set(before_by_path) & set(after_by_path)):
		b, a = before_by_path[path], after_by_path[path]
		node_changes = {}
		for key in ('nodeX', 'nodeY', 'nodeWidth', 'nodeHeight', 'opType', 'family', 'name'):
			if b.get(key) != a.get(key):
				node_changes[key] = {'before': b.get(key), 'after': a.get(key)}
		b_pars = b.get('customPars', {})
		a_pars = a.get('customPars', {})
		par_added = sorted(set(a_pars) - set(b_pars))
		par_removed = sorted(set(b_pars) - set(a_pars))
		par_changed = sorted(p for p in (set(a_pars) & set(b_pars)) if a_pars[p] != b_pars[p])
		if par_added or par_removed or par_changed:
			node_changes['customPars'] = {
				'added': {p: a_pars[p] for p in par_added},
				'removed': {p: b_pars[p] for p in par_removed},
				'changed': {p: {'before': b_pars[p], 'after': a_pars[p]} for p in par_changed},
			}
		if node_changes:
			changed.append({'path': path, 'changes': node_changes})
	return {'added': added, 'removed': removed, 'changed': changed}


def _diff_edges(before_edges, after_edges, key_fields):
	def key(e):
		return tuple(e[f] for f in key_fields)
	before_by_key = {key(e): e for e in before_edges}
	after_by_key = {key(e): e for e in after_edges}
	added = [after_by_key[k] for k in sorted(after_by_key.keys() - before_by_key.keys())]
	removed = [before_by_key[k] for k in sorted(before_by_key.keys() - after_by_key.keys())]
	return {'added': added, 'removed': removed}


def diff_networks(before, after):
	"""Structural diff between two describe_network() snapshots: added/removed/changed nodes,
	and added/removed wires and references. Both inputs must be full snapshot dicts, not paths."""
	return {
		'nodes': _diff_nodes(before.get('nodes', []), after.get('nodes', [])),
		'wires': _diff_edges(before.get('wires', []), after.get('wires', []), ('from', 'to', 'toInput')),
		'references': _diff_edges(before.get('references', []), after.get('references', []), ('from', 'to', 'kind', 'par')),
	}


# ---------------------------------------------------------------------------
# HTTP request helpers
# ---------------------------------------------------------------------------

class MethodNotAllowedError(Exception):
	pass


class BridgeError(ValueError):
	"""A 4xx with a stable machine-readable `code` and structured `details` (suggestions, valid
	values, counts) so an agent can fix its call without another round trip. Plain ValueErrors
	still work and become code 'bad_request'."""

	def __init__(self, message, code='bad_request', status=400, **details):
		super().__init__(message)
		self.code = code
		self.status = status
		self.details = {k: v for k, v in details.items() if v is not None}


def _suggest(name, candidates, n=5):
	"""Closest matches for a mistyped name (case-insensitive), most likely first."""
	import difflib
	if not name:
		return []
	lowered = {}
	for c in candidates:
		lowered.setdefault(str(c).lower(), c)
	hits = difflib.get_close_matches(str(name).lower(), list(lowered), n=n, cutoff=0.5)
	prefix = [c for low, c in lowered.items() if low.startswith(str(name).lower()) and lowered[low] not in hits]
	return [lowered[h] for h in hits] + prefix[:max(0, n - len(hits))]


def _get_par(target, name):
	"""target.par[name], or a BridgeError suggesting the nearest names (matching labels too, since
	agents often guess from the UI label: 'Font Size X' -> fontsizex)."""
	par = target.par[name] if name else None
	if par is not None:
		return par
	pars = target.pars('*')
	by_label = {p.label.lower().replace(' ', ''): p.name for p in pars}
	label_hit = by_label.get(str(name).lower().replace(' ', '').replace('_', ''))
	suggestions = ([label_hit] if label_hit else []) + _suggest(name, [p.name for p in pars])
	raise BridgeError(
		f"no parameter named '{name}' on '{target.path}' ({target.OPType})",
		code='par_not_found', path=target.path, opType=target.OPType,
		suggestions=list(dict.fromkeys(suggestions))[:5] or None,
		hint="GET /pars?path=<op> lists every parameter name, label and menu.")


def _first_param(pars, name, default=None):
	"""pars values may arrive as a list (repeated query params) or a bare string; normalize to a single value."""
	val = pars.get(name, default)
	if isinstance(val, list):
		return val[0] if val else default
	return val


def _bool_param(pars, name, default=False):
	val = _first_param(pars, name)
	if val is None:
		return default
	return val.lower() in ('1', 'true', 'yes')


def _resolve_comp(pars):
	path = _first_param(pars, 'path')
	if not path:
		return None  # let describe_network default to the currently open network
	target = op(path)
	if target is None:
		raise ValueError(f"no operator found at path '{path}'")
	return target


def _resolve_op(path):
	if not path:
		raise ValueError("missing required 'path' query parameter")
	target = op(path)
	if target is None:
		# Try to resolve utility nodes by looking up parent and finding child including utilities
		parent_path = os.path.dirname(path).replace('\\', '/')
		name = os.path.basename(path)
		parent = op(parent_path) if parent_path else None
		if parent is not None:
			found = parent.findChildren(name=name, depth=1, includeUtility=True)
			if found:
				target = found[0]
	if target is None:
		_raise_op_not_found(path)
	return target


def _raise_op_not_found(path, what='operator'):
	"""BridgeError for a bad path, suggesting siblings with similar names from the deepest
	existing ancestor."""
	path = str(path).rstrip('/')
	parent_path, _, name = path.rpartition('/')
	parent = op(parent_path or '/')
	while parent is None and parent_path:
		parent_path, _, name = parent_path.rpartition('/')
		parent = op(parent_path or '/')
	suggestions = None
	if parent is not None and getattr(parent, 'isCOMP', False):
		names = [c.name for c in parent.findChildren(depth=1, includeUtility=True)]
		suggestions = [f"{parent.path.rstrip('/')}/{s}" for s in _suggest(name, names)] or None
	raise BridgeError(f"no {what} found at path '{path}'", code='op_not_found', path=path,
		suggestions=suggestions,
		hint=None if suggestions else 'GET /network?path=<comp> lists what exists in a COMP.')


def _menu_value(par, raw):
	"""Validate a value for a strict menu par: a menu name, a label (case-insensitive) or an
	index all work; anything else is a BridgeError listing the valid choices."""
	names = list(par.menuNames)
	if raw in names:
		return raw
	labels = list(par.menuLabels)
	for n, l in zip(names, labels):
		bare_label = re.sub(r'\s*\(.*?\)\s*$', '', l).lower()	# 'Simplex 3D (GPU)' also matches 'Simplex 3D'
		if str(raw).lower() in (n.lower(), l.lower(), bare_label):
			return n
	if re.fullmatch(r'\d+', str(raw)) and int(raw) < len(names):
		return names[int(raw)]
	raise BridgeError(
		f"'{raw}' is not a valid value for menu '{par.name}' on '{par.owner.path}'",
		code='invalid_menu_value', path=par.owner.path, par=par.name,
		validValues=names, validLabels=labels, suggestions=_suggest(raw, names + labels) or None)


def _coerce_par_value(par, raw):
	"""Convert a raw value (query-string text, or a JSON scalar from a batch body) into whatever
	type the parameter actually expects. Strict menus are validated (StrMenu pars accept any
	text, as in the UI)."""
	if isinstance(raw, bool):
		raw = 'true' if raw else 'false'
	if par.isToggle:
		return str(raw).lower() in ('1', 'true', 'yes', 'on')
	if par.isMenu and par.style == 'Menu':
		return _menu_value(par, str(raw))
	try:
		if par.isInt:
			return int(float(raw))
		if par.isFloat:
			return float(raw)
	except (TypeError, ValueError):
		raise BridgeError(f"'{raw}' is not a number, but '{par.name}' on '{par.owner.path}' is a {par.style} par",
			code='invalid_value', path=par.owner.path, par=par.name, style=par.style)
	if par.isOP:
		if raw in ('', None):
			return None
		target = par.owner.op(raw) or op(raw)
		if target is None:
			_raise_op_not_found(raw, what=f"operator (for OP par '{par.name}')")
		return target
	return raw


def _resolve_inputs(raw):
	if not raw:
		return []
	result = []
	for p in raw.split(','):
		p = p.strip()
		if not p:
			continue
		target = op(p)
		if target is None:
			raise ValueError(f"no operator found at path '{p}' for input")
		result.append(target)
	return result


def _node_summary(n):
	return {
		'path': n.path, 'name': n.name, 'opType': n.opType, 'family': n.family,
		'nodeX': n.nodeX, 'nodeY': n.nodeY, 'nodeWidth': n.nodeWidth, 'nodeHeight': n.nodeHeight,
		'viewer': n.viewer,
	}


def _op_error_state(n):
	return {'path': n.path, 'name': n.name, 'opType': n.opType, 'errors': n.errors(), 'warnings': n.warnings()}


_IMAGE_MIME_TYPES = {
	'.png': 'image/png',
	'.jpg': 'image/jpeg',
	'.jpeg': 'image/jpeg',
	'.bmp': 'image/bmp',
	'.tif': 'image/tiff',
	'.tiff': 'image/tiff',
	'.exr': 'image/x-exr',
	'.dds': 'image/vnd-ms-dds',
}


def _chop_data(chop, max_samples=100):
	"""Most-recent max_samples samples of every channel in a CHOP, plus rate/count metadata."""
	n = min(chop.numSamples, max_samples) if max_samples else chop.numSamples
	start = chop.numSamples - n
	channels = {}
	for c in chop.chans():
		channels[c.name] = [c[i] for i in range(start, chop.numSamples)]
	return {
		'numChans': chop.numChans,
		'numSamples': chop.numSamples,
		'samplesReturned': n,
		'rate': chop.rate,
		'channels': channels,
	}


def _snapshot_top(top, filetype='.png', quality=1.0):
	"""Render a TOP's current frame to an in-memory image (no disk write) via TOP.saveByteArray().
	Returns (bytearray, mimeType)."""
	if filetype not in _IMAGE_MIME_TYPES:
		raise ValueError(f"unsupported snapshot format '{filetype}'. Supported: {sorted(_IMAGE_MIME_TYPES)}")
	data = top.saveByteArray(filetype, quality=quality)
	return data, _IMAGE_MIME_TYPES[filetype]


def _bounds_of(nodes):
	"""Aggregate bounding box of a list of ops, in network-editor units. nodeX/nodeY anchor each
	node's left/bottom corner (TD convention), so the box extends right/up by width/height."""
	min_x = min(n.nodeX for n in nodes)
	min_y = min(n.nodeY for n in nodes)
	max_x = max(n.nodeX + n.nodeWidth for n in nodes)
	max_y = max(n.nodeY + n.nodeHeight for n in nodes)
	return {
		'minX': min_x, 'minY': min_y, 'maxX': max_x, 'maxY': max_y,
		'width': max_x - min_x, 'height': max_y - min_y,
		'count': len(nodes),
		'nodes': sorted(n.path for n in nodes),
	}


def _move_node(node, x, y):
	"""Move an op and keep its docked ops (e.g. a rampTOP's keys table, a GLSL TOP's shader DATs)
	at the same offset, as dragging it in the editor does."""
	dx, dy = x - node.nodeX, y - node.nodeY
	node.nodeX, node.nodeY = x, y
	for d in getattr(node, 'docked', None) or []:
		d.nodeX += dx
		d.nodeY += dy


def _rects_overlap(x, y, w, h, n):
	return not (x + w <= n.nodeX or n.nodeX + n.nodeWidth <= x or y + h <= n.nodeY or n.nodeY + n.nodeHeight <= y)


def _create_annotation(parent, nodes, title='', body='', mode='networkbox', pad=40, title_height=30):
	"""Create an annotateCOMP sized to enclose the given nodes' bounding box (plus padding, and
	extra headroom for the title bar), with encloseops on so moving it later drags the group with it."""
	box = _bounds_of(nodes)
	annotation = parent.create('annotateCOMP')
	annotation.viewer = True
	annotation.nodeX = box['minX'] - pad
	annotation.nodeY = box['minY'] - pad
	annotation.nodeWidth = box['width'] + 2 * pad
	annotation.nodeHeight = box['height'] + 2 * pad + title_height
	annotation.par.Mode = mode
	if title:
		annotation.par.Titletext = title
	if body:
		annotation.par.Bodytext = body
	annotation.par.encloseops = True
	return annotation


def _auto_place(parent, inputs, exclude=None, width=130, height=90, pad_x=70, pad_y=20):
	"""Placement for a new node when x/y aren't given: to the right of (and vertically averaged
	with) its inputs, following dataflow left-to-right; falls back to right of existing siblings
	if there are no inputs. Nudges down to clear any overlap with existing nodes in the comp."""
	siblings = [n for n in _nodes_in_comp(parent) if n is not exclude]
	if inputs:
		x = max(n.nodeX + n.nodeWidth for n in inputs) + pad_x
		y = sum(n.nodeY for n in inputs) / len(inputs)
	elif siblings:
		x = max(n.nodeX + n.nodeWidth for n in siblings) + pad_x
		y = min(n.nodeY for n in siblings)
	else:
		x, y = 0, 0
	attempts = 0
	while attempts < 50 and any(_rects_overlap(x, y, width, height, n) for n in siblings):
		y -= height + pad_y
		attempts += 1
	return x, y


def _require_write_method(request, uri):
	if request['method'] not in ('POST', 'PUT'):
		raise MethodNotAllowedError(f"{uri} requires POST or PUT")


# ---------------------------------------------------------------------------
# Request logging - in-memory ring buffer, readable via GET /logs.
# Resets on /reload (module-level state), which is fine for a debugging aid.
# ---------------------------------------------------------------------------

_REQUEST_LOG = []
_REQUEST_LOG_MAX = 300
_LOG_EPOCH = time.time()	# changes when the module reloads, so the dashboard knows ids restarted
_log_seq = 0


def _log_request(entry):
	global _log_seq
	_log_seq += 1
	entry['id'] = _log_seq
	_REQUEST_LOG.append(entry)
	del _REQUEST_LOG[:-_REQUEST_LOG_MAX]


def _request_source(request):
	"""Who made the call: 'dashboard' (this bridge's /ui page), 'mcp', 'curl', or the client name."""
	if request.get('_source'):
		return request['_source']
	client = _header(request, 'X-TD-Client')
	if client:
		return str(client)[:24]
	agent = str(_header(request, 'User-Agent') or '')
	if agent.lower().startswith('curl'):
		return 'curl'
	if 'Mozilla' in agent:
		return 'browser'
	return agent.split('/')[0][:24] or 'unknown'


def _args_summary(pars, limit=90):
	out = {}
	for k, v in (pars or {}).items():
		v = v[0] if isinstance(v, list) and v else v
		v = str(v)
		out[k] = v if len(v) <= limit else v[:limit] + '…'
	return out


def _record_call(request, response, uri, pars, elapsed_ms, meta=None, tool=None):
	"""One activity-log entry: what was called, by whom, what it changed, how it ended."""
	meta = meta or {}
	status = response.get('statusCode')
	entry = {
		'ts': time.time(),
		'time': time.strftime('%H:%M:%S'),
		'method': request.get('method'),
		'uri': uri,
		'source': _request_source(request),
		'args': _args_summary(pars),
		'dataLen': len(request.get('data') or ''),
		'statusCode': status,
		'callbackElapsedMs': round(elapsed_ms, 2),
		'requestKeys': sorted(k for k in request.keys() if not str(k).startswith('_')),
	}
	if tool:
		entry['tool'] = tool
	if meta.get('undo'):
		entry['undo'] = meta['undo']
	if meta.get('touched'):
		entry['touched'] = meta['touched']
	if status and status >= 400:
		try:
			err = json.loads(response.get('data') or '{}')
			entry['code'] = err.get('code')
			entry['error'] = str(err.get('error', ''))[:240]
			if err.get('suggestions'):
				entry['suggestions'] = [str(s) for s in err['suggestions'][:5]]
		except (ValueError, TypeError):
			entry['error'] = str(response.get('data', ''))[:240]
	_log_request(entry)
	return entry


# ---------------------------------------------------------------------------
# Route handlers - one function per URI. Each receives (request, response, pars)
# and writes statusCode / content-type / data into response. webServerDAT is
# passed only where needed (health, run).
# ---------------------------------------------------------------------------

def _ok_json(response, data):
	response['statusCode'] = 200
	response['statusReason'] = 'OK'
	response['content-type'] = 'application/json'
	response['data'] = json.dumps(data)

def _ok_json_sorted(response, data):
	response['statusCode'] = 200
	response['statusReason'] = 'OK'
	response['content-type'] = 'application/json'
	response['data'] = json.dumps(data, sort_keys=True, indent=2)

def _ok_text(response, text):
	response['statusCode'] = 200
	response['statusReason'] = 'OK'
	response['content-type'] = 'text/plain'
	response['data'] = text


def _route_network(request, response, pars, **_):
	"""Nodes, wires, references and non-default pars of a COMP (default: the network open in the
	editor). recursive=true walks the subtree. limit/offset page the nodes (default limit 300),
	fields=name,opType,... trims node entries, overlaps=true lists overlapping node pairs,
	graph=true adds a topological order and cycle check over the wires."""
	comp = _resolve_comp(pars)
	recursive = _bool_param(pars, 'recursive')
	data = describe_network(comp, recursive)
	nodes = data['nodes']
	limit = int(_first_param(pars, 'limit', '300'))
	offset = int(_first_param(pars, 'offset', '0'))
	page = nodes[offset:offset + limit]
	shown = {n['path'] for n in page}
	data['totalNodes'] = len(nodes)
	data['offset'] = offset
	data['truncated'] = offset + len(page) < len(nodes)
	data['nodes'] = page
	if data['truncated'] or offset:
		data['wires'] = [w for w in data['wires'] if w['from'] in shown or w['to'] in shown]
		data['references'] = [r for r in data['references'] if r['from'] in shown]
	fields = _first_param(pars, 'fields')
	if fields:
		keep = {'path'} | {f.strip() for f in fields.split(',')}
		data['nodes'] = [{k: v for k, v in n.items() if k in keep} for n in data['nodes']]
	if _bool_param(pars, 'overlaps'):
		data['overlaps'] = _overlapping_pairs(comp or _get_current_network())
	if _bool_param(pars, 'graph'):
		names = [n['path'] for n in nodes]
		order, _, cycle, _ = _topo_layers(names, [(w['from'], w['to']) for w in data['wires']])
		data['graph'] = {'order': order, 'hasCycles': bool(cycle), 'cycleNodes': cycle}
	_ok_json_sorted(response, data)


def _overlapping_pairs(comp):
	"""Pairs of sibling nodes whose tiles overlap in the editor (network boxes and docked ops
	excluded -- they overlap by design)."""
	nodes = [n for n in _nodes_in_comp(comp) if n.OPType != 'annotateCOMP' and not getattr(n, 'dock', None)]
	pairs = []
	for i, a in enumerate(nodes):
		for b in nodes[i + 1:]:
			if _rects_overlap(a.nodeX, a.nodeY, a.nodeWidth, a.nodeHeight, b):
				pairs.append([a.path, b.path])
	return pairs


def _route_project(request, response, pars, **_):
	"""Project and UI state in one call: name, folder, fps, build, frame, perform mode, and every
	pane (type, owner COMP, view position/zoom, current/selected ops)."""
	panes = []
	for p in ui.panes:
		entry = {'name': p.name, 'type': p.type.name, 'id': p.id}
		owner = getattr(p, 'owner', None)
		if owner is not None:
			entry['owner'] = owner.path
			entry['selected'] = [n.path for n in owner.selectedChildren]
			cur = owner.currentChild
			if cur is not None:
				entry['current'] = cur.path
		for attr in ('x', 'y', 'zoom'):
			if hasattr(p, attr):
				entry[attr] = round(float(getattr(p, attr)), 2)
		panes.append(entry)
	_ok_json(response, {
		'name': project.name, 'folder': project.folder, 'saveVersion': project.saveVersion,
		'build': app.build, 'version': app.version, 'cookRate': project.cookRate, 'realTime': project.realTime,
		'frame': absTime.frame, 'seconds': round(absTime.seconds, 3), 'performMode': ui.performMode,
		'currentPane': ui.panes.current.name if ui.panes.current else None, 'panes': panes,
	})


def _route_network_mmd(request, response, pars, **_):
	comp = _resolve_comp(pars)
	recursive = _bool_param(pars, 'recursive')
	_ok_text(response, network_to_mermaid(comp, recursive))


def _route_selected(request, response, pars, **_):
	comp_path = _first_param(pars, 'path')
	comp = _resolve_op(comp_path) if comp_path else _get_current_network()
	_ok_json(response, [_node_summary(n) for n in comp.selectedChildren])


def _route_select(request, response, pars, **_):
	"""Set the TD UI's node selection (and optionally home the network editor on it) so an
	agent can direct the user's attention to specific nodes - the write-side mirror of /selected."""
	_require_write_method(request, '/select')
	paths_param = _first_param(pars, 'paths') or _first_param(pars, 'path')
	if not paths_param:
		raise ValueError("missing required 'path' or 'paths' query parameter")
	nodes = [_resolve_op(p.strip()) for p in paths_param.split(',') if p.strip()]
	parent = nodes[0].parent()
	for child in _nodes_in_comp(parent):
		if child.selected:
			child.selected = False
	for n in nodes:
		n.selected = True
	nodes[0].current = True
	if _bool_param(pars, 'home', True):
		pane = _get_network_editor_pane()
		if pane is not None:
			pane.owner = parent
			pane.homeSelected(zoom=_bool_param(pars, 'zoom', True))
	_ok_json(response, [_node_summary(n) for n in nodes])


def _route_insert(request, response, pars, **_):
	"""Splice a new node into an existing connection: create opType, wire the current source of
	dest's input into it, and wire it into dest - shifting dest and everything to its right to
	make room. Identifies the connection by dest 'path' + 'input' index (default 0)."""
	_require_write_method(request, '/insert')
	dest = _resolve_op(_first_param(pars, 'path'))
	op_type = _first_param(pars, 'opType')
	if not op_type:
		raise ValueError("missing required 'opType' query parameter")
	input_index = int(_first_param(pars, 'input', '0'))
	parent = dest.parent()
	connectors = dest.inputConnectors
	if input_index >= len(connectors):
		raise ValueError(f"input index {input_index} out of range for '{dest.path}' ({len(connectors)} input connectors)")
	conns = connectors[input_index].connections
	src = conns[0].owner if conns else None
	orig_x, orig_y = dest.nodeX, dest.nodeY
	shift = 200
	for sib in _nodes_in_comp(parent):
		if sib.nodeX >= orig_x:
			sib.nodeX += shift
	name = _first_param(pars, 'name')
	new_node = parent.create(op_type, name) if name else parent.create(op_type)
	new_node.viewer = True
	new_node.nodeX = orig_x
	new_node.nodeY = orig_y
	if src is not None:
		new_node.setInputs([src])
	current_inputs = []
	for conn in dest.inputConnectors:
		c = conn.connections
		current_inputs.append(c[0].owner if c else None)
	current_inputs[input_index] = new_node
	dest.setInputs(current_inputs)
	_ok_json(response, {
		'inserted': _node_summary(new_node),
		'source': src.path if src else None,
		'dest': dest.path,
		'input': input_index,
	})


def _route_reload(request, response, pars, **_):
	"""Hot-reload project Python modules. Uses the project's own App.ReloadModules() when it has
	one (haxlib); otherwise reloads every imported module whose file is inside the project folder.
	(This route file itself reloads on its own when its synced file changes.)"""
	app_comp = getattr(op, 'App', None)
	if app_comp is not None and hasattr(app_comp, 'ReloadModules'):
		reloaded, skipped = app_comp.ReloadModules()
		_ok_json(response, {'reloaded': reloaded, 'skipped': skipped, 'via': 'op.App.ReloadModules'})
		return
	import importlib
	root = os.path.realpath(project.folder)
	reloaded, skipped = [], []
	for name, mod in list(sys.modules.items()):
		path = getattr(mod, '__file__', None)
		if not path or not os.path.realpath(path).startswith(root):
			continue
		try:
			importlib.reload(mod)
			reloaded.append(name)
		except Exception as e:
			skipped.append(f'{name}: {type(e).__name__}: {e}')
	_ok_json(response, {'reloaded': sorted(reloaded), 'skipped': skipped, 'via': 'project modules'})


def _route_save(request, response, pars, **_):
	"""Save the current project (.toe) to disk via project.save() -- the Ctrl+S equivalent.
	Does not touch external .tox files unless 'saveExternalToxs' is set -- see /save-external-tox
	for saving one specific external-tox-backed COMP without saving the whole project."""
	_require_write_method(request, '/save')
	# Always pass an explicit path: project.save() with none can open a modal Save As dialog
	# (e.g. an untitled project), which would block TD's main thread inside this callback.
	path = _first_param(pars, 'path') or f'{project.folder}/{project.name}'
	save_external_toxs = _bool_param(pars, 'saveExternalToxs', False)
	saved = project.save(path, saveExternalToxs=save_external_toxs)
	_ok_json(response, {
		'saved': bool(saved),
		'path': path,
		'saveExternalToxs': save_external_toxs,
	})


def _route_save_external_tox(request, response, pars, **_):
	"""Save one COMP's contents out to its own referenced external .tox file via
	COMP.saveExternalTox() -- independent of /save and its 'saveExternalToxs' flag, and
	independent of whether the COMP is currently flagged dirty. Intended for authoring/maintenance
	work on a specific external-tox-backed COMP (e.g. re-exporting a packaged drop-in tool after
	a code change) without saving the whole project."""
	_require_write_method(request, '/save-external-tox')
	path = _first_param(pars, 'path')
	if not path:
		raise ValueError("missing required 'path' query parameter (a COMP referencing an external .tox)")
	comp = _resolve_op(path)
	if not comp.isCOMP:
		raise ValueError(f"'{comp.path}' is not a COMP")
	_ok_json(response, _save_comp_tox(comp, _bool_param(pars, 'recursive', False)))


def _save_comp_tox(comp, recursive=False):
	"""Save a COMP to its External .tox path; returns {path, externaltox, recursive, saved, [via, note]}."""
	externaltox = comp.par.externaltox.eval() if hasattr(comp.par, 'externaltox') else ''
	if not externaltox:
		raise ValueError(f"'{comp.path}' has no external .tox configured (externaltox is blank)")
	count = comp.saveExternalTox(recurse=recursive)
	result = {'path': comp.path, 'externaltox': externaltox, 'recursive': recursive, 'saved': count}
	enabled = getattr(comp.par, 'enableexternaltox', None)
	if not count and enabled is not None and not enabled.eval():
		# TD's saveExternalTox() skips a COMP whose "Enable External .tox" is off (it lives embedded
		# in the .toe) and returns 0. The path is set, so write the file directly.
		full = externaltox if os.path.isabs(externaltox) else os.path.join(project.folder, externaltox)
		comp.save(full, createFolders=True)
		result.update({'saved': 1, 'via': 'COMP.save',
			'note': "Enable External .tox is off on this COMP, so it's embedded in the .toe; wrote the file directly"})
	return result


def on_project_save(comp):
	"""execute_start's onProjectPostSave: with Save Tox With Project on, re-save this COMP to its
	External .tox so the tox never lags the project (Ctrl+S in the dev project)."""
	par = getattr(comp.par, 'Savetoxwithproject', None)
	if par is None or not par.eval() or not comp.par.externaltox.eval():
		return
	try:
		result = _save_comp_tox(comp)
		print(f"[td_http_api] saved {result['externaltox']} with the project")
	except Exception as e:
		print(f'[td_http_api] could not save {comp.par.externaltox.eval()} with the project: {e}')


def _route_export_release(request, response, pars, webServerDAT, **_):
	"""Save a clean drop-in copy of this bridge COMP (default build/TdHttpApi-v<VERSION>.tox): file
	syncs, the external .tox path (value and expression), the dev-only Save Tox With Project toggle
	and post-save hook, and per-session state are removed in the saved file only, then restored
	here. Used by scripts/release.py."""
	_require_write_method(request, '/export-release')
	comp = webServerDAT.parent()
	output = _first_param(pars, 'output') or f'build/TdHttpApi-v{VERSION}.tox'
	full = (output if os.path.isabs(output) else os.path.join(project.folder, output)).replace('\\', '/')
	save_tox = getattr(comp.par, 'Savetoxwithproject', None)
	save_tox = save_tox.eval() if save_tox is not None else None
	dev = _package_dir(comp) is not None
	# Before the clears below: it refreshes the agent prompt, which the clears then blank.
	setup_component(comp, dev=False)
	ext = getattr(comp.par, 'externaltox', None)
	ext_state = (ext.mode, ext.expr) if ext is not None else None
	if ext is not None and ext.expr:
		ext.expr = ''
	# (par, original value). Order matters: sync off before the file is cleared, and restored in
	# reverse so the file is set again before sync is turned back on.
	changed = []

	def clear(par, value):
		if par is not None and par.eval() != value:
			changed.append((par, par.val))
			par.val = value

	cleared = []
	for dat in comp.findChildren(type=DAT, depth=1):
		if getattr(dat.par, 'file', None) is not None and dat.par.file.eval():
			cleared.append(dat.name)
			clear(getattr(dat.par, 'syncfile', None), False)
			clear(getattr(dat.par, 'loadonstart', None), False)
			clear(dat.par.file, '')
	clear(ext, '')
	clear(getattr(comp.par, 'Activeport', None), 0)
	clear(getattr(comp.par, 'Agentprompt', None), '(server not running)')
	clear(getattr(comp.par, 'Version', None), VERSION)
	# Per-session state that would otherwise ship: the viewer image (this app's port and label) and
	# the restart-pending stamp in COMP storage.
	pending = comp.fetch('_tdapi_restart_pending', None, search=False)
	comp.unstore('_tdapi_restart_pending')
	_update_viewer(comp, 0, '')
	try:
		comp.save(full, createFolders=True)
	finally:
		if pending is not None:
			comp.store('_tdapi_restart_pending', pending)
		for par, value in reversed(changed):
			if par.name != 'Version':
				par.val = value
		if ext_state is not None and ext_state[1]:
			ext.expr = ext_state[1]
			ext.mode = ext_state[0]	# setting expr switches the mode; keep the original
		setup_component(comp, dev=dev)
		if save_tox is not None and getattr(comp.par, 'Savetoxwithproject', None) is not None:
			comp.par.Savetoxwithproject = save_tox
	_ok_json(response, {
		'output': full,
		'version': VERSION,
		'bytes': os.path.getsize(full) if os.path.isfile(full) else 0,
		'unsyncedDATs': cleared,
	})


def _route_health(request, response, pars, webServerDAT, **_):
	node_paths = _first_param(pars, 'nodes')
	stats = []
	if node_paths:
		for p in node_paths.split(','):
			p = p.strip()
			n = op(p)
			if n is not None:
				stats.append({
					'path': n.path,
					'opType': n.opType,
					'cpuCookTime': n.cpuCookTime,
					'gpuCookTime': n.gpuCookTime,
					'totalCooks': n.totalCooks,
					'cookedThisFrame': n.cookedThisFrame,
					'cookedPreviousFrame': n.cookedPreviousFrame,
				})
	_ok_json(response, {
		'cookRate': project.cookRate,
		'realTime': project.realTime,
		'webServerCpuCookTime': webServerDAT.cpuCookTime,
		'webServerTotalCooks': webServerDAT.totalCooks,
		'nodes': stats,
	})


def _route_root(request, response, pars, webServerDAT, **_):
	"""Root endpoint - greet a brand-new agent and guide it through the onboarding sequence."""
	port = webServerDAT.par.port.eval()
	onboarding = f"""# TD HTTP API - Welcome

You are connected to a live TouchDesigner project over its HTTP bridge (this server, port {port}).

## Getting Started (Recommended Order)

1. **Read the Operating Guide:**
   ```
   GET http://127.0.0.1:{port}/docs
   ```
   Covers the full HTTP route reference, client-side workflow lessons, and how to work idiomatically with this bridge.

2. **Inspect the Current Network:**
   ```
   GET http://127.0.0.1:{port}/network
   ```
   Understand what nodes, wires, and references exist in the actively displayed network.

3. **Discover Available Routes:**
   ```
   GET http://127.0.0.1:{port}/routes
   ```
   See every operation this server supports with one-line summaries.

4. **Validate Your Request Schema:**
   ```
   GET http://127.0.0.1:{port}/schema
   ```
   Machine-readable contracts for every route (methods, params, examples).

## Quick Links

- **Which app is this?** `GET /server-info` (label, project, port). Several TD apps may each run a
  bridge on ports 3031-3040.
- **Short orientation prompt:** `GET /init`
- **Build many nodes at once:** `POST /build` (JSON: ops, params, connections, auto layout)
- **Parameters:** `/par` (single or JSON batch set), `/pulse`, `/parsearch`, `/custompars`, `/opinfo`
- **Edit code:** `POST /dat?mode=edit` with JSON `{{"old": "...", "new": "..."}}`
- **See output:** `/snapshot` (any op; non-TOPs return a `/job`), `/sample` (pixel stats), `/chop`
- **TouchDesigner docs:** `GET /help?q=feedback` (search), `/help?op=noiseTOP&par=period` (one
  parameter), `/help?class=OP&section=Methods/General`. TD's own offline docs for this build.
- **Official example networks:** `GET /examples?q=...` (OP Snippets) or `GET /examples.tsv`
- **Health Status:** `GET /health`
- **Request Logs:** `GET /logs`
- **Network Mermaid Diagram:** `GET /network.mmd`
- **Run Arbitrary Python:** `POST /run` (advanced; read `/docs` first)
- **MCP:** the same routes as `td_*` tools at `POST /mcp`

Errors are JSON `{{error, code, ...}}` and usually carry `suggestions` or `validValues`. Every change
is one named undo step for the user.

---

**Pro Tip:** Start with `/docs`. It has everything you need to work effectively. Don't skip it.
"""
	_ok_text(response, onboarding)


def _route_server_info(request, response, pars, webServerDAT, **_):
	"""Identify the running bridge itself: the Web Server DAT, its Callbacks DAT, and whether
	that DAT is file-synced (edit-the-file-directly) or an embedded snapshot (needs /dat pushes
	to update route code). Generalizes the "find the webserver + callbacks DAT" /run probe that
	otherwise gets rewritten by hand every time this question comes up."""
	cb = webServerDAT.par.callbacks.eval()
	comp = webServerDAT.parent()
	first = getattr(comp.par, 'Port', None)
	count = getattr(comp.par, 'Portrange', None)
	info = {
		# Identity first: agents scanning the port range use these to pick the right app.
		'label': _comp_label(comp),
		'project': project.name,
		'projectFolder': project.folder,
		'build': app.build,
		'portRange': [first.eval(), first.eval() + count.eval() - 1] if first is not None and count is not None else None,
		'webserver': webServerDAT.path,
		'port': webServerDAT.par.port.eval(),
		'active': webServerDAT.par.active.eval(),
		'callbacksDAT': cb.path if cb else None,
		'callbacksFile': (cb.par.file.eval() if cb is not None and hasattr(cb.par, 'file') else None),
		'callbacksSyncFile': (cb.par.syncfile.eval() if cb is not None and hasattr(cb.par, 'syncfile') else None),
	}
	info['version'] = VERSION
	info['environment'], info['warnings'] = _environment()
	_ok_json(response, info)


# ---------------------------------------------------------------------------
# Local install + per-build cache. Anything derived from the user's TD install (the OP Snippets
# catalog, later the /help docs index) is built on first use and cached per TD build under the
# user's TD preferences folder -- never committed, never written into the user's project.
# ---------------------------------------------------------------------------

def _cache_dir():
	return f'{_cache_root()}/{app.build}'


def _cache_root():
	return f'{app.preferencesFolder}/TdHttpApi/cache'.replace('\\', '/')


# What each per-build cache file is, for /cache (and the skill's "The per-machine cache" section).
_CACHE_FILES = {
	'op_snippets_catalog.tsv': ('OP Snippets catalog behind /examples: one row per official example network '
		'(relpath, family, optype, label, topic, text), copied from the install\'s OPSnippetsOnDemand.tox. '
		'Built on the first /examples call on this TD build (~2 s).'),
	'help_index.json.gz': ('Search index behind /help: every offline help page as text sections (gzip, one '
		'JSON line per page). Built after the first /help call on this TD build (~40 s, a slice per frame); '
		'loaded on the first /help call of each later session (~2 s).'),
	'help_index.json.gz.tmp': 'The /help index while it is being built; renamed to help_index.json.gz when done.',
}


def _route_cache(request, response, pars, **_):
	"""The bridge's per-machine cache (OP Snippets catalog, docs index), one folder per TD build under
	the user's TD preferences folder. GET lists it; POST clear=true deletes this build's folder
	(all=true: every build). Everything in it is rebuilt from the TD install on next use."""
	root = _cache_root()
	if _bool_param(pars, 'clear', False):
		_require_write_method(request, '/cache?clear=true')
		targets = [os.path.join(root, d) for d in os.listdir(root)] if _bool_param(pars, 'all', False) and \
			os.path.isdir(root) else [_cache_dir()]
		removed = []
		for t in targets:
			t = t.replace('\\', '/')
			if os.path.isdir(t) and os.path.dirname(t) == root:	# never delete outside the cache root
				shutil.rmtree(t)
				removed.append(t)
		global _SNIPPETS
		_SNIPPETS = None
		state = getattr(sys, '_tdapi_help_state', None)
		if state is not None and state['status'] not in ('building', 'loading'):
			del sys._tdapi_help_state
		_ok_json(response, {'removed': removed})
		return
	builds = []
	if os.path.isdir(root):
		for d in sorted(os.listdir(root)):
			folder = os.path.join(root, d)
			if os.path.isdir(folder):
				files = [{'name': f, 'bytes': os.path.getsize(os.path.join(folder, f)), 'about': _CACHE_FILES.get(f, '')}
					for f in sorted(os.listdir(folder))]
				builds.append({'build': d, 'current': d == str(app.build), 'files': files,
					'bytes': sum(f['bytes'] for f in files)})
	_ok_json(response, {'root': root, 'currentBuild': app.build, 'builds': builds})


def _snippets_loader_path():
	return f'{app.samplesFolder}/Learn/OPSnippets/OPSnippetsOnDemand.tox'.replace('\\', '/')


def _offline_help_root():
	return f'{app.samplesFolder}/Learn/OfflineHelp/https.docs.derivative.ca'.replace('\\', '/')


def _par_help_path():
	config = getattr(app, 'configFolder', None) or os.path.join(app.installFolder, 'Config')
	return os.path.join(config, 'TDParameterHelp.json').replace('\\', '/')


def _environment():
	"""(environment dict, warnings list): what this bridge relies on in the local TD install."""
	def entry(path, kind='file'):
		ok = os.path.isfile(path) if kind == 'file' else os.path.isdir(path)
		return {'path': path, 'exists': ok}

	env = {
		'os': sys.platform,
		'python': sys.version.split()[0],
		'tdVersion': app.version,
		'tdBuild': app.build,
		'samplesFolder': entry(app.samplesFolder, 'dir'),
		'opSnippets': entry(_snippets_loader_path()),
		'offlineHelp': entry(_offline_help_root(), 'dir'),
		'parameterHelp': entry(_par_help_path()),
		'cacheDir': entry(_cache_dir(), 'dir'),
	}
	warnings = []
	if not env['opSnippets']['exists']:
		warnings.append('OP Snippets not found in this TD install: /examples is unavailable')
	if not env['parameterHelp']['exists']:
		warnings.append("TDParameterHelp.json not found: /opinfo and /build can't check parameter names")
	if not env['offlineHelp']['exists']:
		warnings.append("TD's offline help isn't installed: /help (TouchDesigner docs) is unavailable")
	else:
		state = getattr(sys, '_tdapi_help_state', None)
		env['helpIndex'] = {'status': state['status'] if state and state.get('build') == app.build else 'idle',
			'cacheFile': f'{_cache_dir()}/help_index.json.gz'}
	return env, warnings


def _route_init(request, response, pars, webServerDAT, **_):
	"""The agent-init prompt (the bridge COMP's prompt_agent_init DAT) with this instance's port
	filled in, so the one-line Agent Prompt par can stay short."""
	_ok_text(response, init_prompt_text(webServerDAT.parent(), webServerDAT.par.port.eval()))


def _route_smoketest(request, response, pars, webServerDAT, **_):
	"""Run a lightweight functionality smoketest for this running server instance.

	Default mode is read-only and non-destructive. Optional `writeTest=true` performs a
	create/destroy probe and therefore requires POST/PUT.
	"""
	write_test = _bool_param(pars, 'writeTest', False)
	if write_test:
		_require_write_method(request, '/smoketest?writeTest=true')
	checks = []

	def record(name, ok, detail):
		checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

	# Route registration sanity for critical endpoints.
	required_routes = (
		'/network', '/errors', '/docs', '/routes', '/schema',
		'/examples', '/examples.tsv', '/examples-refresh', '/flag', '/par',
	)
	missing = [r for r in required_routes if r not in _ROUTES]
	record('routes_registered', len(missing) == 0, {'missing': missing})

	# Embedded docs presence (drop-in tox should include these).
	parent = webServerDAT.parent()
	doc_nodes = [n.name for n in _nodes_in_comp(parent) if n.isDAT and n.name.startswith('docs_')]
	record('embedded_docs_present', len(doc_nodes) >= 1, {'docs': sorted(doc_nodes)})

	# OP Snippets catalog: already cached for this TD build, or buildable from the install.
	cache = _snippets_cache_path()
	record('examples_catalog', os.path.isfile(cache) or os.path.isfile(_snippets_loader_path()), {
		'cached': os.path.isfile(cache),
		'cacheFile': cache,
		'loader': _snippets_loader_path(),
	})

	# TouchDesigner docs (/help) available from this install's offline help.
	help_root = _offline_help_root()
	record('help_docs', os.path.isdir(help_root), {'root': help_root})

	# Schema coverage sanity for critical documented routes.
	missing_schema = [r for r in ('/examples', '/examples.tsv', '/examples-refresh') if r not in _SCHEMA_OVERRIDES]
	record('schema_overrides_present', len(missing_schema) == 0, {'missing': missing_schema})

	# Basic runtime state sanity.
	record('runtime_state', project.realTime and project.cookRate > 0, {
		'realTime': project.realTime,
		'cookRate': project.cookRate,
	})

	# Optional write probe (create then destroy a temp node in the current network).
	if write_test:
		probe_name = '_smoketest_probe_null'
		probe_path = None
		try:
			comp = _get_current_network()
			probe = comp.op(probe_name)
			if probe is not None:
				probe.destroy()
			created = comp.create('nullCOMP', probe_name)
			probe_path = created.path
			created.destroy()
			record('write_probe_create_destroy', True, {'path': probe_path})
		except Exception as e:
			record('write_probe_create_destroy', False, {'path': probe_path, 'error': str(e)})

	passed = all(c['ok'] for c in checks)
	_ok_json(response, {
		'passed': passed,
		'checkCount': len(checks),
		'writeTest': write_test,
		'checks': checks,
	})


_DOCS_PREAMBLE = """# TD HTTP API - Agent Onboarding

You are connected to a live TouchDesigner project over its HTTP bridge (this server, port {port}).
Everything below is the operating documentation for working with it - read all of it before making
any changes to the project. It covers the full HTTP route reference, client-side workflow lessons,
and idiomatic TouchDesigner network-building technique.

Included docs (in order below): {names}

Fetch a single doc instead of all of them with `GET /docs?name=<docname>` (e.g. `td-http-api`; the
`docs_skill_` prefix is optional), or list available doc names with `GET /docs?list=true`.

For a live, code-accurate reference of every route this server currently supports (method + one-line
summary, generated straight from the running handlers rather than this prose), see `GET /routes`.
For a machine-readable contract of route methods/params/examples that agents can validate against,
see `GET /schema`.
"""


def _route_docs(request, response, pars, webServerDAT, **_):
	"""Serve the skill docs embedded alongside this server as sibling 'docs_*' Text DATs (per the
	packaged TdHttpApi.tox) - no filesystem access needed, works identically whether or not the
	project is file-synced. Intended as a one-shot onboarding URL: a brand-new agent hitting
	GET /docs with zero prior context gets everything it needs to start operating this bridge."""
	parent = webServerDAT.parent()
	all_docs = sorted(
		(n for n in _nodes_in_comp(parent) if n.isDAT and n.name.startswith('docs_')),
		key=lambda n: n.name,
	)
	if not all_docs:
		raise ValueError(
			f"no 'docs_*' DATs found alongside the Web Server DAT at '{parent.path}' - this route "
			"needs the packaged TdHttpApi.tox's embedded skill docs (missing in a from-scratch manual setup)"
		)
	if _bool_param(pars, 'list'):
		_ok_json(response, {'docs': [n.name for n in all_docs]})
		return
	name_filter = _first_param(pars, 'name')
	if name_filter:
		# Op names can't contain hyphens (TD forces docs_td-network-craft -> docs_td_network_craft),
		# so treat '-' and '_' as equivalent when matching a requested doc name against a DAT name.
		normalized_filter = name_filter.replace('-', '_')
		matches = [
			n for n in all_docs
			if n.name in (normalized_filter, f'docs_{normalized_filter}', f'docs_skill_{normalized_filter}')
		]
		if not matches:
			raise ValueError(f"no doc named '{name_filter}'. available: {', '.join(n.name for n in all_docs)}")
		docs_to_serve = matches
	else:
		docs_to_serve = all_docs
	sections = []
	if not name_filter:
		sections.append(_DOCS_PREAMBLE.format(
			port=webServerDAT.par.port.eval(),
			names=', '.join(n.name for n in all_docs),
		))
	for n in docs_to_serve:
		text = n.csv if n.isTable else n.text
		sections.append(f"<!-- ===== {n.name} ===== -->\n\n{text}")
	_ok_text(response, '\n\n---\n\n'.join(sections))


# ---------------------------------------------------------------------------
# OP Snippets catalog (/examples): Derivative's curated example networks, one row each. Read from
# the snippetsChooser/allAlphaNumeric table inside the install's OPSnippetsOnDemand.tox (~2 s to
# load), then cached per TD build (see _cache_dir) and in memory.
# ---------------------------------------------------------------------------

_SNIPPET_COLUMNS = ('relpath', 'family', 'optype', 'label', 'topic', 'text')
_SNIPPETS = None	# {'build', 'header', 'records'}


def _snippets_cache_path():
	return f'{_cache_dir()}/op_snippets_catalog.tsv'


def _build_snippets_catalog():
	"""Load the install's OP Snippets browser into a throwaway COMP, copy its catalog table, write
	the per-build cache. Returns (header, records)."""
	loader = _snippets_loader_path()
	if not os.path.isfile(loader):
		raise BridgeError("OP Snippets aren't in this TD install, so there's no examples catalog",
			code='snippets_unavailable', status=503, loader=loader)
	probe = op('/').create(baseCOMP, '_tdapi_snippets_probe')
	try:
		probe.loadTox(loader)
		root = probe.children[0] if probe.children else None
		table = root.op('snippetsChooser/allAlphaNumeric') if root is not None else None
		if table is None:
			raise BridgeError('no catalog table (snippetsChooser/allAlphaNumeric) in OPSnippetsOnDemand.tox',
				code='snippets_unavailable', status=503, loader=loader)
		cols = [c.val for c in table.row(0)]
		header = [c for c in _SNIPPET_COLUMNS if c in cols]
		idx = [cols.index(c) for c in header]
		# Collapse whitespace so every example stays one greppable line.
		records = [{cols[i]: ' '.join(table[r, i].val.split()) for i in idx} for r in range(1, table.numRows)]
	finally:
		probe.destroy()
	path = _snippets_cache_path()
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, 'w', encoding='utf-8', newline='') as f:
		f.write(_examples_tsv_text(records, header))
	return header, records


def _snippets_catalog(rebuild=False):
	"""(header, records, source) for the OP Snippets catalog; source is memory, cache or built."""
	global _SNIPPETS
	if not rebuild and _SNIPPETS is not None and _SNIPPETS['build'] == app.build:
		return _SNIPPETS['header'], _SNIPPETS['records'], 'memory'
	path = _snippets_cache_path()
	if not rebuild and os.path.isfile(path):
		with open(path, encoding='utf-8', newline='') as f:
			rows = list(csv.reader(f, delimiter='\t'))
		header = rows[0] if rows else []
		records = [dict(zip(header, r + [''] * (len(header) - len(r)))) for r in rows[1:]]
		source = 'cache'
	else:
		header, records = _build_snippets_catalog()
		source = 'built'
	_SNIPPETS = {'build': app.build, 'header': header, 'records': records}
	return header, records, source


def _filter_examples_records(records, pars):
	"""Apply optional text filter (`q`) over all record values."""
	query = (_first_param(pars, 'q') or '').strip().lower()
	if not query:
		return records
	return [rec for rec in records if query in ' '.join(str(v) for v in rec.values()).lower()]


def _route_examples(request, response, pars, **_):
	"""Search the OP Snippets catalog: Derivative's curated example networks, one row per example
	(relpath, family, optype, label, topic, text). Built from the local TD install on first use
	(~2 s), then cached per TD build."""
	header, records, source = _snippets_catalog()
	records = _filter_examples_records(records, pars)
	offset = int(_first_param(pars, 'offset', '0'))
	limit = int(_first_param(pars, 'limit', '50'))
	if offset < 0:
		offset = 0
	if limit < 1:
		limit = 1
	if limit > 500:
		limit = 500
	paged = records[offset:offset + limit]
	_ok_json(response, {
		'source': source,
		'cacheFile': _snippets_cache_path(),
		'tdBuild': app.build,
		'columns': header,
		'total': len(records),
		'offset': offset,
		'limit': limit,
		'returned': len(paged),
		'rows': paged,
	})


def _examples_tsv_text(records, header):
	buf = io.StringIO()
	writer = csv.writer(buf, delimiter='\t', lineterminator='\n')
	writer.writerow(header)
	for rec in records:
		writer.writerow([rec.get(col, '') for col in header])
	return buf.getvalue()


def _route_examples_tsv(request, response, pars, **_):
	"""The OP Snippets catalog as TSV (optionally filtered by `q`), for grepping outside TD."""
	header, records, _source = _snippets_catalog()
	records = _filter_examples_records(records, pars)
	response['statusCode'] = 200
	response['statusReason'] = 'OK'
	response['content-type'] = 'text/tab-separated-values; charset=utf-8'
	response['data'] = _examples_tsv_text(records, header)


def _route_examples_refresh(request, response, pars, **_):
	"""Rebuild the OP Snippets catalog from this TD install, replacing the per-build cache.
	Optional `output` also writes a copy of the TSV there (relative to the project folder)."""
	_require_write_method(request, '/examples-refresh')
	header, records, _source = _snippets_catalog(rebuild=True)
	result = {'cacheFile': _snippets_cache_path(), 'tdBuild': app.build, 'rows': len(records),
		'columns': len(header)}
	output = _first_param(pars, 'output')
	if output:
		if not os.path.isabs(output):
			output = os.path.join(project.folder, output)
		output = output.replace('\\', '/')
		os.makedirs(os.path.dirname(output), exist_ok=True)
		with open(output, 'w', encoding='utf-8', newline='') as f:
			f.write(_examples_tsv_text(_filter_examples_records(records, pars), header))
		result['output'] = output
	_ok_json(response, result)


# ---------------------------------------------------------------------------
# /help: TouchDesigner's own offline docs (Samples/Learn/OfflineHelp, ~2,100 pages that ship with
# every install and match its build). Reading a page parses that one file on demand. Search uses an
# index built (or loaded from the per-build cache in _cache_dir()) on TD's main thread a few
# milliseconds per frame via run(). NOT a Python thread: a background indexing thread deadlocked TD
# (main thread and the worker both stuck, 0% CPU) on Nov 6, 2026. Index state lives on `sys` so a
# module reload doesn't start a second build.
# ---------------------------------------------------------------------------

import difflib
import gzip
import html as _html
from html.parser import HTMLParser

_HELP_SKIP_TAGS = {'script', 'style', 'img', 'input', 'label', 'noscript'}
_HELP_BLOCK_TAGS = {'p', 'div', 'blockquote', 'dd', 'dt', 'table', 'ul', 'ol', 'dl', 'center'}
_HELP_VOID_TAGS = {'br', 'img', 'input', 'hr', 'meta', 'link', 'wbr'}
_HELP_SECTION_CAP = 30000
_HELP_URL = 'https://docs.derivative.ca/'
_HELP_FRAME_BUDGET = 0.006	# seconds of main-thread indexing work per frame
_HELP_INDEX_FORMAT = 1


class _HelpHTML(HTMLParser):
	"""MediaWiki page body -> [[level, heading, parts]] sections of compact Markdown-ish text."""

	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.sections = [[1, 'Summary', []]]
		self.skip = 0	# depth inside skipped elements (toc, edit links, toggles, ...)
		self.stack = []	# per open tag: True if it started a skipped element
		self.heading = None	# [level, parts] while inside h1-h5
		self.pre = 0
		self.lists = 0

	def _out(self, s):
		(self.heading[1] if self.heading is not None else self.sections[-1][2]).append(s)

	def handle_starttag(self, tag, attrs):
		if tag in _HELP_VOID_TAGS:
			if tag == 'br' and not self.skip:
				self._out('\n')
			return
		a = dict(attrs)
		cls = a.get('class') or ''
		skipping = (tag in _HELP_SKIP_TAGS or a.get('id') == 'toc' or 'mw-editsection' in cls
			or 'mw-customtoggle' in cls or 'printfooter' in cls or 'catlinks' in cls)
		self.stack.append(skipping)
		if skipping:
			self.skip += 1
			return
		if self.skip:
			return
		if tag in ('h1', 'h2', 'h3', 'h4', 'h5'):
			self.heading = [int(tag[1]), []]
		elif tag == 'pre':
			self.pre += 1
			self._out('\n```\n')
		elif tag == 'code' and not self.pre:
			self._out('`')
		elif tag in ('b', 'strong'):
			self._out('**')
		elif tag in ('ul', 'ol'):
			self.lists += 1
			self._out('\n')
		elif tag == 'li':
			self._out('\n' + '  ' * max(self.lists - 1, 0) + '- ')
		elif tag == 'tr':
			self._out('\n|')
		elif tag in ('td', 'th'):
			self._out(' ')
		elif tag in _HELP_BLOCK_TAGS:
			self._out('\n')

	def handle_endtag(self, tag):
		if tag in _HELP_VOID_TAGS or not self.stack:
			return
		if self.stack.pop():
			self.skip -= 1
			return
		if self.skip:
			return
		if tag in ('h1', 'h2', 'h3', 'h4', 'h5') and self.heading is not None:
			level, parts = self.heading
			self.heading = None
			title = ' '.join(''.join(parts).split()).strip('# ')
			if title:
				self.sections.append([level, title, []])
		elif tag == 'pre':
			self.pre -= 1
			self._out('\n```\n')
		elif tag == 'code' and not self.pre:
			self._out('`')
		elif tag in ('b', 'strong'):
			self._out('**')
		elif tag in ('ul', 'ol'):
			self.lists -= 1
			self._out('\n')
		elif tag in ('td', 'th'):
			self._out(' |')
		elif tag in _HELP_BLOCK_TAGS:
			self._out('\n')

	def handle_data(self, data):
		if not self.skip:
			self._out(data if self.pre else re.sub(r'\s+', ' ', data))


def _help_tidy(text):
	out, in_code = [], False
	for line in text.split('\n'):
		if line.strip() == '```':
			in_code = not in_code
			out.append('```')
			continue
		if in_code:
			out.append(line.rstrip())
			continue
		line = line.strip().replace('** **', ' ').replace('``', '')
		if line not in ('-', '|', '**', '- **'):
			out.append(line)
	text = '\n'.join(out).replace(' -  - ', ' - ')
	text = re.sub(r'\n{3,}', '\n\n', text)
	text = re.sub(r'\n\n(?=\s*- )', '\n', text)	# MediaWiki wraps each <li> in its own <ul>
	text = re.sub(r'```\n+```\n?', '', text)
	return text.strip()


def _help_parse_page(raw):
	"""(title, [[level, heading, text]]) for one offline help page's HTML."""
	m = re.search(r'<title>(.*?)</title>', raw, re.S)
	title = _html.unescape(m.group(1)).replace(' - TouchDesigner Documentation', '').strip() if m else ''
	start = raw.find('<div class="mw-parser-output">')
	end = raw.find('class="printfooter"', start)
	p = _HelpHTML()
	p.feed(raw[start:end if end > 0 else None] if start >= 0 else raw)
	p.close()
	sections = []
	for level, heading, parts in p.sections:
		text = _help_tidy(''.join(parts))
		if text or level > 1:
			sections.append([level, heading, text])
	return title, sections


def _help_norm(s):
	return re.sub(r'[^a-z0-9]', '', s.lower())


def _help_names(root):
	"""{normalized name: page name} for every page in the offline help folder (cached per root)."""
	cache = getattr(sys, '_tdapi_help_names', None)
	if cache is None or cache[0] != root:
		names = {}
		if os.path.isdir(root):
			for f in os.listdir(root):
				if f.endswith('.htm'):
					names.setdefault(_help_norm(f[:-4]), f[:-4])
		cache = (root, names)
		sys._tdapi_help_names = cache
	return cache[1]


def _help_resolve(name, prefer_class=False):
	"""Page name for a loose reference ('noiseTOP', 'Noise TOP', 'OP' -> 'OP_Class', 'td.OP'), or
	raises BridgeError help_page_not_found with suggestions. prefer_class (the class= param) tries
	'<name>_Class' first: the offline help has stub pages like OP.htm and COMP.htm that would win."""
	names = _help_names(_offline_help_root())
	raw = re.sub(r'^td\.', '', (name or '').strip())
	key = _help_norm(raw.replace('.htm', ''))
	for k in ((key + 'class', key) if prefer_class else (key, key + 'class')):
		if k in names:
			return names[k]
	close = difflib.get_close_matches(key, list(names), n=6, cutoff=0.6)
	raise BridgeError(f"no help page '{name}'", code='help_page_not_found', status=404,
		suggestions=[names[k] for k in close] or None)


def _help_state():
	"""The shared index state for this TD build (survives module reloads)."""
	state = getattr(sys, '_tdapi_help_state', None)
	if state is None or state.get('build') != app.build:
		state = {'build': app.build, 'status': 'idle', 'done': 0, 'total': 0, 'pages': None, 'error': None}
		sys._tdapi_help_state = state
	return state


def _help_index_path():
	return f'{_cache_dir()}/help_index.json.gz'


def _help_start_index(rebuild=False):
	"""Start loading (from the per-build cache) or building the search index, a slice per frame.
	Idempotent: returns the shared state."""
	state = _help_state()
	if state['status'] in ('building', 'loading') or (state['status'] == 'ready' and not rebuild):
		return state
	root, cache = _offline_help_root(), _help_index_path()
	if not os.path.isdir(root):
		state.update(status='error', error=f'no offline help at {root}')
		return state
	state.update(done=0, total=0, pages={}, error=None, startedAt=time.time())
	if not rebuild and os.path.isfile(cache):
		try:
			fh = gzip.open(cache, 'rt', encoding='utf-8')
			header = json.loads(fh.readline())
			if header.get('build') == state['build'] and header.get('format') == _HELP_INDEX_FORMAT:
				state.update(status='loading', fh=fh, total=header.get('pages', 0))
				run(_help_step, delayFrames=1)
				return state
			fh.close()
		except (OSError, ValueError):
			pass	# unreadable or stale cache: rebuild it
	files = sorted(f for f in os.listdir(root) if f.endswith('.htm'))
	os.makedirs(os.path.dirname(cache), exist_ok=True)
	out = gzip.open(cache + '.tmp', 'wt', encoding='utf-8')
	out.write(json.dumps({'build': state['build'], 'format': _HELP_INDEX_FORMAT, 'pages': len(files)}) + '\n')
	state.update(status='building', files=files, root=root, out=out, total=len(files))
	run(_help_step, delayFrames=1)
	return state


def _help_step():
	"""One frame's slice of index loading/building (main thread, ~_HELP_FRAME_BUDGET), then reschedule."""
	state = getattr(sys, '_tdapi_help_state', None)
	if not state or state['status'] not in ('building', 'loading'):
		return
	deadline = time.perf_counter() + _HELP_FRAME_BUDGET
	try:
		if state['status'] == 'loading':
			fh = state['fh']
			while time.perf_counter() < deadline:
				line = fh.readline()
				if not line:
					fh.close()
					state.pop('fh', None)
					state.update(status='ready', source='cache')
					return
				entry = json.loads(line)
				state['pages'][entry['name']] = {'title': entry['title'], 'sections': entry['sections']}
				state['done'] += 1
		else:
			files, out = state['files'], state['out']
			while state['done'] < len(files) and time.perf_counter() < deadline:
				f = files[state['done']]
				state['done'] += 1
				try:
					with open(os.path.join(state['root'], f), encoding='utf-8-sig', errors='replace') as fh:
						title, sections = _help_parse_page(fh.read())
				except Exception:
					continue	# one bad page shouldn't sink the index
				state['pages'][f[:-4]] = {'title': title, 'sections': sections}
				out.write(json.dumps({'name': f[:-4], 'title': title, 'sections': sections}) + '\n')
			if state['done'] >= len(files):
				out.close()
				os.replace(_help_index_path() + '.tmp', _help_index_path())
				for k in ('out', 'files', 'root'):
					state.pop(k, None)
				state.update(status='ready', source='built')
				return
	except Exception as e:
		for k in ('fh', 'out'):
			h = state.pop(k, None)
			if h is not None:
				try:
					h.close()
				except Exception:
					pass
		state.update(status='error', error=f'{type(e).__name__}: {e}')
		return
	run(_help_step, delayFrames=1)


def _help_status(state=None):
	state = state or _help_state()
	out = {'status': state['status'], 'pages': len(_help_names(_offline_help_root())),
		'indexed': state['done'], 'total': state['total'], 'cacheFile': _help_index_path()}
	if state['status'] == 'building' and state['total']:
		elapsed = time.time() - state.get('startedAt', time.time())
		rate = state['done'] / elapsed if elapsed > 0 and state['done'] else 0
		out['retryAfter'] = max(1, round((state['total'] - state['done']) / rate)) if rate else 10
	if state.get('error'):
		out['error'] = state['error']
	return out


def _help_snippet(text, terms, width=220):
	low = text.lower()
	hits = [low.find(t) for t in terms if low.find(t) >= 0]
	at = min(hits) if hits else 0
	start = max(0, at - width // 3)
	snip = ' '.join(text[start:start + width].split())
	return ('…' if start else '') + snip + ('…' if start + width < len(text) else '')


def _help_stem(word):
	"""Crude suffix strip so instancing/instance, callbacks/callback match each other."""
	if len(word) > 4:
		for suffix in ('ing', 'ed', 'es', 'e', 's'):
			if word.endswith(suffix) and len(word) - len(suffix) >= 4:
				return word[:-len(suffix)]
	return word


def _help_search(query, limit):
	"""Rank pages for a query. Scoring: exact page-name match, then whole-word title matches, then how
	many query terms a section covers and how often they start a word there. Results whose best
	section is identical text (inherited class members repeated on every *_Class page) are collapsed."""
	raw_terms = [t for t in re.findall(r'[a-z0-9_]+', query.lower()) if t]
	terms = list(dict.fromkeys(_help_stem(t) for t in raw_terms))
	qnorm = _help_norm(query)
	names = _help_names(_offline_help_root())
	state = _help_state()
	results = []
	if state['status'] == 'ready' and terms:
		patterns = {t: re.compile(r'(?<![a-z0-9])' + re.escape(t)) for t in terms}
		for name, page in state['pages'].items():
			title_words = ({_help_stem(w) for w in re.findall(r'[a-z0-9]+', page['title'].lower())}
				| {_help_stem(w) for w in name.lower().split('_')})
			nnorm = _help_norm(name)
			page_score = 1000 if nnorm in (qnorm, qnorm + 'class') else 0
			title_hits = sum(t in title_words for t in terms)
			page_score += 60 * title_hits
			best = None
			for idx, (level, heading, text) in enumerate(page['sections']):
				low = text.lower()
				head_low = heading.lower()
				covered = 0
				count = 0
				for t in terms:
					in_head = t in head_low
					n = min(len(patterns[t].findall(low)), 8) if t in low else 0
					if n or in_head or t in title_words:
						covered += 1
					count += n + 15 * in_head
				if not covered:
					continue
				score = page_score + count + 40 * covered * covered // len(terms)
				if best is None or score > best[0]:
					best = (score, heading, text, idx)
			if best is None and page_score:
				best = (page_score, page['sections'][0][1] if page['sections'] else '', '', 0)
			if best is not None:
				section = _help_section_paths(page['sections'])[best[3]] if page['sections'] else best[1]
				results.append({'page': name, 'title': page['title'], 'section': section, 'score': best[0],
					'snippet': _help_snippet(best[2], terms)})
		results.sort(key=lambda r: -r['score'])
		seen = {}
		collapsed = []
		for r in results:
			key = (r['section'], r['snippet'])
			if key in seen and r['snippet']:
				seen[key]['alsoIn'] = seen[key].get('alsoIn', 0) + 1
				continue
			seen[key] = r
			collapsed.append(r)
		results = collapsed
	else:
		# Index not ready: match page names only (instant).
		for k, name in names.items():
			if qnorm and (qnorm == k or qnorm + 'class' == k):
				results.append({'page': name, 'score': 1000})
			elif raw_terms and all(t in k for t in raw_terms):
				results.append({'page': name, 'score': 100 - len(k)})
		results.sort(key=lambda r: -r['score'])
	return results[:limit]


def _help_section_paths(sections):
	"""'Parent/Child' path per (level, heading, text) section, so a heading repeated under different
	parents (OP_Class has 'General' under both Members and Methods) can be addressed. Level-1 headings
	(the page title / Summary) aren't used as parents."""
	paths, chain = [], []
	for lv, h, t in sections:
		chain = [(l, n) for l, n in chain if l < lv]
		paths.append('/'.join([n for l, n in chain if l >= 2] + [h]))
		chain.append((lv, h))
	return paths


def _help_pick_sections(sections, paths, want):
	"""Indexes of the sections a section= value selects: exact path, else exact heading, else
	substring of the path, each extended with the deeper-level subsections that follow it.
	An empty want selects everything."""
	if not want:
		return list(range(len(sections)))
	lows = [p.lower() for p in paths]
	heads = [h.lower() for lv, h, t in sections]
	hits = [i for i, p in enumerate(lows) if p == want] or [i for i, h in enumerate(heads) if h == want] or \
		[i for i, p in enumerate(lows) if want in p]
	picked = []
	for i in hits:
		j = i + 1
		while j < len(sections) and sections[j][0] > sections[i][0]:
			j += 1
		picked.extend(k for k in range(i, j) if k not in picked)
	return picked


def _help_page(name, prefer_class=False):
	"""(page, title, sections) from the index when ready, else parsed from the file now."""
	page = _help_resolve(name, prefer_class)
	state = _help_state()
	if state['status'] == 'ready' and page in state['pages']:
		entry = state['pages'][page]
		return page, entry['title'], entry['sections']
	with open(os.path.join(_offline_help_root(), page + '.htm'), encoding='utf-8-sig', errors='replace') as f:
		title, sections = _help_parse_page(f.read())
	return page, title, sections


def _help_par_entry(sections, par):
	"""The paragraph documenting parameter `par` (its line plus any menu items under it)."""
	needle = f'`{par.lower()}`'
	for level, heading, text in sections:
		if not heading.lower().startswith('parameters'):
			continue
		lines = text.split('\n')
		for i, line in enumerate(lines):
			if needle in line.lower() and not line.lstrip().startswith('- '):
				out = [line]
				for nxt in lines[i + 1:]:
					if nxt.lstrip().startswith('- '):
						out.append(nxt)
					else:
						break
				return heading, '\n'.join(out)
	return None, None


def _route_help(request, response, pars, **_):
	"""TouchDesigner docs from this install's offline help: q= searches, page= (or op=, class=)
	reads a page (summary + section list), &section= one section, &par= one parameter, &full=true
	everything. No args: status. POST reindex=true rebuilds the search index."""
	root = _offline_help_root()
	if not os.path.isdir(root):
		raise BridgeError("TD's offline help isn't installed with this TD, so /help is unavailable",
			code='help_unavailable', status=503, path=root)
	if _bool_param(pars, 'reindex', False):
		_require_write_method(request, '/help?reindex=true')
		_ok_json(response, _help_status(_help_start_index(rebuild=True)))
		return
	query = _first_param(pars, 'q')
	name = _first_param(pars, 'page') or _first_param(pars, 'op') or _first_param(pars, 'class')
	if query:
		state = _help_start_index()
		limit = max(1, min(int(_first_param(pars, 'limit', '10')), 50))
		result = {'query': query, 'results': _help_search(query, limit), 'index': _help_status(state)}
		if state['status'] != 'ready':
			result['note'] = ('the full-text index is still building, so these are page-name matches only; '
				'retry after index.retryAfter seconds for full-text results')
		_ok_json(response, result)
		return
	if not name:
		state = _help_state()
		_ok_json(response, {'root': root, 'index': _help_status(state), 'usage': [
			'/help?q=feedback loop', '/help?page=Noise_TOP', '/help?op=noiseTOP&section=Parameters',
			'/help?op=noiseTOP&par=period', '/help?class=OP&section=Methods/General', '/help?page=Noise_TOP&full=true']})
		return
	_help_start_index()	# warm the index for later searches
	page, title, sections = _help_page(name, prefer_class=not _first_param(pars, 'page') and not _first_param(pars, 'op'))
	paths = _help_section_paths(sections)
	out ={'page': page, 'title': title, 'url': _HELP_URL + page,
		'sections': [dict({'heading': h, 'level': lv, 'chars': len(t)}, **({'path': p} if p != h else {}))
			for p, (lv, h, t) in zip(paths, sections)]}
	par = _first_param(pars, 'par')
	section = _first_param(pars, 'section')
	if par:
		heading, text = _help_par_entry(sections, par)
		if text is None:
			raise BridgeError(f"no parameter '{par}' documented on {page}", code='help_par_not_found', status=404,
				page=page)
		out.update(section=heading, text=text)
	elif section or _bool_param(pars, 'full', False):
		want = re.sub(r'\s*[/>]\s*', '/', (section or '').strip().lower())
		picked = _help_pick_sections(sections, paths, want)
		if not picked:
			raise BridgeError(f"no section '{section}' on {page}", code='help_section_not_found', status=404,
				validValues=paths, hint='a heading matches its subsections too; Parent/Child picks one nested heading')
		text = '\n\n'.join(f'## {paths[i]}\n{sections[i][2]}' for i in picked if sections[i][2] or len(picked) == 1)
		cap = _HELP_SECTION_CAP * (2 if not want else 1)
		if len(text) > cap:
			out['truncated'] = True
			out['hint'] = 'narrow it down with section=<one heading> or par=<parameter name>'
			text = text[:cap]
		out['text'] = text
	else:
		summary = sections[0][2] if sections else ''
		out['summary'] = summary[:3000]
		out['hint'] = 'add section=<heading> (substring ok, e.g. Parameters), par=<name>, or full=true'
	_ok_json(response, out)


def _infer_methods(uri, fn, _seen=None):
	"""Best-effort HTTP method(s) for a route, derived from the handler's compiled bytecode rather
	than a hand-maintained table that can drift (or inspect.getsource(), which isn't reliable here --
	TD's DAT-based module loading doesn't expose normal readable source, so every write route came
	back misclassified as GET-only until this switched to co_names/co_consts). Every write-only route
	in this file gates on the shared _require_write_method() helper (POST/PUT); /par and /dat are
	dual-mode (GET always works, POST/PUT optionally, checked inline against the literal 'POST'
	rather than via that helper, which usually ends up folded into a tuple constant rather than a
	bare top-level one -- hence the nested-tuple flattening below); /delete checks its own method
	inline too (POST/DELETE). A route like /bypass that simply delegates to another route's handler
	(_route_flag) has no write-gate of its own to find, so this recurses one level into any other
	known handler referenced by name, with a visited-set guard against cycles."""
	if uri == '/delete':
		return ['POST', 'DELETE']
	if uri == '/smoketest':
		return ['GET', 'POST', 'PUT']
	if '_require_write_method' in fn.__code__.co_names:
		return ['POST', 'PUT']
	flat_consts = set()
	for c in fn.__code__.co_consts:
		if isinstance(c, tuple):
			flat_consts.update(c)
		else:
			flat_consts.add(c)
	if 'POST' in flat_consts:
		return ['GET', 'POST', 'PUT']
	_seen = _seen or set()
	_seen.add(fn)
	for name in fn.__code__.co_names:
		if name.startswith('_route_') and name in globals():
			callee = globals()[name]
			if callable(callee) and callee not in _seen:
				callee_methods = _infer_methods(uri, callee, _seen)
				if callee_methods != ['GET']:
					return callee_methods
	return ['GET']


def _route_routes(request, response, pars, **_):
	"""Live, code-derived API reference: every currently-registered route, its HTTP method(s),
	and a one-line summary pulled from its handler's docstring. Generated straight from _ROUTES
	and the running handlers at request time, so it always matches this exact server instance --
	unlike the prose in /docs, which documents intent and can drift from the code over time."""
	entries = []
	for uri, fn in sorted(_ROUTES.items()):
		doc = inspect.getdoc(fn)
		summary = doc.splitlines()[0] if doc else ''
		entries.append({'uri': uri, 'methods': _infer_methods(uri, fn), 'summary': summary})
	_ok_json(response, entries)


# Every one of these is a plain Python attribute on OP ("Common Flags" in TD's own docs) -
# never a Par, so /par can never reach them. Keep this above schema overrides so enum generation
# works at module import time.
_KNOWN_OP_FLAGS = frozenset((
	'activeViewer', 'allowCooking', 'bypass', 'cloneImmune', 'current', 'display',
	'expose', 'lock', 'python', 'render', 'selected', 'showCustomOnly', 'showDocked',
	'viewer',
))


_SCHEMA_OVERRIDES = {
	'/flag': {
		'query': {
			'name': {
				'type': 'string',
				'required': True,
				'enum': sorted(_KNOWN_OP_FLAGS),
			},
			'value': {
				'type': 'boolean',
				'required': False,
				'default': True,
			},
			'path': {'type': 'string', 'required': False},
			'paths': {'type': 'csv<string>', 'required': False},
			'family': {'type': 'string', 'required': False},
			'recursive': {'type': 'boolean', 'required': False, 'default': False},
		},
		'response': {'type': 'array<object>', 'shape': {'path': 'string', '<flagName>': 'boolean'}},
		'examples': [
			{
				'method': 'POST',
				'query': 'path=/project1/TESTING&family=TOP&name=bypass&value=true',
				'description': 'Bypass all TOPs in a network root.',
			},
		],
	},
	'/par': {
		'query': {
			'path': {'type': 'string', 'required': True},
			'par': {'type': 'string', 'required': True},
			'value': {'type': 'string', 'required': False},
			'expr': {'type': 'string', 'required': False},
			'reset': {'type': 'boolean', 'required': False},
			'unbind': {'type': 'boolean', 'required': False},
			'paths': {'type': 'csv<string>', 'required': False},
			'pars': {'type': 'csv<string>', 'required': False},
			'includeMenus': {'type': 'boolean', 'required': False},
		},
		'rules': [
			"GET reads parameter state; batch read with paths=a,b&pars=t*,file.",
			"POST/PUT need 'value', 'expr', 'reset=true' or 'unbind=true' -- or a JSON batch body "
			"{\"set\": {\"<path>\": {\"<par>\": value | [tuplet] | {\"expr\"|\"bindExpr\"|\"reset\"|\"unbind\"|\"pulse\"}}}}.",
			"Strict menus accept a name, a label or an index; anything else returns invalid_menu_value with validValues.",
		],
		'response': {'type': 'object', 'shape': {'name': 'string', 'mode': 'string', 'value': 'any'}},
		'examples': [
			{
				'method': 'POST',
				'query': 'path=/project1/geo1&par=tx&value=1.5',
				'description': 'Set a numeric parameter value.',
			},
		],
	},
	'/dat': {
		'query': {
			'path': {'type': 'string', 'required': True},
			'mode': {'type': 'string', 'required': False, 'enum': ['replace', 'edit', 'rows', 'file']},
			'format': {'type': 'string', 'required': False, 'enum': ['json', 'raw']},
			'offset': {'type': 'integer', 'required': False},
			'limit': {'type': 'integer', 'required': False},
		},
		'rules': [
			"GET returns JSON (format=raw for bare text/CSV).",
			"POST mode=replace: body is the new content. mode=edit: JSON {old, new, replaceAll}. "
			"mode=rows: JSON {rows: [[...]]}. mode=file: JSON {file, text?}.",
		],
		'response': {'type': 'object'},
	},
	'/snapshot': {
		'query': {
			'path': {'type': 'string', 'required': True},
			'size': {'type': 'integer', 'required': False},
			'format': {'type': 'string', 'required': False, 'default': '.png'},
			'frames': {'type': 'integer', 'required': False},
			'interval': {'type': 'integer', 'required': False, 'default': 5},
			'delay': {'type': 'integer', 'required': False},
		},
		'rules': ["TOPs return image bytes now; other families (or frames/delay) return {jobId}: fetch /job?id=...&frame=0."],
		'response': {'type': 'image | object'},
	},
	'/sample': {
		'query': {
			'path': {'type': 'string', 'required': True},
			'grid': {'type': 'integer', 'required': False},
			'u': {'type': 'number', 'required': False},
			'v': {'type': 'number', 'required': False},
		},
		'response': {'type': 'object', 'shape': {'width': 'integer', 'height': 'integer', 'stats': 'object', 'grid': 'array'}},
	},
	'/job': {
		'query': {'id': {'type': 'string', 'required': True}, 'frame': {'type': 'integer', 'required': False}},
		'response': {'type': 'object | image'},
	},
	'/pulse': {
		'query': {'path': {'type': 'string', 'required': True}, 'par': {'type': 'string', 'required': True}},
		'response': {'type': 'object', 'shape': {'path': 'string', 'par': 'string', 'pulsed': 'boolean'}},
	},
	'/parsearch': {
		'query': {
			'path': {'type': 'string', 'required': False},
			'name': {'type': 'string', 'required': False},
			'value': {'type': 'string', 'required': False},
			'expr': {'type': 'string', 'required': False},
			'recursive': {'type': 'boolean', 'required': False, 'default': True},
			'limit': {'type': 'integer', 'required': False, 'default': 100},
		},
		'response': {'type': 'object', 'shape': {'root': 'string', 'count': 'integer', 'truncated': 'boolean', 'matches': 'array<object>'}},
	},
	'/build': {
		'query': {},
		'rules': ["POST a JSON body: {parent, ops: [{name, type, params, x?, y?}], connections: [{from, to, input?}], "
			"autoLayout?, origin?, annotate?}. Safe to retry: same-name/same-type ops are reused."],
		'response': {'type': 'object', 'shape': {'created': 'array', 'reused': 'array', 'failed': 'array',
			'paramErrors': 'array', 'wired': 'array', 'failedWires': 'array', 'ok': 'boolean'}},
	},
	'/chop': {
		'query': {
			'path': {'type': 'string', 'required': True},
			'samples': {'type': 'integer', 'required': False, 'default': 100},
			'record': {'type': 'number', 'required': False},
			'prePulse': {'type': 'string', 'required': False},
		},
		'response': {'type': 'object'},
	},
	'/wire': {
		'query': {
			'path': {'type': 'string', 'required': True},
			'inputs': {'type': 'csv<string>', 'required': False, 'default': ''},
		},
		'response': {'type': 'object', 'shape': {'path': 'string', 'inputs': 'array<string|null>'}},
		'examples': [
			{
				'method': 'POST',
				'query': 'path=/project1/level1&inputs=/project1/noise1',
				'description': 'Connect one input to a TOP.',
			},
		],
	},
	'/insert': {
		'query': {
			'target': {'type': 'string', 'required': True},
			'opType': {'type': 'string', 'required': True},
			'name': {'type': 'string', 'required': False},
			'inputIndex': {'type': 'integer', 'required': False, 'default': 0},
			'viewer': {'type': 'boolean', 'required': False, 'default': True},
		},
		'response': {'type': 'object', 'shape': {'inserted': 'object', 'target': 'object'}},
		'examples': [
			{
				'method': 'POST',
				'query': 'target=/project1/null1&opType=noiseCHOP&name=noise_offset1',
				'description': 'Insert an operator upstream of target input 0.',
			},
		],
	},
	'/examples': {
		'query': {
			'q': {'type': 'string', 'required': False},
			'offset': {'type': 'integer', 'required': False, 'default': 0},
			'limit': {'type': 'integer', 'required': False, 'default': 50, 'min': 1, 'max': 500},
		},
		'response': {
			'type': 'object',
			'shape': {
				'source': 'string (memory | cache | built)',
				'cacheFile': 'string',
				'tdBuild': 'string',
				'columns': 'array<string>',
				'total': 'integer',
				'offset': 'integer',
				'limit': 'integer',
				'returned': 'integer',
				'rows': 'array<object>',
			},
		},
		'examples': [
			{
				'method': 'GET',
				'query': 'q=feedback&limit=10',
				'description': 'Search the OP Snippets catalog for feedback-related examples.',
			},
		],
	},
	'/examples.tsv': {
		'query': {
			'q': {'type': 'string', 'required': False},
		},
		'response': {
			'type': 'text/tab-separated-values',
			'shape': 'header row + tab-delimited data rows',
		},
		'examples': [
			{
				'method': 'GET',
				'query': 'q=feedback',
				'description': 'Export only matching rows as TSV.',
			},
		],
	},
	'/examples-refresh': {
		'query': {
			'q': {'type': 'string', 'required': False},
			'output': {'type': 'string', 'required': False, 'description': 'Also write a TSV copy here'},
		},
		'response': {
			'type': 'object',
			'shape': {'cacheFile': 'string', 'tdBuild': 'string', 'rows': 'integer', 'columns': 'integer',
				'output': 'string (only with output=)'},
		},
		'examples': [
			{
				'method': 'POST',
				'query': '',
				'description': 'Rebuild the catalog from this TD install.',
			},
		],
	},
	'/help': {
		'query': {
			'q': {'type': 'string', 'required': False, 'description': 'Search the docs (full text once the index is built)'},
			'page': {'type': 'string', 'required': False, 'description': 'Page to read: Noise_TOP, noiseTOP, "Noise TOP", OP_Class'},
			'op': {'type': 'string', 'required': False, 'description': 'Same as page, by operator type (noiseTOP)'},
			'class': {'type': 'string', 'required': False, 'description': 'Python class page (OP, td.Par, CHOP)'},
			'section': {'type': 'string', 'required': False, 'description': 'With page: a section and its subsections (substring ok, e.g. Parameters), or Parent/Child for one nested heading (Methods/General)'},
			'par': {'type': 'string', 'required': False, 'description': "With page: one parameter's entry (e.g. period)"},
			'full': {'type': 'boolean', 'required': False, 'default': False},
			'limit': {'type': 'integer', 'required': False, 'default': 10, 'min': 1, 'max': 50},
			'reindex': {'type': 'boolean', 'required': False, 'default': False, 'description': 'POST: rebuild the search index'},
		},
		'response': {
			'type': 'object',
			'shape': 'search: {query, results: [{page, title, section, score, snippet}], index}; '
				'page: {page, title, url, sections: [{heading, level, chars}], summary | text}',
		},
		'examples': [
			{'method': 'GET', 'query': 'q=feedback loop', 'description': 'Search the docs.'},
			{'method': 'GET', 'query': 'op=noiseTOP&par=period', 'description': "One parameter's documentation."},
			{'method': 'GET', 'query': 'class=OP&section=Methods/General', 'description': 'Part of a Python class page.'},
		],
	},
	'/smoketest': {
		'query': {
			'writeTest': {'type': 'boolean', 'required': False, 'default': False},
		},
		'rules': [
			"Default mode is read-only.",
			"If writeTest=true, request must use POST/PUT and performs a create/destroy probe.",
		],
		'response': {
			'type': 'object',
			'shape': {'passed': 'boolean', 'checkCount': 'integer', 'writeTest': 'boolean', 'checks': 'array<object>'},
		},
		'examples': [
			{'method': 'GET', 'query': '', 'description': 'Read-only pre-commit smoketest.'},
			{'method': 'POST', 'query': 'writeTest=true', 'description': 'Include write create/destroy probe.'},
		],
	},
}


def _route_schema(request, response, pars, webServerDAT, **_):
	"""Machine-readable API contract for this running server instance: route methods, one-line
	summaries, and (for selected high-risk write routes) explicit query param specs + examples.
	Designed for agent self-configuration and preflight validation so calls are correct on first try."""
	route_filter = _first_param(pars, 'route')
	include_examples = _bool_param(pars, 'examples', True)
	routes = {}
	for uri, fn in sorted(_ROUTES.items()):
		if route_filter and uri != route_filter:
			continue
		doc = inspect.getdoc(fn)
		summary = doc.splitlines()[0] if doc else ''
		entry = {
			'methods': _infer_methods(uri, fn),
			'summary': summary,
			'request': {
				'query': {},
				'body': {'type': 'string', 'required': False},
			},
			'response': {'type': 'varies-by-route'},
		}
		override = _SCHEMA_OVERRIDES.get(uri)
		if override:
			entry['request']['query'] = dict(override.get('query', {}))
			entry['response'] = dict(override.get('response', entry['response']))
			if 'rules' in override:
				entry['rules'] = list(override['rules'])
			if include_examples and 'examples' in override:
				entry['examples'] = list(override['examples'])
		routes[uri] = entry
	if route_filter and not routes:
		raise ValueError(f"unknown route '{route_filter}'. try {_KNOWN_ROUTES}")
	_ok_json(response, {
		'version': '1.0.0',
		'generatedAt': time.time(),
		'server': {
			'webserver': webServerDAT.path,
			'port': webServerDAT.par.port.eval(),
		},
		'capabilities': {
			'discovery': ['/', '/docs', '/routes', '/schema'],
			'routeFilterQuery': 'route=/wire',
			'includeExamplesQuery': 'examples=true|false',
		},
		'routes': routes,
	})


def _route_cookstats(request, response, pars, **_):
	"""Read-only cook-cost snapshot for a subtree, scan-style like /errors (path/family/recursive
	or an explicit paths list) instead of /health's fixed comma-separated 'nodes' list. Intended for
	client-side before/after diffing around a state change (call once, trigger the change, call again,
	diff the two responses) - deliberately does NOT sleep/wait server-side, since this callback runs
	on TD's main thread and a blocking sleep here would freeze the whole UI for its duration."""
	paths_param = _first_param(pars, 'paths') or _first_param(pars, 'path')
	family = _first_param(pars, 'family')
	if paths_param and not family and not _first_param(pars, 'recursive'):
		targets = [_resolve_op(p.strip()) for p in paths_param.split(',') if p.strip()]
	else:
		comp = _resolve_op(paths_param) if paths_param else _get_current_network()
		recursive = _bool_param(pars, 'recursive')
		nodes = comp.findChildren(includeUtility=True) if recursive else _nodes_in_comp(comp)
		if family:
			nodes = [n for n in nodes if n.family == family]
		targets = nodes
	stats = [{
		'path': n.path,
		'opType': n.opType,
		'totalCooks': n.totalCooks,
		'cpuCookTime': n.cpuCookTime,
		'gpuCookTime': n.gpuCookTime,
		'cookedThisFrame': n.cookedThisFrame,
	} for n in targets]
	_ok_json(response, {'sampledAt': time.time(), 'nodes': stats})


def _fit_size(target, size):
	"""(w, h) with the longest side = size, keeping a TOP's aspect (square for other families)."""
	longest = int(size or 512)
	if target.isTOP and target.width and target.height:
		scale = longest / max(target.width, target.height)
		return max(1, round(target.width * scale)), max(1, round(target.height * scale))
	return longest, longest


def _capture_op(target, bridge_comp, size):
	"""(op to save, cleanup or None, needs_frames). TOPs render now: natively, or resampled by a
	shared selectTOP when `size` is set. Any other family renders through its own opviewerTOP,
	which only draws after a real cook cycle (same frame = blank white), so it needs frames."""
	if target.isCOMP:
		# What the editor tile shows: the COMP's Operator Viewer target, else its first Out TOP.
		shown = target.par.opviewer.eval() if getattr(target.par, 'opviewer', None) is not None else None
		if shown is None:
			outs = sorted(target.findChildren(type=outTOP, depth=1), key=lambda o: o.digits or 0)
			shown = outs[0] if outs else None
		if shown is not None and shown.isTOP:
			target = shown
	if target.isTOP and not size:
		return target, None, False
	w, h = _fit_size(target, size)
	if target.isTOP:
		resize = bridge_comp.op('_snapshot_resize') or bridge_comp.create(selectTOP, '_snapshot_resize')
		resize.nodeX, resize.nodeY, resize.viewer = 600, -400, False
		resize.par.top = target
		resize.par.outputresolution = 'custom'
		resize.par.resolutionw, resize.par.resolutionh = w, h

		def cleanup_resize():	# don't leave it in the COMP (it would be saved into the .tox)
			if resize.valid:
				resize.destroy()
		return resize, cleanup_resize, False
	viewer = bridge_comp.create(opviewerTOP, f'_snapshot_view{int(time.time() * 1000) % 1000000}')
	viewer.nodeX, viewer.nodeY, viewer.viewer = 750, -400, False
	viewer.par.opviewer = target
	viewer.par.outputresolution = 'custom'
	viewer.par.resolutionw, viewer.par.resolutionh = w, h

	def cleanup():
		if viewer.valid:
			viewer.destroy()
	return viewer, cleanup, True


def _save_image(capture, target, filetype, quality, force=True):
	if force:
		if target.valid:
			target.cook(force=True)
		if capture is not target:
			capture.cook(force=True)
	return _snapshot_top(capture, filetype, quality)


def _route_snapshot(request, response, pars, webServerDAT, **_):
	"""Image of any op: TOP pixels now; COMP/SOP/CHOP/DAT/POP viewers (or frames=N, delay=D) as a
	background job -- the response is {jobId}, and /job?id=...&frame=0 returns the image.
	size=N caps the longest side; interval=K frames between grid frames."""
	target = _resolve_op(_first_param(pars, 'path'))
	size = _first_param(pars, 'size')
	size = int(size) if size else None
	filetype = _first_param(pars, 'format', '.png').lower()
	if not filetype.startswith('.'):
		filetype = '.' + filetype	# accept png as well as .png
	if filetype not in _IMAGE_MIME_TYPES:
		raise BridgeError(f"unsupported format '{filetype}'", code='invalid_format', validValues=sorted(_IMAGE_MIME_TYPES))
	quality = float(_first_param(pars, 'quality', '1.0'))
	frames = int(_first_param(pars, 'frames', '0'))
	delay = int(_first_param(pars, 'delay', '0'))
	capture, cleanup, needs_frames = _capture_op(target, webServerDAT.parent(), size)
	if not (frames > 0 or delay > 0 or needs_frames):
		try:
			data, mime = _save_image(capture, target, filetype, quality, force=_bool_param(pars, 'force', True))
			data = bytearray(data)	# copy out before the helper TOP that produced it is destroyed
		finally:
			if cleanup:
				cleanup()
		response['statusCode'] = 200
		response['statusReason'] = 'OK'
		response['content-type'] = mime
		response['data'] = data
		return
	frames = max(1, min(frames or 1, 16))
	interval = max(1, int(_first_param(pars, 'interval', '5')))
	delay = max(delay, 5 if needs_frames else 0)	# an opviewerTOP shows the op ~3 frames after it's pointed at it
	try:
		job = _new_job('snapshot', expected_frames=delay + frames * interval)
	except BridgeError:
		if cleanup:
			cleanup()
		raise

	def grab(i, attempt=0):
		try:
			if job['status'] != 'running':
				return
			if needs_frames:
				# A fresh opviewerTOP draws nothing (all-zero RGBA) for ~3-10 frames; keep checking.
				capture.cook(force=True)
				arr = capture.numpyArray()
				if arr is None or float(arr[..., 3].max()) == 0.0:
					if attempt < 30:
						run(grab, i, attempt + 1, delayFrames=2)
						return
					job['warning'] = (f"{target.path}'s viewer drew nothing after 60 frames -- e.g. a Base COMP "
						"with no Operator Viewer set and no Out TOP shows an empty tile")
			data, mime = _save_image(capture, target, filetype, quality, force=not needs_frames)
			job['images'].append(bytearray(data))
			job['mime'] = mime
			if len(job['images']) >= frames:
				_finish_job(job)
		except Exception as e:
			_fail_job(job, e)
		finally:
			if cleanup and job['status'] != 'running':
				cleanup()

	for i in range(frames):
		run(grab, i, delayFrames=delay + i * interval + 1)
	_ok_json(response, _job_status(job))


def _route_sample(request, response, pars, **_):
	"""Numbers instead of a picture: an NxN grid of RGBA samples from a TOP plus per-channel
	min/max/mean and NaN/Inf counts (?path&grid=8), or one pixel (?path&u=0.5&v=0.5, TD UV,
	origin bottom-left). Cheap check for blank, black, NaN or saturated output."""
	import numpy as np
	target = _resolve_op(_first_param(pars, 'path'))
	if not target.isTOP:
		raise BridgeError(f"'{target.path}' is not a TOP; /sample reads pixels (use /snapshot for other families)",
			code='wrong_family', family=target.family)
	target.cook(force=True)
	arr = target.numpyArray()	# (h, w, 4) float32, row 0 = bottom
	if arr is None:
		raise BridgeError(f"'{target.path}' has no image yet", code='no_image')
	h, w = arr.shape[:2]

	def clean(v):
		v = float(v)
		return None if v != v or v in (float('inf'), float('-inf')) else round(v, 5)

	result = {'path': target.path, 'width': w, 'height': h, 'channels': int(arr.shape[2])}
	u, v = _first_param(pars, 'u'), _first_param(pars, 'v')
	if u is not None and v is not None:
		x = min(w - 1, max(0, int(float(u) * w)))
		y = min(h - 1, max(0, int(float(v) * h)))
		result['pixel'] = {'u': float(u), 'v': float(v), 'x': x, 'y': y, 'rgba': [clean(c) for c in arr[y, x]]}
	finite = np.isfinite(arr)
	stats = {}
	for ci, name in enumerate('rgba'[:arr.shape[2]]):
		chan = arr[..., ci]
		ok = chan[finite[..., ci]]
		stats[name] = {'min': clean(ok.min()) if ok.size else None, 'max': clean(ok.max()) if ok.size else None,
			'mean': clean(ok.mean()) if ok.size else None,
			'nan': int(np.isnan(chan).sum()), 'inf': int(np.isinf(chan).sum())}
	result['stats'] = stats
	n = int(_first_param(pars, 'grid', '0') or 0)
	if n > 0:
		n = min(n, 32)
		ys = [min(h - 1, int((j + 0.5) * h / n)) for j in range(n)]
		xs = [min(w - 1, int((i + 0.5) * w / n)) for i in range(n)]
		# Rows listed top to bottom (as you'd read the image), each left to right.
		result['grid'] = [[[clean(c) for c in arr[y, x]] for x in xs] for y in reversed(ys)]
		result['gridOrder'] = 'rows top->bottom, columns left->right, values RGBA'
	_ok_json(response, result)


# ---------------------------------------------------------------------------
# Background jobs: multi-frame snapshots and CHOP recordings run across frames via run(),
# never blocking TD's main thread. Poll /job?id=... ; results expire after _JOB_TTL seconds.
# ---------------------------------------------------------------------------

_JOBS = {}
_JOB_TTL = 120.0
_JOB_MAX_ACTIVE = 4


def _new_job(kind, expected_frames=1):
	now = time.time()
	for jid, j in list(_JOBS.items()):	# evict expired results
		if now - j['created'] > _JOB_TTL:
			del _JOBS[jid]
	active = [j for j in _JOBS.values() if j['status'] == 'running']
	if len(active) >= _JOB_MAX_ACTIVE:
		raise BridgeError(f'{len(active)} capture jobs are already running', code='capture_busy', status=429,
			retryAfter=1.0, jobs=[j['id'] for j in active])
	job_id = f'{kind}-{int(now * 1000) % 100000000}-{len(_JOBS)}'
	fps = project.cookRate or 60
	job = {'id': job_id, 'kind': kind, 'status': 'running', 'created': now, 'images': [], 'data': None,
		'mime': None, 'error': None, 'expectedSeconds': round(expected_frames / fps, 3)}
	_JOBS[job_id] = job
	return job


def _finish_job(job, data=None):
	if job['status'] == 'running':
		job['status'] = 'done'
		job['data'] = data
		job['finished'] = time.time()


def _fail_job(job, exc):
	if job['status'] == 'running':
		job['status'] = 'failed'
		job['error'] = f'{type(exc).__name__}: {exc}'


def _job_status(job):
	status = {'jobId': job['id'], 'kind': job['kind'], 'status': job['status']}
	if job['status'] == 'running':
		elapsed = time.time() - job['created']
		status['waitSeconds'] = round(max(0.05, job['expectedSeconds'] - elapsed + 0.05), 3)
	if job['images']:
		status['frames'] = len(job['images'])
		status['frameUrls'] = [f"/job?id={job['id']}&frame={i}" for i in range(len(job['images']))]
	if job['error']:
		status['error'] = job['error']
	if job.get('warning'):
		status['warning'] = job['warning']
	if job['status'] == 'done' and job['data'] is not None:
		status['result'] = job['data']
	return status


def _route_job(request, response, pars, **_):
	"""Status/result of a background capture job: ?id=<jobId>. Snapshot frames: &frame=<i>
	returns that image. Running jobs report waitSeconds before the next poll is useful."""
	job_id = _first_param(pars, 'id')
	job = _JOBS.get(job_id)
	if job is None:
		raise BridgeError(f"no job '{job_id}' (finished results expire after {int(_JOB_TTL)}s)", code='job_not_found',
			status=404, jobs=sorted(_JOBS))
	frame = _first_param(pars, 'frame')
	if frame is not None:
		i = int(frame)
		if i >= len(job['images']):
			raise BridgeError(f"job has {len(job['images'])} frame(s) so far", code='frame_not_ready', status=409,
				frames=len(job['images']), jobStatus=job['status'])
		response['statusCode'] = 200
		response['statusReason'] = 'OK'
		response['content-type'] = job['mime'] or 'image/png'
		response['data'] = job['images'][i]
		return
	_ok_json(response, _job_status(job))


def _fire_pulses(spec):
	"""'path:par,path:par' -> pulse each (same frame as a recording starts)."""
	fired = []
	for item in [s.strip() for s in (spec or '').split(',') if s.strip()]:
		path, _, par_name = item.rpartition(':')
		target = _resolve_op(path)
		par = _get_par(target, par_name)
		par.pulse()
		fired.append(f'{target.path}:{par.name}')
	return fired


def _route_chop(request, response, pars, **_):
	"""CHOP channel data (?path&samples=100). record=<seconds> starts a background job that
	samples every channel once per frame for that long (optionally after pulsing
	prePulse=path:par,... on the same frame) -- collect with /job."""
	target = _resolve_op(_first_param(pars, 'path'))
	if not target.isCHOP:
		raise BridgeError(f"'{target.path}' is not a CHOP (family={target.family})", code='wrong_family', family=target.family)
	record = _first_param(pars, 'record')
	if record:
		seconds = min(float(record), 30.0)
		fps = project.cookRate or 60
		total = max(1, int(round(seconds * fps)))
		names = [c.name for c in target.chans()][:64]
		job = _new_job('chop', expected_frames=total)
		job['data'] = None
		samples = {n: [] for n in names}
		fired = _fire_pulses(_first_param(pars, 'prePulse'))

		def sample(i):
			try:
				if not target.valid:
					raise RuntimeError(f'{target.path} was deleted')
				for n in names:
					c = target[n]
					samples[n].append(round(float(c.eval()), 6) if c is not None else None)
				if i + 1 >= total:
					_finish_job(job, {'path': target.path, 'frames': total, 'seconds': seconds, 'fps': fps,
						'prePulsed': fired, 'channels': samples})
			except Exception as e:
				_fail_job(job, e)

		for i in range(total):
			run(sample, i, delayFrames=i + 1)
		_ok_json(response, _job_status(job))
		return
	if _bool_param(pars, 'force', True):
		target.cook(force=True)
	max_samples = int(_first_param(pars, 'samples', '100'))
	_ok_json(response, _chop_data(target, max_samples))


def _body_text(request):
	"""Request body as str (TD may hand it over as bytes)."""
	body = request.get('data', '') or ''
	if isinstance(body, (bytes, bytearray)):
		body = body.decode('utf-8')
	return body


def _body_json(request, required=True):
	body = _body_text(request).strip()
	if not body:
		if required:
			raise BridgeError('this call needs a JSON body', code='missing_body')
		return None
	try:
		return json.loads(body)
	except json.JSONDecodeError as e:
		raise BridgeError(f'body is not valid JSON: {e}', code='invalid_json')


def _dat_file_path(dat):
	"""Absolute path of a file-synced DAT's file, or None if it isn't synced."""
	if not getattr(dat.par, 'syncfile', None) or not dat.par.syncfile.eval():
		return None
	rel = dat.par.file.eval()
	if not rel:
		return None
	return (rel if os.path.isabs(rel) else os.path.join(project.folder, rel)).replace('\\', '/')


def _write_dat_text(dat, text):
	"""Set a text DAT's content. For a file-synced DAT, write the file (keeping its line endings)
	and reload, instead of setting dat.text and relying on the sync-back -- that path once
	overwrote td_http_api.py with the repr() of its own bytes."""
	path = _dat_file_path(dat)
	if path is None:
		dat.text = text
		return None
	newline = '\n'
	if os.path.isfile(path):
		with open(path, 'rb') as f:
			newline = '\r\n' if b'\r\n' in f.read(65536) else '\n'
	normalized = text.replace('\r\n', '\n')
	with open(path, 'w', encoding='utf-8', newline=newline) as f:
		f.write(normalized)
	dat.par.loadonstartpulse.pulse()
	return path


def _project_relative_file(rel):
	"""Resolve a path for /dat file mode, refusing anything outside the project folder."""
	root = os.path.realpath(project.folder)
	full = os.path.realpath(rel if os.path.isabs(rel) else os.path.join(root, rel))
	if os.path.commonpath([root, full]) != root:
		raise BridgeError(f"'{rel}' is outside the project folder", code='path_outside_project', projectFolder=root)
	return full


def _route_dat(request, response, pars, **_):
	"""Read or edit a DAT. GET returns JSON (text, or table rows with offset/limit); format=raw gives
	the bare text. POST mode=replace (default: raw body = new content), mode=edit (JSON
	{old, new, replaceAll}), mode=rows (JSON {rows: [[...]]}) or mode=file (JSON {file, text?})."""
	target = _resolve_op(_first_param(pars, 'path'))
	if not target.isDAT:
		raise BridgeError(f"'{target.path}' is not a DAT (family={target.family})", code='wrong_family', family=target.family)
	if request['method'] in ('POST', 'PUT'):
		_dat_write(target, request, pars, response)
		return
	if _first_param(pars, 'format') == 'raw':
		_ok_text(response, target.csv if target.isTable else target.text)
		return
	info = {'path': target.path, 'opType': target.OPType, 'editable': target.isEditable}
	file_path = _dat_file_path(target)
	if file_path:
		info['syncFile'] = file_path
	if target.isTable:
		offset = int(_first_param(pars, 'offset', '0'))
		limit = int(_first_param(pars, 'limit', '500'))
		rows = [[c.val for c in target.row(r)] for r in range(offset, min(target.numRows, offset + limit))]
		info.update({'kind': 'table', 'numRows': target.numRows, 'numCols': target.numCols,
			'offset': offset, 'rows': rows, 'truncated': offset + len(rows) < target.numRows})
	else:
		text = target.text
		info.update({'kind': 'text', 'length': len(text), 'lines': text.count('\n') + 1 if text else 0, 'text': text})
	_ok_json(response, info)


def _dat_write(target, request, pars, response):
	if not target.isEditable:
		raise BridgeError(f"'{target.path}' is not editable (its content is generated)", code='not_editable')
	mode = _first_param(pars, 'mode', 'replace')
	result = {'path': target.path, 'mode': mode}
	if mode == 'replace':
		body = _body_text(request)
		if target.isTable:
			target.csv = body
		else:
			result['wroteFile'] = _write_dat_text(target, body)
		result['length'] = len(body)
	elif mode == 'edit':
		spec = _body_json(request)
		old, new = spec.get('old'), spec.get('new')
		if not isinstance(old, str) or not isinstance(new, str) or not old:
			raise BridgeError("mode=edit needs JSON {\"old\": \"...\", \"new\": \"...\"} with a non-empty old", code='invalid_edit')
		text = target.text.replace('\r\n', '\n')
		old_n, new_n = old.replace('\r\n', '\n'), new.replace('\r\n', '\n')
		count = text.count(old_n)
		if count == 0:
			raise BridgeError(f"'old' text not found in {target.path}", code='no_match',
				hint='Copy the exact text (whitespace included) from GET /dat; tabs vs spaces matter.')
		if count > 1 and not spec.get('replaceAll'):
			raise BridgeError(f"'old' text occurs {count} times in {target.path}", code='ambiguous_match', count=count,
				hint='Include more surrounding lines so it is unique, or pass "replaceAll": true.')
		text = text.replace(old_n, new_n) if spec.get('replaceAll') else text.replace(old_n, new_n, 1)
		result['wroteFile'] = _write_dat_text(target, text)
		result['replacements'] = count if spec.get('replaceAll') else 1
	elif mode == 'rows':
		if not target.isTable:
			raise BridgeError(f"mode=rows needs a table DAT; '{target.path}' is text", code='wrong_dat_kind')
		rows = (_body_json(request) or {}).get('rows')
		if not isinstance(rows, list) or not all(isinstance(r, list) for r in rows):
			raise BridgeError('mode=rows needs JSON {"rows": [[...], ...]}', code='invalid_rows')
		target.clear()
		for r in rows:
			target.appendRow([str(c) for c in r])
		result['numRows'] = target.numRows
	elif mode == 'file':
		spec = _body_json(request)
		rel = spec.get('file')
		if not rel:
			raise BridgeError('mode=file needs JSON {"file": "relative/path.py", "text": "..."}', code='missing_file')
		full = _project_relative_file(rel)
		if 'text' in spec:
			os.makedirs(os.path.dirname(full), exist_ok=True)
			with open(full, 'w', encoding='utf-8', newline='\n') as f:
				f.write(spec['text'])
		elif not os.path.isfile(full):
			raise BridgeError(f"'{rel}' doesn't exist; pass \"text\" to create it", code='file_not_found')
		target.par.file = os.path.relpath(full, project.folder).replace('\\', '/')
		target.par.syncfile = True
		ext = os.path.splitext(full)[1].lower()
		lang = {'.py': 'python', '.glsl': 'glsl', '.frag': 'glsl', '.vert': 'glsl', '.json': 'json',
			'.xml': 'xml', '.txt': 'text', '.md': 'text'}.get(ext)
		if lang and getattr(target.par, 'language', None) is not None:
			try:
				target.par.language = lang
			except Exception:
				pass
		target.par.loadonstartpulse.pulse()
		result.update({'file': target.par.file.eval(), 'syncFile': True})
	else:
		raise BridgeError(f"unknown mode '{mode}'", code='invalid_mode', validValues=['replace', 'edit', 'rows', 'file'])
	_touch(target)
	_ok_json(response, result)


# ---------------------------------------------------------------------------
# Parameters: single and batch get/set, pulse, search, custom parameter editing
# ---------------------------------------------------------------------------

def _describe_par_full(par, include_menus=False):
	entry = _describe_par(par)
	entry['label'] = par.label
	entry['style'] = par.style
	if not par.isDefault:
		entry['default'] = par.default if not par.isOP else None
	else:
		entry['isDefault'] = True
	if par.bindExpr:
		entry['bindExpr'] = par.bindExpr
	if include_menus and par.isMenu:
		entry['menuNames'] = list(par.menuNames)
		entry['menuLabels'] = list(par.menuLabels)
	return entry


def _set_one_par(target, name, spec):
	"""Apply one batch-set spec to target. spec is a value, a list (tuplet: 't': [1, 2, 3]), or a
	dict {value|expr|reset|unbind|pulse|bindExpr}. Returns list of par names touched."""
	if isinstance(spec, list):
		group = target.parGroup[name] if hasattr(target, 'parGroup') else None
		if group is None:
			raise BridgeError(f"'{name}' is not a parameter tuplet on '{target.path}' (lists set tuplets like t: [x, y, z])",
				code='not_a_tuplet', path=target.path, par=name,
				suggestions=_suggest(name, [g.name for g in target.parGroups]) or None)
		if len(spec) != len(group):
			raise BridgeError(f"'{name}' has {len(group)} components, got {len(spec)} values", code='wrong_tuplet_size',
				size=len(group))
		for p, v in zip(group, spec):
			p.val = _coerce_par_value(p, v)
		return [p.name for p in group]
	par = _get_par(target, name)
	if not isinstance(spec, dict):
		par.val = _coerce_par_value(par, spec)
		return [par.name]
	if spec.get('reset'):
		par.reset()
	elif spec.get('unbind'):
		if par.bindExpr:
			par.bindExpr = ''
		par.mode = ParMode.CONSTANT
	elif spec.get('pulse'):
		par.pulse()
	elif 'expr' in spec:
		par.expr = spec['expr']
		par.mode = ParMode.EXPRESSION
	elif 'bindExpr' in spec:
		par.bindExpr = spec['bindExpr']
		par.mode = ParMode.BIND
	elif 'value' in spec:
		par.val = _coerce_par_value(par, spec['value'])
	else:
		raise BridgeError(f"don't know how to apply {spec!r} to '{name}'", code='invalid_spec',
			validKeys=['value', 'expr', 'bindExpr', 'reset', 'unbind', 'pulse'])
	return [par.name]


def _route_par(request, response, pars, **_):
	"""Read or set parameters. Single: ?path&par (+ value/expr/reset/unbind on POST). Batch read:
	?paths=a,b&pars=t*,file (globs). Batch set: POST a JSON body {"set": {"<path>": {"<par>":
	value | [tuplet] | {"expr"|"bindExpr"|"reset"|"unbind"|"pulse"}}}} -- partial failures are
	reported per par, everything else is applied."""
	writing = request['method'] in ('POST', 'PUT')
	body = _body_text(request).strip() if writing else ''
	if writing and body.startswith('{'):
		spec = _body_json(request).get('set')
		if not isinstance(spec, dict):
			raise BridgeError('batch set needs JSON {"set": {"<op path>": {"<par>": <value>, ...}}}', code='invalid_batch')
		results, ok = {}, True
		for path, par_specs in spec.items():
			entry = {'set': [], 'errors': []}
			results[path] = entry
			try:
				target = _resolve_op(path)
			except BridgeError as e:
				entry['errors'].append({'error': str(e), 'code': e.code, **e.details})
				ok = False
				continue
			for name, value in (par_specs or {}).items():
				try:
					entry['set'].extend(_set_one_par(target, name, value))
				except BridgeError as e:
					entry['errors'].append({'par': name, 'error': str(e), 'code': e.code, **e.details})
					ok = False
				except Exception as e:
					entry['errors'].append({'par': name, 'error': f'{type(e).__name__}: {e}', 'code': 'set_failed'})
					ok = False
			_touch(target)
		_ok_json(response, {'ok': ok, 'results': results})
		return

	paths = _first_param(pars, 'paths')
	if paths and not writing:
		patterns = (_first_param(pars, 'pars') or '*').split(',')
		include_defaults = _bool_param(pars, 'includeDefaults')
		include_menus = _bool_param(pars, 'includeMenus')
		out = {}
		for path in [p.strip() for p in paths.split(',') if p.strip()]:
			target = _resolve_op(path)
			found = {}
			for pattern in patterns:
				for par in target.pars(pattern.strip()):
					if par.hidden or (par.isDefault and not include_defaults and pattern.strip() in ('*', '')):
						continue
					found[par.name] = _describe_par_full(par, include_menus)
			out[target.path] = found
		_ok_json(response, {'ops': out})
		return

	target = _resolve_op(_first_param(pars, 'path'))
	par_name = _first_param(pars, 'par')
	if not par_name:
		raise BridgeError("missing required 'par' query parameter", code='missing_param', param='par')
	if writing:
		expr = _first_param(pars, 'expr')
		raw_value = _first_param(pars, 'value')
		if _bool_param(pars, 'reset'):
			_set_one_par(target, par_name, {'reset': True})
		elif _bool_param(pars, 'unbind'):
			_set_one_par(target, par_name, {'unbind': True})
		elif expr is not None:
			_set_one_par(target, par_name, {'expr': expr})
		elif raw_value is not None:
			_set_one_par(target, par_name, {'value': raw_value})
		else:
			raise BridgeError("POST /par needs 'value', 'expr', 'reset=true' or 'unbind=true' (or a JSON batch body)",
				code='missing_param')
		_touch(target)
	par = _get_par(target, par_name)
	_ok_json(response, _describe_par_full(par, include_menus=_bool_param(pars, 'includeMenus')))


def _route_pulse(request, response, pars, **_):
	"""Pulse a pulse-style parameter (Reload, Resetpulse, custom pulse buttons): POST ?path&par."""
	_require_write_method(request, '/pulse')
	target = _resolve_op(_first_param(pars, 'path'))
	par = _get_par(target, _first_param(pars, 'par'))
	if not par.isPulse and not par.isMomentary:
		raise BridgeError(f"'{par.name}' is a {par.style} par, not a pulse", code='not_a_pulse', style=par.style)
	par.pulse()
	_touch(target)
	_ok_json(response, {'path': target.path, 'par': par.name, 'pulsed': True})


def _route_parsearch(request, response, pars, **_):
	"""Find parameters under a COMP by name, value or expression glob (case-insensitive):
	?path=/project1&name=*file*&value=*.mp4&expr=*absTime*&recursive=true&limit=100."""
	import fnmatch
	root = _resolve_op(_first_param(pars, 'path')) if _first_param(pars, 'path') else _get_current_network()
	name_glob = (_first_param(pars, 'name') or '').lower()
	value_glob = (_first_param(pars, 'value') or '').lower()
	expr_glob = (_first_param(pars, 'expr') or '').lower()
	if not (name_glob or value_glob or expr_glob):
		raise BridgeError("give at least one of name, value or expr (globs, e.g. value=*.mp4)", code='missing_param')
	limit = int(_first_param(pars, 'limit', '100'))
	nodes = root.findChildren(includeUtility=False) if _bool_param(pars, 'recursive', True) else _nodes_in_comp(root)
	matches, truncated = [], False
	for node in nodes:
		for par in node.pars('*'):
			if par.hidden:
				continue
			if name_glob and not (fnmatch.fnmatch(par.name.lower(), name_glob) or fnmatch.fnmatch(par.label.lower(), name_glob)):
				continue
			if expr_glob and not (par.expr and fnmatch.fnmatch(par.expr.lower(), expr_glob)):
				continue
			if value_glob:
				val = _par_json_value(par)
				if not fnmatch.fnmatch(str(val).lower(), value_glob):
					continue
			if len(matches) >= limit:
				truncated = True
				break
			entry = _describe_par(par)
			entry['path'] = node.path
			matches.append(entry)
		if truncated:
			break
	_ok_json(response, {'root': root.path, 'count': len(matches), 'truncated': truncated, 'matches': matches})


_CUSTOM_PAR_STYLES = ('Float', 'Int', 'Str', 'StrMenu', 'Menu', 'Toggle', 'Pulse', 'Momentary', 'Header',
	'XY', 'XYZ', 'XYZW', 'WH', 'UV', 'UVW', 'RGB', 'RGBA', 'File', 'FileSave', 'Folder', 'Python',
	'OP', 'COMP', 'Object', 'PanelCOMP', 'TOP', 'CHOP', 'SOP', 'MAT', 'DAT', 'POP')
_SIZED_STYLES = ('Float', 'Int')
_PAR_ATTRS = ('label', 'default', 'min', 'max', 'normMin', 'normMax', 'clampMin', 'clampMax', 'help',
	'readOnly', 'enable', 'menuNames', 'menuLabels', 'section', 'startSection')


def _custom_par_info(par):
	info = {'name': par.name, 'label': par.label, 'style': par.style, 'value': _par_json_value(par)}
	if par.isNumber:
		info.update({'default': par.default, 'min': par.min, 'max': par.max, 'normMin': par.normMin,
			'normMax': par.normMax, 'clampMin': par.clampMin, 'clampMax': par.clampMax})
	elif not par.isOP:
		info['default'] = par.default
	if par.isMenu:
		info['menuNames'] = list(par.menuNames)
		info['menuLabels'] = list(par.menuLabels)
	if par.help:
		info['help'] = par.help
	if par.readOnly:
		info['readOnly'] = True
	if par.mode.name == 'EXPRESSION':
		info['expr'] = par.expr
	return info


def _route_custompars(request, response, pars, **_):
	"""List or edit a COMP's custom parameters. GET ?path -> pages and pars. POST ?path with JSON
	{"actions": [{"action": "add"|"edit"|"delete"|"addpage"|"deletepage"|"renamepage"|"sort", ...}]}.
	Every action is validated before anything changes, so a bad batch leaves the COMP untouched."""
	comp = _resolve_op(_first_param(pars, 'path'))
	if not comp.isCOMP:
		raise BridgeError(f"'{comp.path}' is not a COMP; only COMPs have custom parameters", code='wrong_family')
	if request['method'] not in ('POST', 'PUT'):
		_ok_json(response, {'path': comp.path, 'pages': [
			{'page': pg.name, 'pars': [_custom_par_info(p) for p in pg.pars]} for pg in comp.customPages]})
		return
	actions = (_body_json(request) or {}).get('actions')
	if not isinstance(actions, list) or not actions:
		raise BridgeError('POST /custompars needs JSON {"actions": [...]}', code='invalid_actions')
	_validate_custom_actions(comp, actions)
	done = []
	for a in actions:
		done.append(_apply_custom_action(comp, a))
	_touch(comp)
	_ok_json(response, {'path': comp.path, 'applied': done,
		'pages': [{'page': pg.name, 'pars': [p.name for p in pg.pars]} for pg in comp.customPages]})


def _validate_custom_actions(comp, actions):
	pages = {pg.name for pg in comp.customPages}
	existing = {p.name for p in comp.customPars}
	errors = []
	for i, a in enumerate(actions):
		kind = a.get('action')
		name = a.get('name')
		where = f'actions[{i}] ({kind} {name or a.get("page", "")})'
		if kind == 'add':
			style = a.get('style')
			if style not in _CUSTOM_PAR_STYLES:
				errors.append({'at': where, 'code': 'invalid_style', 'error': f"style '{style}' is not a custom par style",
					'validValues': list(_CUSTOM_PAR_STYLES), 'suggestions': _suggest(style, _CUSTOM_PAR_STYLES) or None})
			if not name or not re.fullmatch(r'[A-Z][a-z0-9]*', name):
				errors.append({'at': where, 'code': 'invalid_name',
					'error': f"custom par names must be a capital letter then lowercase letters/digits (e.g. 'Speed', 'Port2'), got {name!r}"})
			elif name in existing and not a.get('replace'):
				errors.append({'at': where, 'code': 'par_exists', 'error': f"'{name}' already exists; use edit, or pass replace: true"})
			if not a.get('page'):
				errors.append({'at': where, 'code': 'missing_page', 'error': 'add needs a page (created if missing)'})
			if a.get('size') and style not in _SIZED_STYLES:
				errors.append({'at': where, 'code': 'invalid_size', 'error': 'size only applies to Float and Int'})
			existing.add(name)
			pages.add(a.get('page'))
		elif kind in ('edit', 'delete'):
			if name not in existing:
				errors.append({'at': where, 'code': 'par_not_found', 'error': f"no custom par '{name}'",
					'suggestions': _suggest(name, existing) or None})
			if kind == 'edit':
				unknown = [k for k in a if k not in ('action', 'name', 'value', 'expr') + _PAR_ATTRS]
				if unknown:
					errors.append({'at': where, 'code': 'invalid_field', 'error': f'unknown fields {unknown}',
						'validValues': list(_PAR_ATTRS) + ['value', 'expr']})
			else:
				existing.discard(name)
		elif kind in ('deletepage', 'renamepage', 'sort'):
			if a.get('page') not in pages:
				errors.append({'at': where, 'code': 'page_not_found', 'error': f"no custom page '{a.get('page')}'",
					'suggestions': _suggest(a.get('page'), pages) or None})
			if kind == 'renamepage' and not a.get('to'):
				errors.append({'at': where, 'code': 'missing_param', 'error': 'renamepage needs "to"'})
			if kind == 'sort' and not isinstance(a.get('order'), list):
				errors.append({'at': where, 'code': 'missing_param', 'error': 'sort needs "order": [par names]'})
			if kind == 'deletepage':
				pages.discard(a.get('page'))
			if kind == 'renamepage':
				pages.discard(a.get('page'))
				pages.add(a.get('to'))
		elif kind == 'addpage':
			if not a.get('page'):
				errors.append({'at': where, 'code': 'missing_page', 'error': 'addpage needs a page name'})
			pages.add(a.get('page'))
		else:
			errors.append({'at': where, 'code': 'invalid_action', 'error': f"unknown action {kind!r}",
				'validValues': ['add', 'edit', 'delete', 'addpage', 'deletepage', 'renamepage', 'sort']})
	if errors:
		raise BridgeError(f'{len(errors)} invalid action(s); nothing was changed', code='invalid_actions', errors=errors)


def _apply_par_attrs(par_group, a):
	for p in par_group:
		for attr in _PAR_ATTRS:
			if attr in a and attr not in ('menuNames', 'menuLabels'):
				setattr(p, attr, a[attr])
		# An agent's "min 0, max 10" means the slider range too, unless it set normMin/normMax.
		if 'min' in a and 'normMin' not in a and p.isNumber:
			p.normMin = a['min']
		if 'max' in a and 'normMax' not in a and p.isNumber:
			p.normMax = a['max']
		if 'menuNames' in a:
			p.menuNames = a['menuNames']
			p.menuLabels = a.get('menuLabels', a['menuNames'])
		elif 'menuLabels' in a:
			p.menuLabels = a['menuLabels']
	if 'value' in a:
		vals = a['value'] if isinstance(a['value'], list) else [a['value']] * len(par_group)
		for p, v in zip(par_group, vals):
			p.val = _coerce_par_value(p, v)
	if 'expr' in a:
		for p in par_group:
			p.expr = a['expr']


def _apply_custom_action(comp, a):
	kind = a['action']
	if kind in ('add', 'addpage'):
		page = next((pg for pg in comp.customPages if pg.name == a['page']), None) or comp.appendCustomPage(a['page'])
		if kind == 'addpage':
			return f"page {a['page']}"
		method = getattr(page, f"append{a['style']}")
		kwargs = {'label': a.get('label', a['name'])}
		if a['style'] in _SIZED_STYLES and a.get('size'):
			kwargs['size'] = int(a['size'])
		group = method(a['name'], replace=True, **kwargs)
		_apply_par_attrs(group, a)
		if 'default' in a and 'value' not in a:
			for p in group:
				p.val = p.default
		return f"added {a['name']} ({a['style']})"
	if kind == 'edit':
		group = comp.parGroup[a['name']]	# ParGroup has no truth value in TD; compare to None
		_apply_par_attrs(group if group is not None else [comp.par[a['name']]], a)
		return f"edited {a['name']}"
	if kind == 'delete':
		comp.par[a['name']].destroy()
		return f"deleted {a['name']}"
	page = next(pg for pg in comp.customPages if pg.name == a['page'])
	if kind == 'deletepage':
		page.destroy()
		return f"deleted page {a['page']}"
	if kind == 'renamepage':
		page.name = a['to']
		return f"renamed page {a['page']} -> {a['to']}"
	if kind == 'sort':
		page.sort(*a['order'])
		return f"sorted page {a['page']}"
	raise BridgeError(f'unknown action {kind}', code='invalid_action')


# ---------------------------------------------------------------------------
# /build: create + parameterize + wire + lay out many ops in one call
# ---------------------------------------------------------------------------

_LAYOUT_DX = 175	# TD layout convention (haxlib's td-node-layout skill): 130 wide + 45 gap
_LAYOUT_DY = 125	# between parallel chains

_PAR_HELP = None


def _par_help():
	"""TD's own TDParameterHelp.json ({family: {opType: {summary, label, parameters}}}), cached.
	Gives every op type's parameters without creating a probe op."""
	global _PAR_HELP
	if _PAR_HELP is None:
		try:
			with open(_par_help_path(), encoding='utf-8') as f:
				_PAR_HELP = json.load(f).get('help', {})
		except (OSError, ValueError):
			_PAR_HELP = {}
	return _PAR_HELP


def _all_op_types():
	return [t for fam in _par_help().values() for t in fam]


def _op_type_help(op_type):
	for fam in _par_help().values():
		if op_type in fam:
			return fam[op_type]
	return None


def _topo_layers(names, edges):
	"""Longest-path layering of a DAG given as names + (from, to) edges. Returns (order, column,
	cycle_nodes); nodes in feedback loops are placed right of their ranked inputs."""
	index = {n: i for i, n in enumerate(names)}
	succ = {n: [] for n in names}
	pred = {n: [] for n in names}
	indeg = {n: 0 for n in names}
	for a, b in dict.fromkeys(e for e in edges if e[0] in index and e[1] in index and e[0] != e[1]):
		succ[a].append(b)
		pred[b].append(a)
		indeg[b] += 1
	queue = sorted((n for n in names if indeg[n] == 0), key=index.get)
	work = dict(indeg)
	order = []
	while queue:
		u = queue.pop(0)
		order.append(u)
		for v in succ[u]:
			work[v] -= 1
			if work[v] == 0:
				queue.append(v)
		queue.sort(key=index.get)
	cycle = [n for n in names if n not in set(order)]
	col = {n: 0 for n in names}
	for u in order + cycle:
		for v in succ[u]:
			if v not in cycle or u not in cycle:
				col[v] = max(col[v], col[u] + 1)
	return order + cycle, col, cycle, pred


def _auto_layout(names, edges, origin):
	"""{name: (x, y)}: columns by longest path left-to-right, rows ordered by the barycenter of
	their inputs' rows (fewer crossings), declaration order as tiebreak."""
	_, col, _, pred = _topo_layers(names, edges)
	index = {n: i for i, n in enumerate(names)}
	columns = {}
	for n in names:
		columns.setdefault(col[n], []).append(n)
	row, pos = {}, {}
	for c in sorted(columns):
		def bary(n):
			rows = [row[p] for p in pred[n] if p in row]
			return sum(rows) / len(rows) if rows else index[n]
		for r, n in enumerate(sorted(columns[c], key=lambda n: (bary(n), index[n]))):
			row[n] = r
			pos[n] = (origin[0] + c * _LAYOUT_DX, origin[1] - r * _LAYOUT_DY)
	return pos


def _route_build(request, response, pars, **_):
	"""Create, parameterize, wire and lay out many ops in one call. POST a JSON body:
	{"parent": "/project1/x", "ops": [{"name", "type", "params": {...}, "x", "y", "viewer",
	"comment", "flags": {...}}], "connections": [{"from", "to", "input": 0}], "autoLayout": true,
	"annotate": "Title"}. Ops that already exist with the same type are reused (params
	re-applied), so a retried build is safe. Partial failures are listed, not fatal."""
	_require_write_method(request, '/build')
	spec = _body_json(request)
	parent = _resolve_op(spec['parent']) if spec.get('parent') else _get_current_network()
	if not parent.isCOMP:
		raise BridgeError(f"'{parent.path}' is not a COMP", code='wrong_family')
	ops_spec = spec.get('ops') or []
	if not isinstance(ops_spec, list) or not all(isinstance(o, dict) and o.get('name') and o.get('type') for o in ops_spec):
		raise BridgeError('ops must be a list of {"name", "type", ...}', code='invalid_ops')
	connections = spec.get('connections') or []
	result = {'parent': parent.path, 'created': [], 'reused': [], 'failed': [], 'paramErrors': [],
		'wired': [], 'failedWires': []}
	built = {}
	for o in ops_spec:
		name, op_type = o['name'], o['type']
		existing = parent.op(name)
		try:
			if existing is not None:
				if existing.OPType != op_type:
					raise BridgeError(f"'{existing.path}' exists as {existing.OPType}, not {op_type}", code='type_mismatch',
						path=existing.path, existingType=existing.OPType)
				node = existing
				result['reused'].append(node.path)
			else:
				try:
					node = parent.create(op_type, name)
				except Exception as e:
					raise BridgeError(f"can't create '{op_type}': {e}", code='invalid_op_type',
						suggestions=_suggest(op_type, _all_op_types()) or None)
				node.viewer = o.get('viewer', True)
				result['created'].append(node.path)
			built[name] = node
		except BridgeError as e:
			result['failed'].append({'name': name, 'error': str(e), 'code': e.code, **e.details})
			continue
		for par_name, value in (o.get('params') or {}).items():
			try:
				_set_one_par(node, par_name, value)
			except BridgeError as e:
				result['paramErrors'].append({'op': node.path, 'par': par_name, 'error': str(e), 'code': e.code, **e.details})
			except Exception as e:
				result['paramErrors'].append({'op': node.path, 'par': par_name, 'error': f'{type(e).__name__}: {e}', 'code': 'set_failed'})
		if o.get('comment'):
			node.comment = o['comment']
		for flag, value in (o.get('flags') or {}).items():
			if flag in _KNOWN_OP_FLAGS:
				setattr(node, flag, bool(value))
			else:
				result['paramErrors'].append({'op': node.path, 'par': flag, 'code': 'invalid_flag',
					'error': f"unknown flag '{flag}'", 'validValues': sorted(_KNOWN_OP_FLAGS)})
		_touch(node)

	def lookup(ref):
		if ref in built:
			return built[ref]
		return parent.op(ref) or op(ref)

	edges = []
	for c in connections:
		src, dst = lookup(c.get('from')), lookup(c.get('to'))
		idx = int(c.get('input', 0))
		try:
			if src is None or dst is None:
				missing = c.get('from') if src is None else c.get('to')
				raise BridgeError(f"no op named '{missing}' in this build or in {parent.path}", code='op_not_found',
					suggestions=_suggest(missing, list(built) + [n.name for n in _nodes_in_comp(parent)]) or None)
			if idx >= len(dst.inputConnectors):
				raise BridgeError(f"'{dst.path}' has {len(dst.inputConnectors)} input(s); input {idx} doesn't exist",
					code='input_out_of_range', numInputs=len(dst.inputConnectors))
			dst.inputConnectors[idx].connect(src.outputConnectors[int(c.get('fromOutput', 0))])
			result['wired'].append({'from': src.path, 'to': dst.path, 'input': idx})
			edges.append((src.name, dst.name))
		except BridgeError as e:
			result['failedWires'].append({**c, 'error': str(e), 'code': e.code, **e.details})
		except Exception as e:
			result['failedWires'].append({**c, 'error': f'{type(e).__name__}: {e}', 'code': 'wire_failed'})

	# Layout: explicit x/y win. Newly created ops without x/y are auto-laid-out to the right of
	# what's already there; reused ops stay where they are, so a retried build doesn't drift.
	created = {n for n, node in built.items() if node.path in result['created']}
	needs_layout = [n for n in built if n in created and 'x' not in next(o for o in ops_spec if o['name'] == n)]
	if needs_layout and spec.get('autoLayout', True):
		others = [n for n in _nodes_in_comp(parent) if n.name not in created and n.OPType != 'annotateCOMP'
			and not getattr(n, 'dock', None)]
		origin = spec.get('origin') or ([max(n.nodeX + n.nodeWidth for n in others) + 200, max(n.nodeY for n in others)]
			if others else [0, 0])
		pos = _auto_layout(list(built), edges, origin)
		for n in needs_layout:
			_move_node(built[n], *pos[n])
	for o in ops_spec:
		if o['name'] in built and 'x' in o:
			_move_node(built[o['name']], o['x'], o.get('y', 0))
	if spec.get('annotate') and built:
		title = spec['annotate']
		existing_box = next((a for a in parent.findChildren(type=annotateCOMP, depth=1, includeUtility=True)
			if a.par.Titletext.eval() == title), None)
		box = existing_box or _create_annotation(parent, list(built.values()), title=title)
		result['annotation'] = box.path
	result['ok'] = not (result['failed'] or result['paramErrors'] or result['failedWires'])
	_ok_json(response, result)


def _route_create(request, response, pars, **_):
	_require_write_method(request, '/create')
	parent_path = _first_param(pars, 'parent')
	parent = _resolve_op(parent_path) if parent_path else _get_current_network()
	if not parent.isCOMP:
		raise ValueError(f"'{parent.path}' is not a COMP, can't create children inside it")
	op_type = _first_param(pars, 'opType')
	if not op_type:
		raise ValueError("missing required 'opType' query parameter")
	inputs = _resolve_inputs(_first_param(pars, 'inputs'))
	name = _first_param(pars, 'name')
	new_node = parent.create(op_type, name) if name else parent.create(op_type)
	new_node.viewer = _bool_param(pars, 'viewer', True)
	x = _first_param(pars, 'x')
	y = _first_param(pars, 'y')
	if x is None and y is None:
		nx, ny = _auto_place(parent, inputs, exclude=new_node)
		new_node.nodeX = nx
		new_node.nodeY = ny
	else:
		if x is not None:
			new_node.nodeX = float(x)
		if y is not None:
			new_node.nodeY = float(y)
	if inputs:
		new_node.setInputs(inputs)
	_ok_json(response, _node_summary(new_node))


def _route_wire(request, response, pars, **_):
	_require_write_method(request, '/wire')
	target = _resolve_op(_first_param(pars, 'path'))
	inputs = _resolve_inputs(_first_param(pars, 'inputs'))
	target.setInputs(inputs)
	_ok_json(response, _node_summary(target))


def _route_duplicate(request, response, pars, **_):
	_require_write_method(request, '/duplicate')
	paths_param = _first_param(pars, 'paths') or _first_param(pars, 'path')
	if not paths_param:
		raise ValueError("missing required 'path' or 'paths' query parameter")
	sources = [_resolve_op(p.strip()) for p in paths_param.split(',') if p.strip()]
	parent_path = _first_param(pars, 'parent')
	target_parent = _resolve_op(parent_path) if parent_path else sources[0].parent()
	if not target_parent.isCOMP:
		raise ValueError(f"'{target_parent.path}' is not a COMP, can't create children inside it")
	if len(sources) == 1:
		name = _first_param(pars, 'name')
		copies = [target_parent.copy(sources[0], name=name) if name else target_parent.copy(sources[0])]
	else:
		copies = target_parent.copyOPs(sources)
	for n in copies:
		n.viewer = True
	dx = _first_param(pars, 'dx')
	dy = _first_param(pars, 'dy')
	x = _first_param(pars, 'x')
	y = _first_param(pars, 'y')
	if (dx is not None or dy is not None) and (x is not None or y is not None):
		raise ValueError("specify either absolute 'x'/'y' (single copy only) or relative 'dx'/'dy' (any count), not both")
	if dx is not None or dy is not None:
		ddx = float(dx) if dx is not None else 0.0
		ddy = float(dy) if dy is not None else 0.0
		for n in copies:
			n.nodeX += ddx
			n.nodeY += ddy
	elif x is not None or y is not None:
		if len(copies) != 1:
			raise ValueError("absolute 'x'/'y' positioning only supports duplicating a single 'path' - use 'dx'/'dy' for multiple")
		if x is not None:
			copies[0].nodeX = float(x)
		if y is not None:
			copies[0].nodeY = float(y)
	_ok_json(response, [_node_summary(n) for n in copies])


def _route_comment(request, response, pars, **_):
	_require_write_method(request, '/comment')
	paths_param = _first_param(pars, 'paths') or _first_param(pars, 'path')
	if not paths_param:
		raise ValueError("missing required 'path' or 'paths' query parameter")
	text = _first_param(pars, 'text')
	if text is None:
		raise ValueError("missing required 'text' query parameter (pass an empty string to clear)")
	targets = [_resolve_op(p.strip()) for p in paths_param.split(',') if p.strip()]
	for n in targets:
		n.comment = text
	_ok_json(response, [{'path': n.path, 'comment': n.comment} for n in targets])

def _route_flag(request, response, pars, **_):
	_require_write_method(request, '/flag')
	name = _first_param(pars, 'name')
	if not name:
		raise ValueError(f"missing required 'name' query parameter (one of: {', '.join(sorted(_KNOWN_OP_FLAGS))})")
	if name not in _KNOWN_OP_FLAGS:
		raise ValueError(f"unknown flag '{name}' - must be one of: {', '.join(sorted(_KNOWN_OP_FLAGS))}")
	value = _bool_param(pars, 'value', True)
	paths_param = _first_param(pars, 'paths') or _first_param(pars, 'path')
	family = _first_param(pars, 'family')
	# Disambiguate 'path' as a single explicit target (no family/recursive given) vs. a
	# scan root (family and/or recursive given) - mirrors /errors' path+recursive scan mode.
	if paths_param and not family and not _first_param(pars, 'recursive'):
		targets = [_resolve_op(p.strip()) for p in paths_param.split(',') if p.strip()]
	else:
		comp = _resolve_op(paths_param) if paths_param else _get_current_network()
		recursive = _bool_param(pars, 'recursive')
		nodes = comp.findChildren(includeUtility=True) if recursive else _nodes_in_comp(comp)
		if family:
			nodes = [n for n in nodes if n.family == family]
		targets = nodes
	if not targets:
		raise ValueError(f"no nodes found to set '{name}' on")
	for n in targets:
		setattr(n, name, value)
	_ok_json(response, [{'path': n.path, name: getattr(n, name)} for n in targets])


def _route_bypass(request, response, pars, **_):
	# Backward-compatible alias: /bypass is /flag pinned to name=bypass.
	pars = dict(pars)
	pars['name'] = 'bypass'
	_route_flag(request, response, pars)


def _route_annotate(request, response, pars, **_):
	_require_write_method(request, '/annotate')
	paths_param = _first_param(pars, 'paths')
	if not paths_param:
		raise ValueError("missing required 'paths' query parameter (nodes to enclose)")
	nodes = [_resolve_op(p.strip()) for p in paths_param.split(',') if p.strip()]
	parent_path = _first_param(pars, 'parent')
	parent = _resolve_op(parent_path) if parent_path else nodes[0].parent()
	if not parent.isCOMP:
		raise ValueError(f"'{parent.path}' is not a COMP, can't create children inside it")
	title = _first_param(pars, 'title', '') or ''
	body = _first_param(pars, 'body', '') or ''
	mode = _first_param(pars, 'mode', 'networkbox') or 'networkbox'
	if mode not in ('networkbox', 'annotate', 'comment'):
		raise ValueError(f"invalid mode '{mode}'. Must be 'networkbox', 'annotate', or 'comment'")
	pad = float(_first_param(pars, 'pad', '40'))
	annotation = _create_annotation(parent, nodes, title=title, body=body, mode=mode, pad=pad)
	_ok_json(response, _node_summary(annotation))


def _route_move(request, response, pars, **_):
	_require_write_method(request, '/move')
	paths_param = _first_param(pars, 'paths') or _first_param(pars, 'path')
	if not paths_param:
		raise ValueError("missing required 'path' or 'paths' query parameter")
	targets = [_resolve_op(p.strip()) for p in paths_param.split(',') if p.strip()]
	dx = _first_param(pars, 'dx')
	dy = _first_param(pars, 'dy')
	x = _first_param(pars, 'x')
	y = _first_param(pars, 'y')
	if (dx is not None or dy is not None) and (x is not None or y is not None):
		raise ValueError("specify either absolute 'x'/'y' or relative 'dx'/'dy', not both")
	# Docked ops (a GLSL TOP's shader DATs, a ramp's keys) follow their host, as in the editor;
	# one that's also listed explicitly is only moved once.
	listed = {n.path for n in targets}
	movers = [n for n in targets if not (getattr(n, 'dock', None) is not None and n.dock.path in listed)]
	if dx is not None or dy is not None:
		ddx = float(dx) if dx is not None else 0.0
		ddy = float(dy) if dy is not None else 0.0
		for n in movers:
			_move_node(n, n.nodeX + ddx, n.nodeY + ddy)
	else:
		if x is None and y is None:
			raise ValueError("must supply 'x'/'y' (absolute) or 'dx'/'dy' (relative)")
		if len(targets) != 1:
			raise ValueError("absolute 'x'/'y' positioning only supports a single 'path' - use 'paths' with 'dx'/'dy' to shift a group")
		n = targets[0]
		_move_node(n, float(x) if x is not None else n.nodeX, float(y) if y is not None else n.nodeY)
	_ok_json(response, [_node_summary(n) for n in targets])


def _route_layout(request, response, pars, **_):
	"""Lay out operators via COMP.layout() -- the Python equivalent of TouchDesigner's Shift+L
	'Layout Nodes' command. Arranges the given ops in a line or grid, snapping them into a clean,
	non-overlapping arrangement relative to their wiring, instead of guessing x/y by hand. Returns
	the updated node summaries so the caller can verify the result inline, without a follow-up
	/network call."""
	_require_write_method(request, '/layout')
	paths_param = _first_param(pars, 'paths')
	parent_param = _first_param(pars, 'parent')
	horizontal = _bool_param(pars, 'horizontal') if _first_param(pars, 'horizontal') is not None else None
	vertical = _bool_param(pars, 'vertical') if _first_param(pars, 'vertical') is not None else None
	grid_rows_raw = _first_param(pars, 'gridRows')
	grid_rows = int(grid_rows_raw) if grid_rows_raw else 0

	modes_given = sum(1 for m in (horizontal, vertical, grid_rows) if m)
	if modes_given > 1:
		raise ValueError("specify only one of 'horizontal', 'vertical', or 'gridRows'")

	if paths_param:
		targets = [_resolve_op(p.strip()) for p in paths_param.split(',') if p.strip()]
		if not targets:
			raise ValueError("no valid operators found in 'paths'")
		parent = _resolve_op(parent_param) if parent_param else targets[0].parent()
	else:
		targets = None  # let COMP.layout() default to laying out all of parent's children
		parent = _resolve_op(parent_param) if parent_param else _get_current_network()

	if not parent.isCOMP:
		raise ValueError(f"'{parent.path}' is not a COMP, can't layout its children")

	if horizontal:
		layout_kwargs = {'horizontal': True}
	elif vertical:
		layout_kwargs = {'vertical': True}
	elif grid_rows:
		layout_kwargs = {'gridRows': grid_rows}
	else:
		layout_kwargs = {'horizontal': True}  # default: arrange left-to-right along the wiring

	if targets is not None:
		parent.layout(targets, **layout_kwargs)
		result_nodes = targets
	else:
		parent.layout(**layout_kwargs)
		result_nodes = _nodes_in_comp(parent)

	_ok_json(response, {
		'parent': parent.path,
		'mode': next(iter(layout_kwargs)),
		'count': len(result_nodes),
		'nodes': [_node_summary(n) for n in sorted(result_nodes, key=lambda n: n.path)],
	})


def _route_delete(request, response, pars, webServerDAT, **_):
	if request['method'] not in ('POST', 'DELETE'):
		raise MethodNotAllowedError("/delete requires POST or DELETE")
	paths_param = _first_param(pars, 'paths') or _first_param(pars, 'path')
	if not paths_param:
		raise ValueError("missing required 'path' or 'paths' query parameter")
	targets = [_resolve_op(p.strip()) for p in paths_param.split(',') if p.strip()]
	bridge = webServerDAT.parent()
	sandbox = (_comp_par(bridge, 'Sandboxpath', '') or '').rstrip('/')
	in_sandbox = bool(sandbox) and all(n.path == sandbox or n.path.startswith(sandbox + '/') for n in targets)
	if _comp_par(bridge, 'Confirmdelete', False) and not in_sandbox:
		names = ', '.join(n.path for n in targets[:8]) + (' ...' if len(targets) > 8 else '')
		choice = ui.messageBox('Agent wants to delete', f'An agent asked to delete {len(targets)} operator(s):\n\n{names}',
			buttons=['Delete', 'Bypass instead', 'Keep'])
		if choice == 1:
			for n in targets:
				if hasattr(n, 'bypass'):
					n.bypass = True
			_ok_json(response, {'deleted': [], 'bypassed': [n.path for n in targets], 'userChoice': 'bypass'})
			return
		if choice != 0:
			raise BridgeError('the user declined this delete', code='delete_declined', status=409,
				hint='Ask the user before trying again.')
	deleted = [n.path for n in targets]
	for n in targets:
		n.destroy()
	_ok_json(response, {'deleted': deleted})


def _route_bounds(request, response, pars, **_):
	paths_param = _first_param(pars, 'paths')
	comp_path = _first_param(pars, 'path')
	if paths_param:
		targets = [_resolve_op(p.strip()) for p in paths_param.split(',') if p.strip()]
	else:
		comp = _resolve_op(comp_path) if comp_path else _get_current_network()
		targets = _nodes_in_comp(comp)
	if not targets:
		raise ValueError("no nodes found to compute bounds for")
	_ok_json(response, _bounds_of(targets))


def _route_errors(request, response, pars, **_):
	paths_param = _first_param(pars, 'paths')
	recursive = _bool_param(pars, 'recursive')
	if paths_param:
		# explicit targets: always return full state (errors AND clean), so a caller
		# checking "did my fix resolve this" gets an explicit confirmation, not silence.
		targets = [_resolve_op(p.strip()) for p in paths_param.split(',') if p.strip()]
		force = _bool_param(pars, 'force', True)
	else:
		comp_path = _first_param(pars, 'path')
		comp = _resolve_op(comp_path) if comp_path else _get_current_network()
		targets = comp.findChildren(includeUtility=True) if recursive else _nodes_in_comp(comp)
		# Network boxes are COMPs with their own internal ops; their errors are TD's business.
		targets = [n for n in targets if not any(a.OPType == 'annotateCOMP' for a in _ancestors(n, comp))]
		force = _bool_param(pars, 'force', False)
	if force:
		for n in targets:
			n.cook(force=True)
	results = [_op_error_state(n) for n in targets]
	if not paths_param and not _bool_param(pars, 'all'):
		results = [r for r in results if r['errors'] or r['warnings']]
	for r in results:
		_attach_compile_log(r)
	_ok_json(response, results)


def _ancestors(n, stop):
	"""Parents of n up to (not including) stop."""
	out, p = [], n.parent()
	while p is not None and p != stop and p.path != '/':
		out.append(p)
		p = p.parent()
	return out


def _attach_compile_log(result):
	"""GLSL ops report a bare 'compile error'; the actual shader log lives in their info DAT.
	Attach it so an agent can fix the shader without a second call."""
	if not (result['errors'] or result['warnings']):
		return
	n = op(result['path'])
	if n is None or not n.OPType.startswith('glsl'):
		return
	try:
		info = n.parent().create(infoDAT, '_tdapi_glsl_info')
		try:
			info.par.op = n
			info.cook(force=True)
			text = info.text.strip()
		finally:
			info.destroy()
		if text:
			result['compileLog'] = text[-4000:]
	except Exception as e:
		result['compileLog'] = f'(could not read the GLSL info log: {e})'


def _route_logs(request, response, pars, **_):
	"""Recent calls, oldest first: ?limit=50. since=<id> returns only newer entries;
	format=feed wraps them as {epoch, entries} (epoch changes when the module reloads)."""
	limit = int(_first_param(pars, 'limit', '50'))
	since = _first_param(pars, 'since')
	entries = [e for e in _REQUEST_LOG if since is None or e.get('id', 0) > int(since)]
	entries = entries[-limit:]
	if _first_param(pars, 'format') == 'feed':
		_ok_json(response, {'epoch': _LOG_EPOCH, 'entries': entries})
	else:
		_ok_json(response, entries)


def _route_run(request, response, pars, webServerDAT, **_):
	_require_write_method(request, '/run')
	code = request.get('data', '') or ''
	if not code:
		raise ValueError("missing required script body inside POST/PUT data")
	old_stdout, old_stderr = sys.stdout, sys.stderr
	buf = io.StringIO()
	sys.stdout = sys.stderr = buf
	error = None
	try:
		exec(code, {'op': op, 'ops': ops, 'ui': ui, 'project': project, 'me': webServerDAT})
	except Exception:
		error = traceback.format_exc()
	finally:
		sys.stdout, sys.stderr = old_stdout, old_stderr
	result = {'output': buf.getvalue()}
	if error:
		result['error'] = error
	_ok_json(response, result)


def _route_diff(request, response, pars, **_):
	_require_write_method(request, '/diff')
	body = request.get('data', '') or ''
	try:
		payload = json.loads(body)
	except Exception as e:
		raise ValueError(f"body must be JSON: {e}")
	before = payload.get('before')
	after = payload.get('after')
	if before is None or after is None:
		raise ValueError("body must be JSON with both 'before' and 'after' network snapshots (each a /network response)")
	_ok_json_sorted(response, diff_networks(before, after))


def _route_pars(request, response, pars, **_):
	"""GET /pars?path=<node> returns exhaustive parameter catalog for that node."""
	target = _resolve_op(_first_param(pars, 'path'))
	result = {}
	for par in target.pars('*'):
		entry = {
			'label': par.label,
			'page': par.page.name if hasattr(par, 'page') and par.page else None,
			'mode': par.mode.name,
			'default': _par_json_value(par) if par.isDefault else None,
		}
		if par.isOP:
			entry['isOP'] = True
		if par.isInt:
			entry['isInt'] = True
		elif par.isFloat:
			entry['isFloat'] = True
		elif par.isToggle:
			entry['isToggle'] = True
		elif par.isMenu:
			entry['isMenu'] = True
			try:
				entry['menuNames'] = par.menuNames
				entry['menuLabels'] = par.menuLabels
			except Exception:
				pass
		result[par.name] = entry
	_ok_json_sorted(response, result)


def _route_opinfo(request, response, pars, **_):
	"""Schema of an operator type (?opType=noiseTOP): summary and every parameter (name, label,
	type) from TD's own help file, OP-reference pars, and -- from a short-lived probe op, skip with
	probe=false -- wire input count and menu values (menus=true). summaries=true adds each par's
	help text."""
	opType = _first_param(pars, 'opType')
	if not opType:
		raise BridgeError("missing required 'opType' query parameter", code='missing_param', param='opType')
	help_entry = _op_type_help(opType)
	if help_entry is None and opType not in _all_op_types():
		raise BridgeError(f"unknown operator type '{opType}'", code='invalid_op_type',
			suggestions=_suggest(opType, _all_op_types()) or None)
	result = {'opType': opType}
	op_families = ('OP', 'COMP', 'TOP', 'CHOP', 'SOP', 'DAT', 'MAT', 'POP', 'Object', 'PanelCOMP')
	if help_entry:
		with_summaries = _bool_param(pars, 'summaries')
		result['summary'] = help_entry.get('summary', '')
		params = {}
		for name, info in (help_entry.get('parameters') or {}).items():
			entry = {'label': info.get('label'), 'type': info.get('parType')}
			if with_summaries and info.get('summary'):
				entry['summary'] = info['summary']
			params[name] = entry
		result['parameters'] = params
		result['opRefParams'] = [{'name': n, 'label': p['label'], 'allowMultiple': False}
			for n, p in params.items() if p['type'] in op_families]
	if _bool_param(pars, 'probe', True):
		# Wire inputs and live menu values aren't in the help file; read them from a probe op.
		try:
			dummy = op('/').create(opType, '_schema_probe')
		except Exception as e:
			raise BridgeError(f"cannot create operator of type '{opType}': {e}", code='invalid_op_type')
		try:
			result['wireInputCount'] = len(dummy.inputConnectors) if hasattr(dummy, 'inputConnectors') else 0
			if 'opRefParams' not in result:
				result['opRefParams'] = [{'name': p.name, 'label': p.label, 'allowMultiple': False}
					for p in dummy.pars('*') if p.isOP]
			if _bool_param(pars, 'menus'):
				result['menus'] = {p.name: list(p.menuNames or []) for p in dummy.pars('*') if p.isMenu and p.style == 'Menu'}
		finally:
			dummy.destroy()
	_ok_json_sorted(response, result)


# Dispatch table - maps URI → handler function.
_ROUTES = {
	'/network':              _route_network,
	'/network.mmd':          _route_network_mmd,
	'/selected':             _route_selected,
	'/select':               _route_select,
	'/insert':               _route_insert,
	'/reload':               _route_reload,
	'/save':                 _route_save,
	'/save-external-tox':    _route_save_external_tox,
	'/export-release':       _route_export_release,
	'/health':               _route_health,
	'/server-info':          _route_server_info,
	'/project':              _route_project,
	'/init':                 _route_init,
	'/docs':                 _route_docs,
	'/examples':             _route_examples,
	'/examples.tsv':         _route_examples_tsv,
	'/examples-refresh':     _route_examples_refresh,
	'/help':                 _route_help,
	'/cache':                _route_cache,
	'/smoketest':            _route_smoketest,
	'/routes':               _route_routes,
	'/schema':               _route_schema,
	'/cookstats':            _route_cookstats,
	'/snapshot':             _route_snapshot,
	'/chop':                 _route_chop,
	'/dat':                  _route_dat,
	'/par':                  _route_par,
	'/pulse':                _route_pulse,
	'/build':                _route_build,
	'/sample':               _route_sample,
	'/job':                  _route_job,
	'/parsearch':            _route_parsearch,
	'/custompars':           _route_custompars,
	'/pars':                 _route_pars,
	'/opinfo':               _route_opinfo,
	'/create':               _route_create,
	'/wire':                 _route_wire,
	'/duplicate':            _route_duplicate,
	'/comment':              _route_comment,
	'/bypass':               _route_bypass,
	'/flag':                 _route_flag,
	'/annotate':             _route_annotate,
	'/move':                 _route_move,
	'/layout':               _route_layout,
	'/delete':               _route_delete,
	'/bounds':               _route_bounds,
	'/errors':               _route_errors,
	'/logs':                 _route_logs,
	'/run':                  _route_run,
	'/diff':                 _route_diff,
}

_KNOWN_ROUTES = ', '.join(sorted(_ROUTES))


# ---------------------------------------------------------------------------
# Component setup: port selection, label, viewer text, agent prompt
# ---------------------------------------------------------------------------
# Several TD apps can each run a bridge: every one starts at its Port par and takes the first
# free port within Port Range, so a second app opening the same .tox lands on 3032, and so on.
# Agents find them by scanning that range with /server-info (see /init).

DEFAULT_PORT = 3031
DEFAULT_PORT_RANGE = 10


def _port_free(port):
	"""True if nothing on this machine is listening on `port` (exclusive bind test)."""
	import socket
	s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	try:
		if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):	# Windows: also fail if another socket shares the port
			s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
		s.bind(('127.0.0.1', port))
		return True
	except OSError:
		return False
	finally:
		s.close()


def _callbacks_dat(comp):
	"""The DAT holding this route code, whatever it's named: the Web Server DAT's Callbacks DAT
	(named td_http_api in current toxes, callbacks in older ones)."""
	ws = comp.op('webserver')
	dat = ws.par.callbacks.eval() if ws is not None else None
	return dat or comp.op('td_http_api') or comp.op('callbacks')


def _package_dir(comp):
	"""Project-relative folder of this bridge's source files, read from the callbacks DAT's own
	synced file ('src/td_http_api.py' -> 'src'). None for an embedded copy with no files."""
	cb = _callbacks_dat(comp)
	if cb is None or not getattr(cb.par, 'syncfile', None) or not cb.par.syncfile.eval():
		return None
	f = (cb.par.file.eval() or '').replace('\\', '/')
	return os.path.dirname(f) if f else None


def _comp_label(comp):
	par = getattr(comp.par, 'Label', None)
	# Default: project name without TD's save counter and extension ('haxlib.2563.toe' -> 'haxlib').
	return (par.eval() if par is not None else '') or re.sub(r'(\.\d+)?\.toe$', '', project.name)


def agent_prompt(port, label):
	"""One-line prompt to paste into an agent: points it at this bridge and its /init guide."""
	return (f"We're working on a live TouchDesigner project ('{label}') through the td-http-api bridge at "
		f"http://127.0.0.1:{port}. Start by fetching http://127.0.0.1:{port}/init and following it.")


# Tooltip text for every custom par on the bridge COMP (hover a par's name in TD to see it).
# setup_component() applies it, so edit it here.
PAR_HELP = {
	# TdHttpApi page
	'Label': ('A name for this TD app, shown in the COMP viewer and to agents (/server-info). Set it when '
		'several TD apps run the bridge at once, so you can tell your agent which one to use. '
		'Blank = the project file name.'),
	'Port': ('The first port the bridge tries (default 3031). If another app already uses it, the bridge '
		'takes the next free one. The port it actually got is shown in Active Port and in the viewer.'),
	'Portrange': 'How many ports to try, starting at Port (default 10, so ports 3031-3040).',
	'Restart': ('Stop the bridge and start it again on the first free port. Use it if the viewer says '
		'"stopped" or agents can\'t connect.'),
	'Activeport': ('Read-only. The port the bridge is listening on right now (0 = not running). Agents connect '
		'to http://127.0.0.1:<this port>.'),
	'Agentprompt': ('Read-only. A one-line message to paste into your coding agent (Claude Code, Codex, ...). '
		'It points the agent at this app\'s /init page, which explains how to work with the bridge. '
		'Copy Prompt copies the longer full version.'),
	'Copyprompt': ('Copy the full agent start-up prompt, with this app\'s port filled in, to the clipboard. '
		'Paste it as the first message to your coding agent.'),
	'Opendashboard': ('Open the bridge\'s web dashboard in your browser: a live feed of what agents are doing '
		'and what they changed, how the bridge works, and every route with a Try button.'),
	'Printlogs': ('Print one line to the Textport for every request an agent makes (route, status, time '
		'taken). For debugging; the dashboard shows the same activity.'),
	'Savetoxwithproject': ('When on, saving the project also saves this COMP to its External .tox file. For '
		'developing the bridge itself; leave it off otherwise. Release toxes have it off.'),
	'Version': 'Read-only. Which td-http-api release this COMP is.',
	# Agent page
	'Followflash': ('Briefly tint each node an agent changes (amber), then restore its color, so you can see '
		'what it touched. Not recorded in undo.'),
	'Flashtime': 'How long the Flash Changed Nodes tint lasts, in seconds.',
	'Followpan': ('Also move the network editor to the first node an agent changes. Off by default because '
		'it takes over your view.'),
	'Slowthreshold': ('Protects TD from being overloaded: after a request that took longer than this many '
		'seconds, the bridge refuses new requests for Cooldown seconds and tells the agent to retry '
		'later. 0 = off.'),
	'Cooldown': 'How many seconds the bridge refuses new requests after a slow one (see Slow Call Threshold).',
	'Confirmdelete': ('Ask you before an agent deletes operators. A dialog offers Delete, Bypass instead, or '
		'Keep, and the agent is told what you chose. Deletes inside Sandbox Path never ask.'),
	'Sandboxpath': ('A COMP where agents can delete freely (tests, scratch builds), even with Confirm Agent '
		'Deletes on.'),
}


def setup_component(comp, dev=None):
	"""Create (or update) the bridge COMP's custom pars and its startup DATs. Idempotent.
	dev: include Save Tox With Project and its post-save hook, which only make sense while developing
	the bridge. Defaults to whether the route code is file-synced; /export-release passes False."""
	if dev is None:
		dev = _package_dir(comp) is not None
	page =next((p for p in comp.customPages if p.name == 'TdHttpApi'), None) or comp.appendCustomPage('TdHttpApi')

	def ensure(name, make, value=None):
		par = getattr(comp.par, name, None)
		if par is not None:
			return par
		par = make()[0]
		if value is not None:
			par.val = value	# before any clamp settings, which would pin a fresh 0 to the minimum
		return par

	p = ensure('Label', lambda: page.appendStr('Label', label='App Label'))
	p = ensure('Port', lambda: page.appendInt('Port', label='Port'), DEFAULT_PORT)
	p.default = DEFAULT_PORT
	p.normMin, p.normMax = 1024, 65535
	p.clampMin = p.clampMax = True
	p.min, p.max = 1024, 65535
	p = ensure('Portrange', lambda: page.appendInt('Portrange', label='Port Range'), DEFAULT_PORT_RANGE)
	p.default = DEFAULT_PORT_RANGE
	p.normMin, p.normMax = 1, 50
	p.clampMin = True
	p.min = 1
	p = ensure('Restart', lambda: page.appendPulse('Restart', label='Restart Server'))
	p = ensure('Activeport', lambda: page.appendInt('Activeport', label='Active Port'))
	p.readOnly = True
	p = ensure('Agentprompt', lambda: page.appendStr('Agentprompt', label='Agent Prompt'))
	p.readOnly = True
	p = ensure('Copyprompt', lambda: page.appendPulse('Copyprompt', label='Copy Prompt'))
	p = ensure('Opendashboard', lambda: page.appendPulse('Opendashboard', label='Open Dashboard'))
	old = getattr(comp.par, 'Exportagentkit', None)	# Export Agent Kit pulse, removed Nov 2026
	if old is not None:
		old.destroy()
	ensure('Printlogs', lambda: page.appendToggle('Printlogs', label='Print Logs'), False)
	if dev:
		ensure('Savetoxwithproject', lambda: page.appendToggle('Savetoxwithproject', label='Save Tox With Project'))
	elif getattr(comp.par, 'Savetoxwithproject', None) is not None:
		comp.par.Savetoxwithproject.destroy()
	p = ensure('Version', lambda: page.appendStr('Version', label='Version'))
	p.readOnly = True
	page.sort(*[n for n in ('Label', 'Port', 'Portrange', 'Restart', 'Activeport', 'Agentprompt', 'Copyprompt',
		'Opendashboard', 'Printlogs', 'Savetoxwithproject', 'Version') if getattr(comp.par, n, None) is not None])

	html = comp.op('dashboard_html') or comp.create(textDAT, 'dashboard_html')
	html.nodeX, html.nodeY = 300, -300
	# While developing, the page is file-synced next to the route code (the saved .tox embeds the
	# text). Never override a file the developer already set; an embedded copy with no synced
	# route file keeps its embedded page.
	pkg = _package_dir(comp)
	if not html.par.file.eval() and pkg is not None:
		html.par.file = (f'{pkg}/' if pkg else '') + 'TdHttpApi_dashboard.html'
		html.par.syncfile = True

	old_follow = next((p for p in comp.customPages if p.name == 'Follow'), None)	# pre-release page name
	if old_follow is not None:
		old_follow.destroy()
	follow = next((p for p in comp.customPages if p.name == 'Agent'), None) or comp.appendCustomPage('Agent')
	p = ensure('Followflash', lambda: follow.appendToggle('Followflash', label='Flash Changed Nodes'), True)
	p.default = True
	p = ensure('Flashtime', lambda: follow.appendFloat('Flashtime', label='Flash Time (s)'), 0.8)
	p.default = 0.8
	p.normMin, p.normMax = 0.1, 3.0
	p.clampMin = True
	p.min = 0.1
	p = ensure('Followpan', lambda: follow.appendToggle('Followpan', label='Pan To Changes'), False)
	p = ensure('Slowthreshold', lambda: follow.appendFloat('Slowthreshold', label='Slow Call Threshold (s)'), 5.0)
	p.default = 5.0
	p.normMin, p.normMax = 0.0, 20.0
	p = ensure('Cooldown', lambda: follow.appendFloat('Cooldown', label='Cooldown (s)'), 1.0)
	p.default = 1.0
	p.normMin, p.normMax = 0.0, 10.0
	p = ensure('Confirmdelete', lambda: follow.appendToggle('Confirmdelete', label='Confirm Agent Deletes'), False)
	p = ensure('Sandboxpath', lambda: follow.appendStr('Sandboxpath', label='Sandbox Path'), '/project1/_agent_sandbox')
	p.default = '/project1/_agent_sandbox'

	# The startup DATs call into this module by the route-code DAT's actual name.
	cb = _callbacks_dat(comp)
	mod = f"op('{cb.name if cb is not None else 'td_http_api'}').module"
	start = comp.op('execute_start') or comp.create(executeDAT, 'execute_start')
	start.text = (
		"# Pick a free port and start the bridge -- see td_http_api.py start_server()\n"
		f"def onStart():\n\t{mod}.start_server(parent())\n\n"
		f"def onCreate():\n\t{mod}.start_server(parent())\n"
	)
	if dev:
		start.text += (
			"\n# Save Tox With Project: re-save this COMP's external .tox after every project save\n"
			f"def onProjectPostSave():\n\t{mod}.on_project_save(parent())\n"
		)
	start.par.start = True
	start.par.create = True
	if getattr(start.par, 'projectpostsave', None) is not None:	# builds before project save callbacks lack it
		start.par.projectpostsave = dev
	start.nodeX, start.nodeY = -150, 0
	pe = comp.op('parexec_server') or comp.create(parameterexecuteDAT, 'parexec_server')
	pe.text = (
		"# Port changes restart the bridge; Label only updates the viewer/prompt\n"
		"def onValueChange(par, prev):\n"
		f"\tm = {mod}\n"
		"\tif par.name == 'Label':\n\t\tm.refresh_labels(parent())\n"
		"\telse:\n\t\tm.start_server(parent())\n\n"
		"def onPulse(par):\n"
		f"\tm = {mod}\n"
		"\tif par.name == 'Copyprompt':\n\t\tm.copy_prompt(parent())\n"
		"\telif par.name == 'Opendashboard':\n\t\tm.open_dashboard(parent())\n"
		"\telse:\n\t\tm.start_server(parent())\n"
	)
	pe.par.op = comp
	pe.par.pars = 'Label Port Portrange Restart Copyprompt Opendashboard'
	pe.par.custom = True
	pe.par.builtin = False
	pe.par.valuechange = True
	pe.par.onpulse = True
	pe.nodeX, pe.nodeY = -150, -100

	for name, text in PAR_HELP.items():
		par = getattr(comp.par, name, None)
		if par is not None:
			par.help = text

	refresh_labels(comp)


def refresh_labels(comp):
	"""Agent Prompt par + the COMP viewer (robot, :port, label), so a developer can tell an agent
	which running app is which."""
	port = getattr(comp.par, 'Activeport', None)
	port = port.eval() if port is not None else 0
	label = _comp_label(comp)
	if getattr(comp.par, 'Version', None) is not None and comp.par.Version.eval() != VERSION:
		comp.par.Version = VERSION
	if getattr(comp.par, 'Agentprompt', None) is not None:
		comp.par.Agentprompt = agent_prompt(port, label) if port else '(server not running)'
	_update_viewer(comp, port, label)


_VIEWER_SIZE = 256
_VIEWER_FONT = "'Segoe UI', 'Helvetica Neue', Helvetica, Arial, sans-serif"


def viewer_svg(logo_svg, port, label, size=_VIEWER_SIZE, version=VERSION):
	"""The COMP viewer image as SVG text: the logo (any SVG; its viewBox is kept) above ':port' and
	the app label, with the bridge version small in the top-right corner."""
	s = size
	m = re.search(r'<svg\b([^>]*)>(.*)</svg>', logo_svg or '', re.S | re.I)
	logo = ''
	if m:
		vb = re.search(r'viewBox\s*=\s*"([^"]+)"', m.group(1))
		if vb is None:	# fall back to width/height for a logo without a viewBox
			w = re.search(r'\bwidth\s*=\s*"([\d.]+)', m.group(1))
			h = re.search(r'\bheight\s*=\s*"([\d.]+)', m.group(1))
			view_box = f'0 0 {w.group(1) if w else 100} {h.group(1) if h else 100}'
		else:
			view_box = vb.group(1)
		logo = (f'<svg x="{s * 0.2:.0f}" y="{s * 0.13:.0f}" width="{s * 0.6:.0f}" height="{s * 0.36:.0f}" '
			f'viewBox="{view_box}" preserveAspectRatio="xMidYMid meet">{m.group(2)}</svg>')
	short = label if len(label) <= 20 else label[:19] + '…'
	esc = _html.escape
	port_text = f':{port}' if port else 'stopped'
	version_text = (f'<text x="{s * 0.95:.0f}" y="{s * 0.09:.0f}" text-anchor="end" font-family="{_VIEWER_FONT}" '
		f'font-size="{s * 0.065:.0f}" fill="#ffffff" fill-opacity="0.45">v{esc(version)}</text>') if version else ''
	# Self-contained on purpose: the palette SVG COMP applies its background color and fit-to-size
	# with JavaScript after load, which doesn't reliably run in a freshly loaded tox. So the image
	# paints its own black background and fills the Web Render TOP at any resolution.
	return (f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%" viewBox="0 0 {s} {s}">'
		f'<rect x="0" y="0" width="{s}" height="{s}" fill="#000000"/>'
		f'{logo}{version_text}'
		f'<line x1="{s * 0.38:.0f}" y1="{s * 0.6:.0f}" x2="{s * 0.62:.0f}" y2="{s * 0.6:.0f}" stroke="#ffffff" '
		f'stroke-opacity="0.35" stroke-width="{s / 128:.0f}" stroke-linecap="round"/>'
		f'<text x="{s / 2:.0f}" y="{s * 0.77:.0f}" text-anchor="middle" font-family="{_VIEWER_FONT}" '
		f'font-size="{s * 0.14:.0f}" font-weight="600" fill="#ffffff">{esc(port_text)}</text>'
		f'<text x="{s / 2:.0f}" y="{s * 0.9:.0f}" text-anchor="middle" font-family="{_VIEWER_FONT}" '
		f'font-size="{s * 0.095:.0f}" fill="#ffffff" fill-opacity="0.7">{esc(short)}</text>'
		'</svg>')


def _update_svg_viewer(comp, port, label):
	"""Point the COMP viewer at the SVG renderer (the palette SVG COMP named 'SVG', whose Web Render
	TOP loads its File par) with the logo from text_logo_svg plus port and label, as a base64 data
	URL. Returns False when either piece is missing, so the caller falls back to the Text TOPs."""
	svg_comp, logo = comp.op('SVG'), comp.op('text_logo_svg')
	if svg_comp is None or logo is None or getattr(svg_comp.par, 'File', None) is None:
		return False
	data = base64.b64encode(viewer_svg(logo.text, port, label).encode('utf-8')).decode('ascii')
	url = f'data:image/svg+xml;base64,{data}'
	if svg_comp.par.File.eval() != url:
		svg_comp.par.File = url
	out = svg_comp.op('out_SVG') or svg_comp
	if comp.par.opviewer.eval() != out:
		comp.par.opviewer = out
	return True


def _update_viewer(comp, port, label):
	"""COMP viewer: the SVG logo + ':port' + label when the SVG renderer is present, else a big robot
	(text_robot) with a small ':port' + label (text_label) laid over it by over_viewer."""
	if _update_svg_viewer(comp, port, label):
		return
	robot = comp.op('text_robot')
	if robot is None:
		return
	robot.par.specdat = ''
	robot.par.text.expr = ''
	robot.par.text = '\U0001F916'
	robot.par.fontautosize = 'off'
	robot.par.fontsizexunit = 'pixels'
	robot.par.fontsizex = 150
	robot.par.alignx = 'center'
	robot.par.aligny = 'center'
	robot.par.positionunit = 'pixels'
	robot.par.positiony = 30	# leave the bottom third for the label

	text = comp.op('text_label') or comp.create(textTOP, 'text_label')
	text.nodeX, text.nodeY = robot.nodeX, robot.nodeY - 150
	text.par.resolutionw, text.par.resolutionh = robot.width or 256, robot.height or 256
	text.par.bgalpha = 0
	text.par.fontautosize = 'off'
	text.par.fontsizexunit = 'pixels'
	text.par.fontsizex = 26
	text.par.alignx = 'center'
	text.par.aligny = 'bottom'
	text.par.positionunit = 'pixels'
	text.par.positiony = 10
	short = label if len(label) <= 20 else label[:19] + '…'
	text.par.text = f"{':' + str(port) if port else 'stopped'}\n{short} · v{VERSION}"

	over = comp.op('over_viewer') or comp.create(overTOP, 'over_viewer')
	over.nodeX, over.nodeY = robot.nodeX + 150, robot.nodeY - 75
	over.inputConnectors[0].connect(text)
	over.inputConnectors[1].connect(robot)
	comp.par.opviewer = over


def init_prompt_text(comp, port):
	"""Full agent-init prompt (the prompt_agent_init DAT) with this instance's port filled in."""
	dat = comp.op('prompt_agent_init')
	text = dat.text if dat is not None else ''
	text = re.sub(r'\A---\n.*?\n---\n', '', text, flags=re.S)	# drop the skill frontmatter
	text = text.replace('127.0.0.1:3031', f'127.0.0.1:{port}')
	header = (f"You are connected to the TouchDesigner app '{_comp_label(comp)}' "
		f"(project {project.name}) on port {port}.\n\n")
	return header + text


def copy_prompt(comp):
	port = comp.par.Activeport.eval() if getattr(comp.par, 'Activeport', None) is not None else 0
	if not port:
		ui.status = 'TdHttpApi: server not running, nothing copied'
		return
	ui.clipboard = init_prompt_text(comp, port)
	ui.status = f'TdHttpApi: agent prompt for port {port} copied to the clipboard'


def start_server(comp):
	"""Restart the bridge on the first free port in [Port, Port + Portrange). Deferred a frame at
	a time so it's safe to call from inside a request on this same server. onStart and onCreate
	both fire at project start, so a restart already pending absorbs the second call."""
	# A frame stamp, not a bool: storage is saved with the COMP, so a flag left set in a saved tox
	# (or by a restart that died) must not block every later start. Only a recent one counts.
	pending = comp.fetch('_tdapi_restart_pending', None, search=False)
	if isinstance(pending, int) and not isinstance(pending, bool) and 0 <= absTime.frame - pending < 30:
		return
	comp.store('_tdapi_restart_pending', absTime.frame)
	run(lambda: _stop_then_pick(comp), delayFrames=1)


def _stop_then_pick(comp):
	ws = comp.op('webserver')
	if ws is None:
		comp.unstore('_tdapi_restart_pending')
		return
	ws.par.active = False
	run(lambda: _pick_port_and_start(comp), delayFrames=2)	# let the socket close first


def _pick_port_and_start(comp):
	try:
		_pick_port_and_start_inner(comp)
	finally:
		comp.unstore('_tdapi_restart_pending')


def _pick_port_and_start_inner(comp):
	ws = comp.op('webserver')
	first = int(getattr(comp.par, 'Port', None) and comp.par.Port.eval() or DEFAULT_PORT)
	count = int(getattr(comp.par, 'Portrange', None) and comp.par.Portrange.eval() or DEFAULT_PORT_RANGE)
	port = next((p for p in range(first, first + max(count, 1)) if _port_free(p)), None)
	if port is None:
		print(f'[td_http_api] no free port in {first}-{first + count - 1}; server not started')
		if getattr(comp.par, 'Activeport', None) is not None:
			comp.par.Activeport = 0
	else:
		ws.par.port = port
		ws.par.active = True
		if getattr(comp.par, 'Activeport', None) is not None:
			comp.par.Activeport = port
		if port != first:
			print(f'[td_http_api] port {first} is taken; listening on {port}')
	refresh_labels(comp)


# ---------------------------------------------------------------------------
# Request gate (browser CSRF / DNS rebinding) and undo blocks
# ---------------------------------------------------------------------------

# The Web Server DAT binds 127.0.0.1, so only this machine can connect -- but that includes
# every web page open in a browser on it. Without a gate, any site could POST Python to /run
# with a no-cors fetch. Browsers always send Origin on cross-site POSTs and the attacker's
# hostname in Host after DNS rebinding; curl and agents send neither, so this costs CLI users
# nothing.
_LOOPBACK_NAMES = ('127.0.0.1', 'localhost', '::1')


def _header(request, name):
	"""Header value by case-insensitive name (TD exposes headers as request dict keys)."""
	lower = name.lower()
	for key, value in request.items():
		if isinstance(key, str) and key.lower() == lower:
			return value
	return None


def _host_part(value):
	"""'127.0.0.1:3031' / '[::1]:3031' / 'localhost' -> bare lowercase host name."""
	value = str(value).strip().lower()
	if value.startswith('['):
		return value[1:].split(']')[0]
	return value.rsplit(':', 1)[0] if value.count(':') == 1 else value


def _request_blocked_reason(request):
	"""Why this request must be refused, or None."""
	client = _host_part(request.get('clientAddress') or '127.0.0.1')
	if client not in _LOOPBACK_NAMES and not client.startswith('127.'):
		return f'client {client} is not on this machine'
	host = _header(request, 'Host')
	if host and _host_part(host) not in _LOOPBACK_NAMES:
		return f'Host header {host!r} is not a loopback address (possible DNS rebinding)'
	origin = _header(request, 'Origin')
	if origin is not None:
		origin_host = _host_part(re.sub(r'^[a-z][a-z0-9+.-]*://', '', str(origin).strip().lower()).split('/')[0])
		if origin_host not in _LOOPBACK_NAMES:
			return f'cross-origin request from {origin!r} (browser pages may not drive this bridge)'
	return None


# Routes that mutate the project get a named undo block, so Ctrl+Z in TD reverts an agent's
# change. Read-only POSTs (/diff, /errors, /select, ...) are left out so they don't add empty
# entries to the undo stack.
_UNDO_ROUTES = {
	'/par', '/dat', '/create', '/wire', '/insert', '/duplicate', '/comment',
	'/bypass', '/flag', '/annotate', '/move', '/layout', '/delete', '/run', '/pulse', '/custompars',
	'/build',
}


def _undo_name(request, uri, pars):
	target = _first_param(pars, 'path') or _first_param(pars, 'paths') or _first_param(pars, 'parent') or ''
	return f'Agent {uri} {target}'.strip()[:120]


# ---------------------------------------------------------------------------
# Follow mode: briefly flash the nodes an agent just changed (and optionally pan to them),
# so the person watching TD can see what the agent is doing.
# ---------------------------------------------------------------------------

_TOUCHED = []			# ops a route explicitly reports as changed this call (see _touch)
_FLASH_COLOR = (1.0, 0.62, 0.1)


def _touch(*ops):
	for o in ops:
		if o is not None:
			_TOUCHED.append(o)


def _touched_ops(pars):
	"""Ops changed by this call: whatever the route reported via _touch(), plus any op named in
	the usual path/paths/target query params."""
	found = list(_TOUCHED)
	for key in ('path', 'paths', 'target'):
		raw = _first_param(pars, key)
		for p in str(raw or '').split(','):
			o = op(p.strip()) if p.strip() else None
			if o is not None and o.valid:
				found.append(o)
	unique = {}
	for o in found:
		if o.valid and o.path not in unique and o.path != '/':
			unique[o.path] = o
	return list(unique.values())[:30]


def _comp_par(comp, name, default):
	par = getattr(comp.par, name, None)
	return par.eval() if par is not None else default


_FLASH_KEY = '_tdapi_flash'	# op storage: (original colour, restore time) -- survives module reloads


# Undo hygiene: script changes are only recorded inside an undo block or a callback, and a change
# made from a deferred run() is not recorded. So the flash and its restore both run deferred, and
# never touch ui.undo. (Toggling ui.undo.globalState looked like a way to suspend recording, but
# it is not that: it stayed False afterwards. Opening startBlock(enable=False) from a run() call
# left a block open that swallowed undo for ~20 minutes.)

def _follow(comp, ops):
	if not ops:
		return
	try:
		if _comp_par(comp, 'Followflash', True):
			hold = max(0.1, _comp_par(comp, 'Flashtime', 0.8))
			paths = [o.path for o in ops]
			run(_start_flash, paths, hold, delayFrames=1)
		if _comp_par(comp, 'Followpan', False):
			_pan_to(ops[0])
	except Exception as e:
		print(f'[td_http_api] follow failed: {e}')	# cosmetic only; never fail the request


def _start_flash(paths, hold):
	until = time.time() + hold
	for path in paths:
		o = op(path)
		if o is None or not o.valid:
			continue
		prev = o.fetch(_FLASH_KEY, None, search=False)
		o.store(_FLASH_KEY, (prev[0] if prev else tuple(o.color), until))
		o.color = _FLASH_COLOR
	run(_restore_flash, paths, delayMilliSeconds=int(hold * 1000) + 30)


def _restore_flash(paths):
	"""Put back each op's pre-flash colour once its hold time has passed (a newer flash on the
	same op extends it). State lives in op storage, not this module, which reloads on edit."""
	now = time.time()
	try:
		for path in paths:
			o = op(path)
			saved = o.fetch(_FLASH_KEY, None, search=False) if o is not None and o.valid else None
			if saved is None:
				continue
			color, until = saved
			if until > now + 0.01:
				continue
			if tuple(round(c, 2) for c in o.color) == tuple(round(c, 2) for c in _FLASH_COLOR):
				o.color = color	# unless something else recoloured it meanwhile
			o.unstore(_FLASH_KEY)
	except Exception as e:
		print(f'[td_http_api] flash restore failed: {e}')


def _pan_to(target):
	pane = _get_network_editor_pane()
	if pane is None or target.parent() is None:
		return
	if pane.owner != target.parent():
		pane.owner = target.parent()
	target.current = True
	pane.homeSelected(zoom=False)


# ---------------------------------------------------------------------------
# /mcp: the same routes as MCP tools (JSON-RPC 2.0 over streamable HTTP; single messages,
# plain JSON responses, no SSE, no sessions). curl access is unchanged.
# ---------------------------------------------------------------------------

_MCP_PROTOCOLS = ('2025-11-25', '2025-06-18', '2025-03-26')
_MCP_SKIP = {'/mcp', '/', '/network.mmd', '/examples.tsv', '/routes', '/schema', '/ui'}
_MCP_DESTRUCTIVE = {'/delete', '/run', '/dat', '/custompars', '/build', '/layout'}
# Dual-mode routes: these arguments mean "write", so the call goes out as POST.
_MCP_WRITE_ARGS = {'/par': ('value', 'expr', 'reset', 'unbind'), '/dat': ('mode',), '/smoketest': ('writeTest',)}

_MCP_INSTRUCTIONS = """You are connected to a live TouchDesigner app '{label}' (project {project}) through the
td-http-api bridge. Every td_* tool is one bridge route; arguments become its query parameters, and
`body` is the request body (an object is sent as JSON).

- Look before you change: td_network (one COMP; recursive=true for subtrees), td_errors, td_par / td_pars,
  td_custompars, td_dat, td_snapshot + td_job (images), td_sample (pixel stats), td_chop.
- Build with td_build (ops + params + connections + auto layout in one call; safe to retry). Set many
  parameters with td_par body {{"set": {{"<path>": {{"<par>": value}}}}}}. Edit code with td_dat mode=edit
  body {{"old", "new"}} rather than rewriting whole DATs.
- Errors come back with a `code` plus `suggestions` / `validValues`; use them instead of guessing.
- Each change is one named undo step the user can Ctrl+Z. Never call ui.undo.undo() yourself.
- td_run executes arbitrary Python on TD's main thread; prefer a dedicated tool when one exists, and
  never block (no sleeps or long loops) -- use background jobs (td_snapshot frames=, td_chop record=).
- TouchDesigner docs (operators, parameters, Python classes) come from this TD's own offline help:
  td_help q= to search, page= / op= / class= to read (section=, par= for one parameter). Use it
  instead of memory.
- Full guide: td_docs. Parameter names: td_pars or td_opinfo, never memory."""


def _mcp_tool_name(uri):
	return 'td_' + re.sub(r'[^a-z0-9]+', '_', uri.strip('/').lower()).strip('_')


def _mcp_tools():
	tools = []
	for uri, fn in sorted(_ROUTES.items()):
		if uri in _MCP_SKIP:
			continue
		doc = inspect.getdoc(fn) or f'{uri} route.'
		methods = _infer_methods(uri, fn)
		props = {}
		for name, spec in (_SCHEMA_OVERRIDES.get(uri, {}).get('query') or {}).items():
			t = spec.get('type', 'string')
			props[name] = {'type': {'boolean': 'boolean', 'integer': 'integer', 'number': 'number'}.get(t, 'string')}
			if 'enum' in spec:
				props[name]['enum'] = spec['enum']
		props['body'] = {'description': 'Request body: JSON object/array (sent as JSON) or raw text. Implies POST.'}
		if methods != ['GET'] and 'GET' in methods:
			props['method'] = {'type': 'string', 'enum': ['GET', 'POST'],
				'description': 'Force GET (read) or POST (write). Default: POST when a body or write argument is given.'}
		read_only = methods == ['GET'] and uri not in _UNDO_ROUTES
		tools.append({
			'name': _mcp_tool_name(uri),
			'title': uri,
			'description': f'{doc}\n\nHTTP: {"/".join(methods)} {uri}. Other arguments are query parameters.',
			'inputSchema': {'type': 'object', 'properties': props, 'additionalProperties': True},
			'annotations': {'readOnlyHint': read_only, 'destructiveHint': uri in _MCP_DESTRUCTIVE,
				'idempotentHint': read_only or uri in ('/build', '/par'), 'openWorldHint': False},
		})
	return tools


def _mcp_call(webServerDAT, name, args):
	"""Run one route for tools/call. Returns an MCP CallToolResult."""
	uri = next((u for u in _ROUTES if _mcp_tool_name(u) == name and u not in _MCP_SKIP), None)
	if uri is None:
		return {'isError': True, 'content': [{'type': 'text', 'text': json.dumps({'error': f"unknown tool '{name}'",
			'code': 'unknown_tool', 'suggestions': _suggest(name, [_mcp_tool_name(u) for u in _ROUTES])})}]}
	args = dict(args or {})
	body = args.pop('body', None)
	method = args.pop('method', None)
	methods = _infer_methods(uri, _ROUTES[uri])
	if method is None:
		wants_write = body is not None or any(k in args for k in _MCP_WRITE_ARGS.get(uri, ()))
		method = 'POST' if (methods == ['POST', 'PUT'] or (wants_write and 'POST' in methods)) else 'GET'
	if isinstance(body, (dict, list)):
		body = json.dumps(body)
	query = {}
	for k, v in args.items():
		if isinstance(v, bool):
			v = 'true' if v else 'false'
		elif isinstance(v, (list, tuple)):
			v = ','.join(str(x) for x in v)
		elif isinstance(v, (dict,)):
			v = json.dumps(v)
		query[k] = str(v)
	retry = _cooldown_remaining(uri)
	if retry:
		return {'isError': True, 'content': [{'type': 'text', 'text': json.dumps({'error': 'bridge cooling down after a '
			'slow call; retry this exact call', 'code': 'cooldown', 'retryAfter': round(retry, 2)})}]}
	request = {'method': method, 'uri': uri, 'pars': query, 'data': body or '', 'clientAddress': '127.0.0.1',
		'_source': 'mcp'}
	response = {}
	start = time.time()
	meta = _dispatch(webServerDAT, request, response, uri, query)
	_note_call_duration(webServerDAT.parent(), uri, time.time() - start)
	_record_call(request, response, uri, query, (time.time() - start) * 1000, meta, tool=name)
	data = response.get('data', '')
	ctype = response.get('content-type', '') or ''
	is_error = (response.get('statusCode') or 200) >= 400
	if ctype.startswith('image/'):
		import base64
		return {'isError': is_error, 'content': [{'type': 'image', 'mimeType': ctype,
			'data': base64.b64encode(bytes(data)).decode('ascii')}]}
	if isinstance(data, (bytes, bytearray)):
		data = data.decode('utf-8', errors='replace')
	return {'isError': is_error, 'content': [{'type': 'text', 'text': str(data)}]}


def _route_mcp(request, response, pars, webServerDAT, **_):
	"""MCP endpoint (JSON-RPC 2.0): initialize, tools/list, tools/call, ping. Point an MCP client
	at http://127.0.0.1:<port>/mcp (transport: http). Every other route is a td_* tool."""
	if request['method'] != 'POST':
		raise MethodNotAllowedError('/mcp accepts POST only (no SSE stream)')
	ctype = (_header(request, 'Content-Type') or '').lower()
	if 'application/json' not in ctype:
		raise BridgeError('/mcp needs Content-Type: application/json', code='unsupported_media_type', status=415)
	msg = _body_json(request)
	request['_mcp_method'] = msg.get('method') if isinstance(msg, dict) else 'batch'
	if isinstance(msg, list):
		_ok_json(response, {'jsonrpc': '2.0', 'id': None,
			'error': {'code': -32600, 'message': 'JSON-RPC batches are not supported; send one message per request'}})
		return
	method, msg_id, params = msg.get('method'), msg.get('id'), msg.get('params') or {}
	if msg_id is None:	# notification (e.g. notifications/initialized): accepted, no body
		response['statusCode'] = 202
		response['statusReason'] = 'Accepted'
		response['data'] = ''
		return
	comp = webServerDAT.parent()
	try:
		if method == 'initialize':
			asked = params.get('protocolVersion')
			result = {
				'protocolVersion': asked if asked in _MCP_PROTOCOLS else _MCP_PROTOCOLS[0],
				'capabilities': {'tools': {'listChanged': False}},
				'serverInfo': {'name': 'td-http-api', 'title': f'TouchDesigner ({_comp_label(comp)})', 'version': VERSION},
				'instructions': _MCP_INSTRUCTIONS.format(label=_comp_label(comp), project=project.name),
			}
		elif method == 'ping':
			result = {}
		elif method == 'tools/list':
			result = {'tools': _mcp_tools()}
		elif method == 'tools/call':
			result = _mcp_call(webServerDAT, params.get('name'), params.get('arguments'))
		elif method in ('resources/list', 'prompts/list', 'resources/templates/list'):
			result = {method.split('/')[0]: []} if method != 'resources/templates/list' else {'resourceTemplates': []}
		else:
			_ok_json(response, {'jsonrpc': '2.0', 'id': msg_id, 'error': {'code': -32601, 'message': f'method not found: {method}'}})
			return
	except Exception as e:
		_ok_json(response, {'jsonrpc': '2.0', 'id': msg_id, 'error': {'code': -32603, 'message': f'{type(e).__name__}: {e}'}})
		return
	_ok_json(response, {'jsonrpc': '2.0', 'id': msg_id, 'result': result})


def _route_ui(request, response, pars, webServerDAT, **_):
	"""The bridge dashboard: live activity feed, how-it-works overview and route reference. Open
	http://127.0.0.1:<port>/ui in a browser (or pulse Open Dashboard on the COMP)."""
	dat = webServerDAT.parent().op('dashboard_html')
	if dat is None or not dat.text.strip():
		raise BridgeError('dashboard_html DAT is missing; pulse Restart or run setup_component()', code='no_dashboard',
			status=404)
	response['statusCode'] = 200
	response['statusReason'] = 'OK'
	response['content-type'] = 'text/html; charset=utf-8'
	response['Cache-Control'] = 'no-store'
	response['data'] = dat.text


def open_dashboard(comp):
	import webbrowser
	port = _comp_par(comp, 'Activeport', 0)
	if not port:
		ui.status = 'TdHttpApi: server not running'
		return
	webbrowser.open(f'http://127.0.0.1:{port}/ui')


_ROUTES['/mcp'] = _route_mcp	# registered here: defined after the main dispatch table
_ROUTES['/ui'] = _route_ui
_KNOWN_ROUTES = ', '.join(sorted(_ROUTES))


# ---------------------------------------------------------------------------
# Dispatch: error mapping, undo, follow mode, slow-call cooldown
# ---------------------------------------------------------------------------

_STATUS_REASONS = {400: 'Bad Request', 403: 'Forbidden', 404: 'Not Found', 405: 'Method Not Allowed',
	409: 'Conflict', 429: 'Too Many Requests', 500: 'Internal Server Error'}


def _error_response(response, status, code, message, **details):
	"""Every error is JSON: {error, code, ...details}. `code` is stable for agents to branch on."""
	response['statusCode'] = status
	response['statusReason'] = _STATUS_REASONS.get(status, 'Error')
	response['content-type'] = 'application/json'
	body = {'error': message, 'code': code}
	body.update({k: v for k, v in details.items() if v is not None})
	response['data'] = json.dumps(body, default=str)


def _dispatch(webServerDAT, request, response, uri, pars):
	"""Run one route with the shared error contract, undo block and follow-mode highlight.
	Used by onHTTPRequest and by /mcp tools/call, so both behave identically."""
	handler = _route_root if uri in ('/', '') else _ROUTES.get(uri)
	if handler is None:
		_error_response(response, 404, 'unknown_route', f"unknown route '{uri}'",
			suggestions=_suggest(uri, list(_ROUTES)) or None, routes=sorted(_ROUTES))
		return
	mutating = uri in _UNDO_ROUTES and request.get('method') != 'GET'
	undo_open = False
	_TOUCHED.clear()
	try:
		if mutating:
			ui.undo.startBlock(_undo_name(request, uri, pars))
			undo_open = True
		handler(request, response, pars, webServerDAT=webServerDAT)
	except MethodNotAllowedError as e:
		_error_response(response, 405, 'method_not_allowed', str(e))
	except BridgeError as e:
		_error_response(response, e.status, e.code, str(e), **e.details)
	except ValueError as e:
		_error_response(response, 400, 'bad_request', str(e))
	except Exception as e:
		tb = traceback.format_exc().strip().splitlines()
		_error_response(response, 500, 'internal_error', f'{type(e).__name__}: {e}', traceback=tb[-6:])
	finally:
		if undo_open:
			try:
				ui.undo.endBlock()
			except Exception:
				pass	# TD also closes dangling blocks at the end of the callback
	meta = {}
	if mutating and response.get('statusCode') == 200:
		meta['undo'] = _undo_name(request, uri, pars)
		touched = _touched_ops(pars)
		meta['touched'] = [o.path for o in touched]
		_follow(webServerDAT.parent(), touched)
	return meta


# Slow-call cooldown: after a call slower than the COMP's Slow Threshold, refuse new calls for
# Cooldown seconds with 429 + retryAfter instead of letting an agent queue more heavy work on
# TD's main thread. Status/identity routes are always answered.
_COOLDOWN_EXEMPT = {'/health', '/server-info', '/logs', '/init', '/', '/mcp', '/ui', '/schema'}	# /mcp checks per tool call
_cooldown_until = 0.0


def _cooldown_remaining(uri):
	if uri in _COOLDOWN_EXEMPT:
		return 0.0
	return max(0.0, _cooldown_until - time.time())


def _note_call_duration(comp, uri, seconds):
	global _cooldown_until
	threshold = getattr(comp.par, 'Slowthreshold', None)
	cooldown = getattr(comp.par, 'Cooldown', None)
	threshold = threshold.eval() if threshold is not None else 5.0
	cooldown = cooldown.eval() if cooldown is not None else 1.0
	if threshold > 0 and seconds > threshold and uri not in _COOLDOWN_EXEMPT:
		_cooldown_until = time.time() + cooldown


def _skip_log(request, uri):
	"""Keep the activity log about real work: no dashboard polling, no MCP plumbing (tools/call
	logs the route it ran instead), no cross-port bridge scans."""
	if _request_source(request) == 'dashboard' and request.get('method') == 'GET':
		return True
	if uri in ('/ui', '/favicon.ico'):
		return True
	if uri == '/mcp' and request.get('_mcp_method') != 'initialize':
		return True
	if uri == '/server-info' and _header(request, 'Origin'):
		return True
	return False


def _allow_loopback_cors(request, response):
	origin = _header(request, 'Origin')
	if origin and not _request_blocked_reason(request):
		response['Access-Control-Allow-Origin'] = origin


# ---------------------------------------------------------------------------
# Web Server DAT callbacks
# ---------------------------------------------------------------------------

def onHTTPRequest(webServerDAT, request, response):
	callback_start = time.time()
	uri = request['uri']
	pars = request.get('pars', {})

	blocked = _request_blocked_reason(request)
	meta = None
	if blocked:
		_error_response(response, 403, 'forbidden_origin', f'request refused: {blocked}')
		print(f'[td_http_api] refused {request.get("method")} {uri}: {blocked}')
	else:
		retry_after = _cooldown_remaining(uri)
		if retry_after:
			_error_response(response, 429, 'cooldown',
				'the previous call was slow, so the bridge is pausing briefly to keep TD responsive. Retry this exact call.',
				retryAfter=round(retry_after, 2))
		else:
			meta = _dispatch(webServerDAT, request, response, uri, pars)
		if uri == '/server-info':
			_allow_loopback_cors(request, response)	# the dashboard scans sibling bridges on other ports

	callback_elapsed_ms = (time.time() - callback_start) * 1000
	_note_call_duration(webServerDAT.parent(), uri, callback_elapsed_ms / 1000.0)
	log_entry = None
	if not _skip_log(request, uri):
		log_entry = _record_call(request, response, uri, pars, callback_elapsed_ms, meta)
	if log_entry and getattr(parent().par, 'Printlogs', None) is not None and parent().par.Printlogs.eval():
		print(f"[td_http_api] {log_entry['method']} {uri} -> {log_entry['statusCode']} "
			f"(callback took {log_entry['callbackElapsedMs']}ms, requestKeys={log_entry['requestKeys']}, dataLen={log_entry['dataLen']})")

	return response


def onWebSocketOpen(webServerDAT, client, uri):
	return


def onWebSocketClose(webServerDAT, client):
	return


def onWebSocketReceiveText(webServerDAT, client, data):
	return


def onWebSocketReceiveBinary(webServerDAT, client, data):
	return


def onServerStart(webServerDAT):
	print(f'[td_http_api] server started: {webServerDAT.path}')
	return


def onServerStop(webServerDAT):
	print(f'[td_http_api] server stopped: {webServerDAT.path}')
	return
