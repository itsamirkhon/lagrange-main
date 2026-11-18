#!/usr/bin/env python3
"""Check whether production is running the latest commit on the remote branch."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = 'https://423in.club/health'
DEFAULT_HOST = 'ubuntu@54.117.166.117'
DEFAULT_KEY = '/Users/ibrohim/Downloads/LightsailDefaultKey-ap-northeast-2.pem'
VERSION_FILE = '/opt/lagrange-rx/app/.lagrange-version'


def git_remote_sha(repo: Path, remote: str, branch: str) -> str:
    out = subprocess.check_output(
        ['git', 'ls-remote', remote, f'refs/heads/{branch}'],
        cwd=repo, text=True, timeout=15,
    ).strip()
    if not out:
        raise RuntimeError(f'Branch {remote}/{branch} not found')
    return out.split()[0]


def git_local_sha(repo: Path) -> str | None:
    try:
        return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True, timeout=5).strip()
    except subprocess.SubprocessError:
        return None


def fetch_health(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers={'User-Agent': 'lagrange-check-prod/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def fetch_ssh_revision(host: str, key: str, version_file: str) -> tuple[str | None, str | None]:
    cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10']
    if key and Path(key).is_file():
        cmd += ['-i', key]
    cmd += [host, f"cat '{version_file}' 2>/dev/null || true; echo '---'; systemctl is-active lagrange-rx || true"]
    out = subprocess.check_output(cmd, text=True, timeout=20)
    rev_part, _, svc_part = out.partition('---')
    return (rev_part.strip() or None), (svc_part.strip() or None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument('--remote', default='origin')
    ap.add_argument('--branch', default='main')
    ap.add_argument('--url', default=DEFAULT_URL)
    ap.add_argument('--host', default=DEFAULT_HOST)
    ap.add_argument('--key', default=DEFAULT_KEY)
    ap.add_argument('--ssh', action='store_true', help='also query the server over SSH')
    args = ap.parse_args(argv)

    remote_sha = git_remote_sha(args.repo, args.remote, args.branch)
    local_sha = git_local_sha(args.repo)

    health_ok = False
    prod_sha: str | None = None
    svc_state: str | None = None
    source = 'health'

    try:
        health = fetch_health(args.url)
        health_ok = health.get('status') == 'ok'
        prod_sha = health.get('revision')
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f'Health check failed ({args.url}): {exc}', file=sys.stderr)

    if args.ssh or not prod_sha:
        try:
            ssh_sha, svc_state = fetch_ssh_revision(args.host, args.key, VERSION_FILE)
            if not prod_sha:
                prod_sha = ssh_sha
                source = 'ssh'
        except (subprocess.SubprocessError, OSError) as exc:
            print(f'SSH check failed ({args.host}): {exc}', file=sys.stderr)

    print(f'Remote ({args.remote}/{args.branch}): {remote_sha}')
    if local_sha:
        print(f'Local (HEAD):        {local_sha}')
    print(f'Prod ({source}):       {prod_sha or "unknown"} (health={"ok" if health_ok else "FAIL"}'
          + (f', service={svc_state}' if svc_state else '') + ')')

    if health_ok and prod_sha == remote_sha:
        print('STATUS: UP TO DATE')
        return 0
    if not health_ok:
        print('STATUS: UNHEALTHY')
        return 2
    print('STATUS: OUT OF DATE' if prod_sha else 'STATUS: UNKNOWN REVISION')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
