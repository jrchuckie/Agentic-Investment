from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

import requests

from agentic_investor_common import ROOT, read_json
from publish_dashboard_firestore import (
    _access_token_from_service_account,
    _credential_path,
)


PUBLIC_DIR = ROOT / "dashboard"
FIREBASE_CONFIG_PATH = ROOT / "firebase.json"


def _hosting_config() -> dict[str, Any]:
    firebase_json = read_json(FIREBASE_CONFIG_PATH, {})
    hosting = firebase_json.get("hosting", {})
    headers = []
    for item in hosting.get("headers", []) or []:
        header_map = {
            header.get("key"): header.get("value")
            for header in item.get("headers", []) or []
            if header.get("key")
        }
        if header_map:
            headers.append({"glob": item.get("source", "**"), "headers": header_map})
    config: dict[str, Any] = {}
    if headers:
        config["headers"] = headers
    if hosting.get("cleanUrls") is not None:
        config["cleanUrls"] = bool(hosting.get("cleanUrls"))
    return config


def _iter_public_files() -> list[Path]:
    files: list[Path] = []
    for path in PUBLIC_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(PUBLIC_DIR).as_posix()
        if rel.startswith("data/"):
            continue
        if rel.startswith("."):
            continue
        files.append(path)
    return sorted(files)


def _gzip_bytes(path: Path) -> bytes:
    return gzip.compress(path.read_bytes(), compresslevel=9, mtime=0)


def deploy_hosting() -> dict[str, Any]:
    credential_path = _credential_path()
    if not credential_path:
        return {"status": "skipped_missing_config", "message": "FIREBASE_SERVICE_ACCOUNT is not configured."}
    credential_file = Path(credential_path).expanduser()
    if not credential_file.exists():
        return {"status": "skipped_missing_service_account", "message": f"Credential file not found: {credential_file}"}

    service_account = read_json(credential_file, {})
    site_id = str(service_account.get("project_id") or "")
    if not site_id:
        return {"status": "failed", "message": "service account JSON has no project_id."}

    token = _access_token_from_service_account(
        service_account,
        scope="https://www.googleapis.com/auth/cloud-platform",
    )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    create_url = f"https://firebasehosting.googleapis.com/v1beta1/sites/{site_id}/versions"
    create_response = requests.post(
        create_url,
        headers=headers,
        json={"config": _hosting_config()},
        timeout=45,
    )
    if not create_response.ok:
        return {"status": "failed_create_version", "message": create_response.text[:2000]}
    version = create_response.json()
    version_name = version["name"]

    compressed_by_hash: dict[str, bytes] = {}
    files_payload: dict[str, str] = {}
    for path in _iter_public_files():
        compressed = _gzip_bytes(path)
        digest = hashlib.sha256(compressed).hexdigest()
        compressed_by_hash[digest] = compressed
        files_payload["/" + path.relative_to(PUBLIC_DIR).as_posix()] = digest

    populate_response = requests.post(
        f"https://firebasehosting.googleapis.com/v1beta1/{version_name}:populateFiles",
        headers=headers,
        json={"files": files_payload},
        timeout=45,
    )
    if not populate_response.ok:
        return {"status": "failed_populate_files", "message": populate_response.text[:2000], "version": version_name}
    populate = populate_response.json()
    upload_url = populate.get("uploadUrl")
    required_hashes = populate.get("uploadRequiredHashes", []) or []

    for digest in required_hashes:
        upload_response = requests.post(
            f"{upload_url}/{digest}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
            },
            data=compressed_by_hash[digest],
            timeout=60,
        )
        if not upload_response.ok:
            return {
                "status": "failed_upload",
                "hash": digest,
                "message": upload_response.text[:2000],
                "version": version_name,
            }

    finalize_response = requests.patch(
        f"https://firebasehosting.googleapis.com/v1beta1/{version_name}?update_mask=status",
        headers=headers,
        json={"status": "FINALIZED"},
        timeout=45,
    )
    if not finalize_response.ok:
        return {"status": "failed_finalize", "message": finalize_response.text[:2000], "version": version_name}

    release_response = requests.post(
        f"https://firebasehosting.googleapis.com/v1beta1/sites/{site_id}/releases",
        headers={"Authorization": f"Bearer {token}"},
        params={"versionName": version_name},
        timeout=45,
    )
    if not release_response.ok:
        return {"status": "failed_release", "message": release_response.text[:2000], "version": version_name}

    return {
        "status": "deployed",
        "siteId": site_id,
        "version": version_name,
        "fileCount": len(files_payload),
        "uploadedFileCount": len(required_hashes),
        "url": f"https://{site_id}.web.app",
        "release": release_response.json().get("name"),
    }


def main() -> int:
    result = deploy_hosting()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "deployed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
