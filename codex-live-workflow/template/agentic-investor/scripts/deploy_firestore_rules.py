from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests

from agentic_investor_common import ROOT, read_json
from publish_dashboard_firestore import _access_token_from_service_account, _credential_path


RULES_PATH = ROOT / "firestore.rules"


def _api_error(response: requests.Response) -> str:
    try:
        return json.dumps(response.json(), ensure_ascii=False, indent=2)
    except Exception:
        return response.text[:2000]


def _request(method: str, url: str, token: str, **kwargs: Any) -> dict[str, Any]:
    response = requests.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=45,
        **kwargs,
    )
    if not response.ok:
        raise RuntimeError(f"{response.status_code} {response.reason}: {_api_error(response)}")
    return response.json() if response.text.strip() else {}


def deploy_firestore_rules() -> dict[str, Any]:
    try:
        credential_path = _credential_path()
        project_id = os.environ.get("FIREBASE_PROJECT_ID", "").strip()
        user_uid = os.environ.get("FIREBASE_USER_UID", "").strip()
        service_account_email = os.environ.get("FIREBASE_SERVICE_ACCOUNT_EMAIL", "").strip()
        if not credential_path or not project_id:
            return {
                "status": "skipped_missing_config",
                "message": "Set FIREBASE_PROJECT_ID and FIREBASE_SERVICE_ACCOUNT or GOOGLE_APPLICATION_CREDENTIALS.",
            }

        credential_file = Path(credential_path).expanduser()
        if not credential_file.exists():
            return {
                "status": "skipped_missing_service_account",
                "message": f"Credential file not found: {credential_file}",
            }

        source = RULES_PATH.read_text(encoding="utf-8")
        service_account = read_json(credential_file, {})
        token = _access_token_from_service_account(
            service_account,
            scope="https://www.googleapis.com/auth/cloud-platform",
        )
        base = f"https://firebaserules.googleapis.com/v1/projects/{project_id}"
        ruleset = _request(
            "POST",
            f"{base}/rulesets",
            token,
            json={"source": {"files": [{"name": "firestore.rules", "content": source}]}},
        )
        ruleset_name = ruleset["name"]
        release_name = f"projects/{project_id}/releases/cloud.firestore"
        release_payload = {
            "name": release_name,
            "rulesetName": ruleset_name,
        }
        try:
            release = _request(
                "PATCH",
                f"https://firebaserules.googleapis.com/v1/{release_name}",
                token,
                json={"release": release_payload, "updateMask": "rulesetName"},
            )
        except RuntimeError as patch_error:
            release = _request(
                "POST",
                f"{base}/releases",
                token,
                json=release_payload,
            )
            release["_patchError"] = str(patch_error)

        return {
            "status": "deployed",
            "projectId": project_id,
            "rulesetName": ruleset_name,
            "releaseName": release.get("name") or release_name,
            "userUid": user_uid or None,
            "serviceAccountEmail": service_account_email or service_account.get("client_email"),
            "rulesPath": str(RULES_PATH),
        }
    except Exception as exc:
        return {
            "status": "deploy_failed",
            "error": str(exc),
            "rulesPath": str(RULES_PATH),
        }


def main() -> int:
    try:
        result = deploy_firestore_rules()
    except Exception as exc:
        result = {"status": "deploy_failed", "error": str(exc), "rulesPath": str(RULES_PATH)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "deployed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
