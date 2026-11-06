"""Release helper for the TdHttpApi bridge. Standard library only; needs td-http-api.toe open in TD.

	python scripts/release.py prepare patch|minor|major|X.Y.Z
		Bump VERSION in src/td_http_api.py, wait for TD to reload it, export the clean drop-in tox to
		build/TdHttpApi-vX.Y.Z.tox (+ build/TdHttpApi.tox), re-save the dev tox (src/TdHttpApi.tox),
		turn CHANGELOG.md's "## Unreleased" section into "## vX.Y.Z (date)" and write its text to
		build/release-notes-vX.Y.Z.md. Then review the diff and commit.

	python scripts/release.py publish [--yes]
		After committing: tag vX.Y.Z, push the tag, and `gh release create` with the tox attached.
		Prints the commands and asks first unless --yes.

Options: --port N to use a specific bridge instead of scanning 3031-3040 for this repo's project.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src', 'td_http_api.py')
CHANGELOG = os.path.join(ROOT, 'CHANGELOG.md')
BUILD = os.path.join(ROOT, 'build')
VERSION_RE = re.compile(r"^VERSION = '(\d+)\.(\d+)\.(\d+)'", re.M)


def fail(msg):
	print(f'[release] {msg}')
	sys.exit(1)


def call(port, path, method='GET', timeout=30):
	req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=b'' if method == 'POST' else None, method=method)
	with urllib.request.urlopen(req, timeout=timeout) as r:
		return json.loads(r.read().decode('utf-8'))


def find_bridge(port=None):
	"""(port, server-info) of the bridge running this repo's dev project."""
	root = os.path.normcase(os.path.normpath(ROOT))
	for p in ([port] if port else range(3031, 3041)):
		try:
			info = call(p, '/server-info', timeout=2)
		except (urllib.error.URLError, OSError, ValueError):
			continue
		if port or os.path.normcase(os.path.normpath(info.get('projectFolder', ''))) == root:
			return p, info
	fail(f'no bridge found for {ROOT} on ports 3031-3040: open td-http-api.toe in TouchDesigner')


def read_version():
	m = VERSION_RE.search(open(SRC, encoding='utf-8').read())
	if not m:
		fail(f"no VERSION = 'X.Y.Z' line in {SRC}")
	return tuple(int(x) for x in m.groups())


def next_version(cur, bump):
	if bump == 'major':
		return (cur[0] + 1, 0, 0)
	if bump == 'minor':
		return (cur[0], cur[1] + 1, 0)
	if bump == 'patch':
		return (cur[0], cur[1], cur[2] + 1)
	m = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', bump)
	if not m:
		fail(f"version must be patch, minor, major or X.Y.Z, not '{bump}'")
	new = tuple(int(x) for x in m.groups())
	if new < cur:
		fail(f'{fmt(new)} is older than the current {fmt(cur)}')
	return new


def fmt(v):
	return '.'.join(str(x) for x in v)


def write_version(new):
	raw = open(SRC, 'rb').read()	# bytes: keep the file's CRLF line endings
	out, n = re.subn(rb"^VERSION = '[\d.]+'", f"VERSION = '{new}'".encode(), raw, count=1, flags=re.M)
	if n != 1:
		fail('could not rewrite VERSION')
	open(SRC, 'wb').write(out)


def changelog_release(new, write=True):
	"""Rename '## Unreleased' to '## vX (date)', add a fresh empty Unreleased above it, return notes.
	write=False only validates and returns the notes."""
	text = open(CHANGELOG, encoding='utf-8').read()
	m = re.search(r'^## Unreleased[ \t]*\n(.*?)(?=^## |\Z)', text, re.M | re.S)
	if not m:
		fail('CHANGELOG.md has no "## Unreleased" section')
	notes = m.group(1).strip()
	if not notes:
		fail('CHANGELOG.md "## Unreleased" is empty: write the release notes there first')
	if not write:
		return notes
	heading = f'## v{new} ({datetime.date.today().isoformat()})'
	text = text[:m.start()] + f'## Unreleased\n\n{heading}\n\n{notes}\n\n' + text[m.end():]
	open(CHANGELOG, 'w', encoding='utf-8', newline='\n').write(text.rstrip('\n') + '\n')
	return notes


