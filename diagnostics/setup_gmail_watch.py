from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


# Optional: force IPv4 to avoid environments where IPv6 egress is blocked
import socket  # noqa: E402

_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):  # noqa: ANN001
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


if os.getenv("FORCE_IPV4", "false").strip().lower() == "true":
    socket.getaddrinfo = _getaddrinfo_ipv4  # type: ignore[assignment]


# Scopes: read-only is sufficient for watch + history listing
SCOPES: List[str] = [
    "https://www.googleapis.com/auth/gmail.readonly",
]


def load_or_run_oauth(client_secret_path: str, token_out_path: Path) -> Credentials:
    """Load existing OAuth credentials or run the local server flow to obtain them.

    Tokens are stored at token_out_path for future reuse.
    """
    creds: Optional[Credentials] = None
    if token_out_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_out_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Let google-auth refresh automatically when used
            pass
        else:
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
            creds = flow.run_local_server(port=0)
        # Persist credentials
        token_out_path.parent.mkdir(parents=True, exist_ok=True)
        with token_out_path.open("w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return creds


def call_watch(
    creds: Credentials,
    project_id: str,
    topic_name: str,
    label_ids: List[str],
    label_filter_behavior: str = "INCLUDE",
) -> Dict[str, Any]:
    """Call users.watch for the current user and return the response.

    Gmail sends only {emailAddress, historyId} notifications to Pub/Sub; persist the
    returned historyId per-user for subsequent history listing.
    """
    service = build("gmail", "v1", credentials=creds)
    body: Dict[str, Any] = {
        "topicName": f"projects/{project_id}/topics/{topic_name}",
        "labelFilterBehavior": label_filter_behavior,
    }
    if label_ids:
        body["labelIds"] = label_ids

    return service.users().watch(userId="me", body=body).execute()


def call_stop(creds: Credentials) -> None:
    service = build("gmail", "v1", credentials=creds)
    service.users().stop(userId="me").execute()


def main() -> None:
    parser = argparse.ArgumentParser(description="Setup Gmail Pub/Sub watch for one Gmail account")
    parser.add_argument("--project", required=True, help="GCP project ID that owns the Pub/Sub topic")
    parser.add_argument("--topic", required=True, help="Pub/Sub topic name (not full path)")
    parser.add_argument(
        "--labels",
        default="INBOX",
        help="Comma-separated Gmail labelIds to include (e.g. INBOX,UNREAD). Leave empty for all",
    )
    parser.add_argument(
        "--client-secret",
        default=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET_FILE", "secrets/client_secret.json"),
        help="Path to OAuth client_secret.json for Desktop app",
    )
    parser.add_argument(
        "--token-out",
        default=None,
        help="Optional path to save OAuth token JSON. Defaults to secrets/tokens/<email>.json",
    )
    parser.add_argument("--stop", action="store_true", help="Stop existing watch for this user")

    args = parser.parse_args()

    client_secret_path = args.client_secret
    if not os.path.isfile(client_secret_path):
        raise SystemExit(
            f"client_secret.json not found at: {client_secret_path}. Set --client-secret or GOOGLE_OAUTH_CLIENT_SECRET_FILE"
        )

    # OAuth for the user (a browser will open on first run)
    # We store the token temporarily at a generic path; once we know the email, we rename it.
    tmp_token_path = Path("secrets/tokens/.tmp_oauth_token.json")
    creds = load_or_run_oauth(client_secret_path, tmp_token_path)

    # Determine email address of the authorized account
    profile = build("gmail", "v1", credentials=creds).users().getProfile(userId="me").execute()
    email_address: str = profile.get("emailAddress", "unknown")

    # Final token path per-user
    token_out_path = Path(args.token_out) if args.token_out else Path(f"secrets/tokens/{email_address}.json")
    token_out_path.parent.mkdir(parents=True, exist_ok=True)
    token_out_path.write_text(creds.to_json(), encoding="utf-8")
    try:
        if tmp_token_path.exists():
            tmp_token_path.unlink(missing_ok=True)
    except Exception:
        pass

    if args.stop:
        call_stop(creds)
        print(f"Stopped Gmail watch for {email_address}")
        return

    label_ids = [l.strip() for l in args.labels.split(",") if l.strip()] if args.labels is not None else []

    try:
        resp = call_watch(creds, args.project, args.topic, label_ids)
    except HttpError as e:  # noqa: BLE001
        print("Failed to start Gmail watch. Common causes:")
        print("- Pub/Sub topic must be in the SAME project as the Gmail API client")
        print("- Topic must grant roles/pubsub.publisher to serviceAccount:gmail-api-push@system.gserviceaccount.com")
        print("- Gmail API must be enabled in the project")
        print(f"HttpError: {e}")
        raise

    # Save minimal state with the returned historyId for this user
    state_path = Path(f"secrets/tokens/{email_address}.state.json")
    state: Dict[str, Any] = {
        "emailAddress": email_address,
        "projectId": args.project,
        "topic": args.topic,
        "watchResponse": resp,
    }
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print("✅ Gmail watch started:")
    print(json.dumps(resp, indent=2))
    print()
    print("Remember: Gmail watch expires periodically (≈7 days). Re-run this script to renew.")
    print("Token saved:", token_out_path)
    print("State saved:", state_path)
    print()
    print("If you haven't yet, grant Pub/Sub Publisher on the topic to Gmail push service account:")
    print(
        f"gcloud pubsub topics add-iam-policy-binding {args.topic} "
        f"--member=serviceAccount:gmail-api-push@system.gserviceaccount.com "
        f"--role=roles/pubsub.publisher --project {args.project}"
    )


if __name__ == "__main__":
    main()


