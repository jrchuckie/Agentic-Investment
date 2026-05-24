from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from agentic_investor_common import ROOT, read_json


TARGETS_PATH = ROOT / "publish-targets.json"


def _parse_repo(url: str) -> tuple[str, str]:
    match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)", url)
    if not match:
        raise ValueError(f"Unsupported GitHub URL: {url}")
    return match.group("owner"), match.group("repo")


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN or GH_TOKEN.")
    return token


def _request(method: str, url: str, token: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agentic-investor-pages-manager",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"message": raw}
        return exc.code, data


def _target_repo() -> tuple[str, str, str]:
    targets = read_json(TARGETS_PATH)
    target = targets["targets"][targets["default_target"]]
    owner, repo = _parse_repo(target["url"])
    return target["url"], owner, repo


def get_pages() -> dict[str, Any]:
    target_url, owner, repo = _target_repo()
    status, data = _request("GET", f"https://api.github.com/repos/{owner}/{repo}/pages", _token())
    return {"target": target_url, "http_status": status, "data": data}


def ensure_pages(branch: str, path: str) -> dict[str, Any]:
    target_url, owner, repo = _target_repo()
    token = _token()
    url = f"https://api.github.com/repos/{owner}/{repo}/pages"
    desired = {"source": {"branch": branch, "path": path}}
    get_status, get_data = _request("GET", url, token)
    if get_status == 404:
        create_status, create_data = _request("POST", url, token, desired)
        return {
            "target": target_url,
            "action": "create",
            "http_status": create_status,
            "data": create_data,
            "required_permissions": "Fine-grained token needs Pages: write and Administration: write.",
        }
    if get_status != 200:
        return {"target": target_url, "action": "get_failed", "http_status": get_status, "data": get_data}

    current_source = get_data.get("source", {})
    if current_source.get("branch") == branch and current_source.get("path") == path:
        return {"target": target_url, "action": "already_configured", "http_status": 200, "data": get_data}

    update_status, update_data = _request("PUT", url, token, desired)
    return {
        "target": target_url,
        "action": "update",
        "http_status": update_status,
        "data": update_data,
        "required_permissions": "Fine-grained token needs Pages: write and Administration: write.",
    }


def request_build() -> dict[str, Any]:
    target_url, owner, repo = _target_repo()
    status, data = _request("POST", f"https://api.github.com/repos/{owner}/{repo}/pages/builds", _token())
    return {
        "target": target_url,
        "action": "request_build",
        "http_status": status,
        "data": data,
        "required_permissions": "Fine-grained token needs Pages: write.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage GitHub Pages for the default publish target.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    ensure = sub.add_parser("ensure")
    ensure.add_argument("--branch", default="main")
    ensure.add_argument("--path", default="/docs", choices=["/", "/docs"])
    sub.add_parser("build")
    args = parser.parse_args()

    if args.command == "status":
        result = get_pages()
    elif args.command == "ensure":
        result = ensure_pages(args.branch, args.path)
    else:
        result = request_build()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
