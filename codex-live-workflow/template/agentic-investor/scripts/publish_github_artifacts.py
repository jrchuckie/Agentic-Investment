from __future__ import annotations

import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from agentic_investor_common import ROOT, read_json


DEFAULT_MANIFEST = ROOT / "publish" / "github_agentic_investment" / "publish-manifest.json"


def _parse_repo(url: str) -> tuple[str, str]:
    match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)", url)
    if not match:
        raise ValueError(f"Unsupported GitHub URL: {url}")
    return match.group("owner"), match.group("repo")


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
            "User-Agent": "agentic-investor-publisher",
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


def _existing_sha(owner: str, repo: str, path: str, branch: str, token: str) -> str | None:
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    status, data = _request("GET", url, token)
    if status == 200:
        return data.get("sha")
    if status == 404:
        return None
    raise RuntimeError(f"GitHub GET failed for {path}: HTTP {status} {data}")


def publish_manifest(manifest_path: Path, branch: str, dry_run: bool) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    owner, repo = _parse_repo(manifest["target"])
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token and not dry_run:
        raise RuntimeError("Missing GITHUB_TOKEN or GH_TOKEN.")

    results = []
    for artifact in manifest.get("artifacts", []):
        if not artifact.get("safe_to_publish"):
            results.append({"artifact": artifact, "status": "skipped_not_safe"})
            continue
        source = ROOT / artifact["source"]
        publish_path = artifact["publish_path"].replace("\\", "/")
        if not source.exists():
            results.append({"artifact": artifact, "status": "missing_source", "source": str(source)})
            continue
        if dry_run:
            results.append({"artifact": artifact, "status": "dry_run_ready", "bytes": source.stat().st_size})
            continue

        content = base64.b64encode(source.read_bytes()).decode("ascii")
        sha = _existing_sha(owner, repo, publish_path, branch, token or "")
        payload = {
            "message": f"Publish {publish_path}",
            "content": content,
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{publish_path}"
        status, data = _request("PUT", url, token or "", payload)
        if status not in {200, 201}:
            raise RuntimeError(f"GitHub PUT failed for {publish_path}: HTTP {status} {data}")
        results.append({"artifact": artifact, "status": "published", "html_url": data.get("content", {}).get("html_url")})

    return {
        "status": "dry_run_completed" if dry_run else "completed",
        "target": manifest["target"],
        "branch": branch,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish selected safe artifacts to the configured GitHub repository.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--branch", default="main")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = publish_manifest(Path(args.manifest), args.branch, args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
