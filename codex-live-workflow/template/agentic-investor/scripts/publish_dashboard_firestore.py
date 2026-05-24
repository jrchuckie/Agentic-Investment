from __future__ import annotations

import json
import math
import os
import re
import base64
import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import quote

from agentic_investor_common import ROOT, now_iso, read_json


SNAPSHOT_PATH = ROOT / "dashboard" / "data" / "snapshot.json"


def _slim_investment_brief_for_firestore(brief: Any) -> dict[str, Any] | None:
    if not isinstance(brief, dict):
        return None

    keep_keys = {
        "headlineZh",
        "summaryZh",
        "cashTakeawayZh",
        "whyNoFullDeploymentZh",
        "dailyStockPitch",
        "dailyDiscoveryPitch",
        "topStockPitches",
        "topDiscoveryPitches",
        "dailyOptionPitch",
        "topOptionPitches",
        "actionCandidates",
        "portfolioStats",
        "batchPlaybookPromptZh",
        "batchDiscoveryPlaybookPromptZh",
        "batchOptionPlaybookPromptZh",
    }
    return {key: value for key, value in brief.items() if key in keep_keys}


def _compact_snapshot_for_firestore(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Firestore documents have a 1 MiB limit (UTF-8 bytes).

    The dashboard `snapshot.json` is optimized for static hosting and can exceed that
    limit due to large pre-rendered visualization blocks. For Firestore publish, we
    keep the core decision data and strip the heaviest visualization payloads so the
    remote dashboard can still render summaries without exceeding limits.
    """

    compact: dict[str, Any] = dict(snapshot)
    visualizations = compact.get("visualizations")
    if isinstance(visualizations, dict):
        slim_visualizations = dict(visualizations)
        # These fields tend to contain long narrative blocks and dominate the
        # snapshot size. Keep a small investment brief so the private dashboard
        # can still render Top 3 pitch cards from Firestore.
        slim_brief = _slim_investment_brief_for_firestore(slim_visualizations.get("investmentBrief"))
        slim_visualizations.pop("watchlistCoverage", None)
        if slim_brief is not None:
            slim_visualizations["investmentBrief"] = slim_brief
        else:
            slim_visualizations.pop("investmentBrief", None)
        compact["visualizations"] = slim_visualizations
    return compact


def _credential_path() -> str:
    return (
        os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    )


def _use_application_default() -> bool:
    return os.environ.get("FIREBASE_USE_ADC", "").strip().lower() in {"1", "true", "yes"}


def _doc_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return cleaned[:120] or "snapshot"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _read_len(data: bytes, offset: int) -> tuple[int, int]:
    first = data[offset]
    offset += 1
    if first < 0x80:
        return first, offset
    count = first & 0x7F
    length = int.from_bytes(data[offset : offset + count], "big")
    return length, offset + count


def _read_tlv(data: bytes, offset: int) -> tuple[int, bytes, int]:
    tag = data[offset]
    length, value_offset = _read_len(data, offset + 1)
    end = value_offset + length
    return tag, data[value_offset:end], end


def _read_int(data: bytes, offset: int) -> tuple[int, int]:
    tag, value, end = _read_tlv(data, offset)
    if tag != 0x02:
        raise ValueError("Expected ASN.1 integer in RSA key.")
    return int.from_bytes(value, "big", signed=False), end


def _rsa_private_numbers(private_key_pem: str) -> tuple[int, int]:
    body = "".join(
        line.strip()
        for line in private_key_pem.splitlines()
        if line and not line.startswith("-----")
    )
    der = base64.b64decode(body)
    tag, pkcs8, _ = _read_tlv(der, 0)
    if tag != 0x30:
        raise ValueError("Expected PKCS#8 sequence.")

    _, offset = _read_int(pkcs8, 0)
    tag, _, offset = _read_tlv(pkcs8, offset)
    if tag != 0x30:
        raise ValueError("Expected PKCS#8 algorithm sequence.")
    tag, private_key_der, _ = _read_tlv(pkcs8, offset)
    if tag != 0x04:
        raise ValueError("Expected PKCS#8 private key octet string.")

    tag, rsa_key, _ = _read_tlv(private_key_der, 0)
    if tag != 0x30:
        raise ValueError("Expected RSA private key sequence.")
    _, offset = _read_int(rsa_key, 0)
    n, offset = _read_int(rsa_key, offset)
    _, offset = _read_int(rsa_key, offset)
    d, _ = _read_int(rsa_key, offset)
    return n, d


def _rs256_sign(signing_input: bytes, private_key_pem: str) -> bytes:
    n, d = _rsa_private_numbers(private_key_pem)
    digest = hashlib.sha256(signing_input).digest()
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + digest
    key_size = (n.bit_length() + 7) // 8
    padding_len = key_size - len(digest_info) - 3
    if padding_len < 8:
        raise ValueError("RSA key is too small for RS256.")
    encoded = b"\x00\x01" + (b"\xff" * padding_len) + b"\x00" + digest_info
    signature = pow(int.from_bytes(encoded, "big"), d, n)
    return signature.to_bytes(key_size, "big")


def _service_account_jwt(service_account: dict[str, Any], scope: str) -> str:
    import time

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": service_account["client_email"],
        "scope": scope,
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now,
        "exp": now + 3600,
    }
    signing_input = ".".join(
        [
            _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8")),
        ]
    ).encode("ascii")
    signature = _rs256_sign(signing_input, service_account["private_key"])
    return signing_input.decode("ascii") + "." + _b64url(signature)


def _access_token_from_service_account(
    service_account: dict[str, Any],
    scope: str = "https://www.googleapis.com/auth/datastore",
) -> str:
    import urllib.parse
    import urllib.request

    payload = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": _service_account_jwt(service_account, scope),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
    data = json.loads(body)
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"OAuth token response missing access_token: {body[:500]}")
    return str(token)


def _firestore_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        if not math.isfinite(value):
            return {"nullValue": None}
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_firestore_value(item) for item in value]}}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {str(key): _firestore_value(item) for key, item in value.items()}}}
    return {"stringValue": str(value)}


def _firestore_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _firestore_value(value) for key, value in payload.items()}


def _document_name(project_id: str, user_uid: str, snapshot_id: str) -> str:
    return (
        f"projects/{quote(project_id, safe='')}/databases/(default)/documents/"
        f"users/{quote(user_uid, safe='')}/snapshots/{quote(snapshot_id, safe='')}"
    )


def _publish_with_firestore_rest(
    service_account: dict[str, Any],
    project_id: str,
    user_uid: str,
    history_id: str,
    payload: dict[str, Any],
) -> None:
    import urllib.request

    token = _access_token_from_service_account(service_account)
    fields = _firestore_fields(payload)
    commit_url = f"https://firestore.googleapis.com/v1/projects/{quote(project_id, safe='')}/databases/(default)/documents:commit"
    writes = [
        {"update": {"name": _document_name(project_id, user_uid, "current"), "fields": fields}},
        {"update": {"name": _document_name(project_id, user_uid, history_id), "fields": fields}},
    ]
    body = json.dumps({"writes": writes}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        commit_url,
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            if response.status >= 400:
                raise RuntimeError(f"{response.status} {response.reason}")
            response.read()
    except Exception as exc:
        message = str(exc)
        if hasattr(exc, "read"):
            try:
                message = exc.read().decode("utf-8")[:2000]
            except Exception:
                pass
        raise RuntimeError(message) from exc


def publish_snapshot() -> dict[str, Any]:
    credential_path = _credential_path()
    project_id = os.environ.get("FIREBASE_PROJECT_ID", "").strip()
    service_account_email = os.environ.get("FIREBASE_SERVICE_ACCOUNT_EMAIL", "").strip()
    user_uid = os.environ.get("FIREBASE_USER_UID", "").strip()
    if not user_uid or (not credential_path and not _use_application_default()):
        return {
            "status": "skipped_missing_config",
            "message": (
                "Set FIREBASE_USER_UID plus FIREBASE_SERVICE_ACCOUNT or GOOGLE_APPLICATION_CREDENTIALS. "
                "For gcloud Application Default Credentials, set FIREBASE_USE_ADC=1 and FIREBASE_PROJECT_ID."
            ),
            "snapshot": str(SNAPSHOT_PATH),
            "serviceAccountEmail": service_account_email or None,
        }

    credential_file = Path(credential_path).expanduser() if credential_path else None
    if credential_file and not credential_file.exists():
        return {
            "status": "skipped_missing_service_account",
            "message": f"Credential file not found: {credential_file}",
            "snapshot": str(SNAPSHOT_PATH),
            "serviceAccountEmail": service_account_email or None,
        }

    snapshot = read_json(SNAPSHOT_PATH, {})
    snapshot = _compact_snapshot_for_firestore(snapshot)
    generated_at = str(snapshot.get("generatedAt") or now_iso())
    history_id = _doc_id(generated_at)
    payload = {
        **snapshot,
        "_publishedAt": now_iso(),
        "_publisher": "agentic-investor-local-bridge",
        "_policy": {
            "advisoryOnly": True,
            "brokerExecutionEnabled": False,
            "realAccountReadEnabled": False,
        },
    }

    provider = "firebase_admin"
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        if not firebase_admin._apps:
            options = {"projectId": project_id} if project_id else None
            credential = (
                credentials.Certificate(str(credential_file))
                if credential_file
                else credentials.ApplicationDefault()
            )
            firebase_admin.initialize_app(credential, options)
        db = firestore.client()
        base = db.collection("users").document(user_uid)
        current_ref = base.collection("snapshots").document("current")
        history_ref = base.collection("snapshots").document(history_id)

        batch = db.batch()
        batch.set(current_ref, payload)
        batch.set(history_ref, payload)
        batch.commit()
    except Exception as admin_exc:
        if not credential_file:
            return {
                "status": "skipped_missing_dependency",
                "message": "firebase-admin is unavailable and REST fallback requires a service account JSON file.",
                "installCommand": "python -m pip install firebase-admin",
                "error": str(admin_exc),
                "snapshot": str(SNAPSHOT_PATH),
                "serviceAccountEmail": service_account_email or None,
            }
        provider = "firestore_rest"
        try:
            service_account = read_json(credential_file, {})
            resolved_project_id = project_id or str(service_account.get("project_id") or "")
            if not resolved_project_id:
                raise RuntimeError("FIREBASE_PROJECT_ID is missing and service account JSON has no project_id.")
            _publish_with_firestore_rest(
                service_account=service_account,
                project_id=resolved_project_id,
                user_uid=user_uid,
                history_id=history_id,
                payload=payload,
            )
            project_id = resolved_project_id
            service_account_email = service_account_email or str(service_account.get("client_email") or "")
        except Exception as rest_exc:
            return {
                "status": "publish_failed",
                "message": "Could not publish dashboard snapshot to Firestore.",
                "firebaseAdminError": str(admin_exc),
                "restError": str(rest_exc),
                "snapshot": str(SNAPSHOT_PATH),
                "serviceAccountEmail": service_account_email or None,
                "projectId": project_id or None,
            }

    return {
        "status": "published",
        "provider": provider,
        "userUid": user_uid,
        "generatedAt": generated_at,
        "currentPath": f"users/{user_uid}/snapshots/current",
        "historyPath": f"users/{user_uid}/snapshots/{history_id}",
        "projectId": project_id or None,
        "serviceAccountEmail": service_account_email or None,
    }


def main() -> int:
    result = publish_snapshot()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"published", "skipped_missing_config"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