def prepare(args):
	port, info = find_bridge(args.port)
	cur = read_version()
	new = fmt(next_version(cur, args.bump))
	comp = info['webserver'].rsplit('/', 1)[0]
	print(f"[release] bridge '{info['label']}' on {port} ({comp}); {fmt(cur)} -> {new}")

	changelog_release(new, write=False)	# fail before changing anything if the notes are missing
	if new != fmt(cur):
		write_version(new)
	# TD reloads the file-synced callbacks DAT on change; wait until the running module reports it.
	deadline = time.time() + 20
	while call(port, '/server-info').get('version') != new:
		if time.time() > deadline:
			fail(f'TD did not pick up VERSION {new} within 20 s (is src/td_http_api.py file-synced?)')
		time.sleep(0.5)

	os.makedirs(BUILD, exist_ok=True)
	out = f'build/TdHttpApi-v{new}.tox'
	res = call(port, f'/export-release?output={out}', 'POST')
	shutil.copyfile(os.path.join(ROOT, out), os.path.join(BUILD, 'TdHttpApi.tox'))
	print(f"[release] exported {res['output']} ({res['bytes']} bytes) + build/TdHttpApi.tox")
	res = call(port, f'/save-external-tox?path={comp}', 'POST')
	print(f"[release] re-saved dev tox {res.get('externaltox')} (via {res.get('via', 'saveExternalTox')})")

	notes = changelog_release(new)
	notes_path = os.path.join(BUILD, f'release-notes-v{new}.md')
	open(notes_path, 'w', encoding='utf-8', newline='\n').write(notes + '\n')
	print(f'[release] notes: {os.path.relpath(notes_path, ROOT)}')
	print(f'\nNext (docs/RELEASING.md): check build/TdHttpApi.tox in an empty project, review the diff, then\n'
		f'  git add -A && git commit -m "Release v{new}" && git push origin main\n'
		f'  python scripts/release.py publish')


def run(cmd):
	print('  $ ' + ' '.join(cmd))
	subprocess.run(cmd, cwd=ROOT, check=True)


def publish(args):
	v = fmt(read_version())
	tox = os.path.join('build', f'TdHttpApi-v{v}.tox')
	notes = os.path.join('build', f'release-notes-v{v}.md')
	for f in (tox, notes):
		if not os.path.isfile(os.path.join(ROOT, f)):
			fail(f'{f} is missing: run "python scripts/release.py prepare ..." first')
	dirty = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT,
		capture_output=True, text=True).stdout.strip()
	if dirty:
		fail(f'commit first; uncommitted changes:\n{dirty}')
	if not shutil.which('gh'):
		fail('the GitHub CLI (gh) is not installed')
	cmds = [
		['git', 'tag', '-a', f'v{v}', '-m', f'v{v}'],
		['git', 'push', 'origin', f'v{v}'],
		['gh', 'release', 'create', f'v{v}', tox, '--title', f'v{v}', '--notes-file', notes],
	]
	print('[release] will run:')
	for c in cmds:
		print('  ' + ' '.join(c))
	if not args.yes and input('Proceed? [y/N] ').strip().lower() != 'y':
		fail('cancelled')
	for c in cmds:
		run(c)
	print(f'[release] published v{v}')


def main():
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	sub = ap.add_subparsers(dest='cmd', required=True)
	p = sub.add_parser('prepare', help='bump, export the tox, roll the changelog')
	p.add_argument('bump', help='patch | minor | major | X.Y.Z (the current version is allowed for a first release)')
	p.add_argument('--port', type=int)
	p.set_defaults(func=prepare)
	p = sub.add_parser('publish', help='tag + push + GitHub release (after committing)')
	p.add_argument('--yes', action='store_true', help="don't ask before running git/gh")
	p.set_defaults(func=publish)
	args = ap.parse_args()
	args.func(args)


if __name__ == '__main__':
	main()
