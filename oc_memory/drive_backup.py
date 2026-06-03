"""Google Drive backup for oc-memory.

Uploads memory-export.json and memory.db to a Google Drive folder.

Credentials are loaded from plain JSON files (no vault dependency):
  - Client credentials: OC_MEMORY_DRIVE_CLIENT_CREDS env var or ~/.oc-memory/drive-client-creds.json
  - Token:              OC_MEMORY_DRIVE_TOKEN env var or ~/.oc-memory/drive-token.json

Setup:
  1. Go to https://console.cloud.google.com/apis/credentials
  2. Create OAuth 2.0 Client ID (Desktop application)
  3. Download the JSON and save to ~/.oc-memory/drive-client-creds.json
  4. On first run, a browser window opens for authorization.
     The token is saved to ~/.oc-memory/drive-token.json.

Install deps:
  pip install oc-memory[drive]
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Default paths
DEFAULT_DATA_DIR = Path(os.environ.get("OC_MEMORY_HOME", Path.home() / ".oc-memory"))
DEFAULT_TOKEN_PATH = Path(os.environ.get("OC_MEMORY_DRIVE_TOKEN", DEFAULT_DATA_DIR / "drive-token.json"))
DEFAULT_CLIENT_CREDS_PATH = Path(
    os.environ.get("OC_MEMORY_DRIVE_CLIENT_CREDS", DEFAULT_DATA_DIR / "drive-client-creds.json")
)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class DriveBackupManager:
    """Upload memory files to Google Drive.

    Loads credentials from plain JSON files. No vault dependency.
    """

    def __init__(
        self,
        folder_name: str = "oc-memory-backups",
        token_path: Optional[str | Path] = None,
        client_creds_path: Optional[str | Path] = None,
    ):
        self.folder_name = folder_name
        self.token_path = Path(token_path) if token_path else DEFAULT_TOKEN_PATH
        self.client_creds_path = Path(client_creds_path) if client_creds_path else DEFAULT_CLIENT_CREDS_PATH
        self._service = None

    def _load_credentials(self):
        """Load or refresh Google OAuth2 credentials."""
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError:
            raise ImportError(
                "Google Drive backup requires: pip install oc-memory[drive]"
            )

        creds = None

        # Try loading saved token
        if self.token_path.exists():
            try:
                token_data = json.loads(self.token_path.read_text())
                creds = Credentials.from_authorized_user_info(token_data, SCOPES)
            except Exception as e:
                log.warning(f"Failed to load Drive token from {self.token_path}: {e}")
                creds = None

        # Refresh or create new credentials
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    self._save_token(creds)
                except Exception as e:
                    log.warning(f"Token refresh failed: {e} — re-authorizing")
                    creds = None

            if not creds:
                if not self.client_creds_path.exists():
                    raise FileNotFoundError(
                        f"Google OAuth2 client credentials not found at {self.client_creds_path}.\n"
                        "Download from https://console.cloud.google.com/apis/credentials and save there."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.client_creds_path), SCOPES
                )
                creds = flow.run_local_server(port=0)
                self._save_token(creds)

        return creds

    def _save_token(self, creds):
        """Save OAuth2 token as plain JSON."""
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        token_data = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if creds.scopes else SCOPES,
        }
        self.token_path.write_text(json.dumps(token_data, indent=2))
        log.debug(f"Drive token saved to {self.token_path}")

    def _get_service(self):
        """Return a Google Drive API service object (cached)."""
        if self._service is None:
            try:
                from googleapiclient.discovery import build
            except ImportError:
                raise ImportError(
                    "Google Drive backup requires: pip install oc-memory[drive]"
                )
            creds = self._load_credentials()
            self._service = build("drive", "v3", credentials=creds)
        return self._service

    def _get_or_create_folder(self) -> str:
        """Get or create the backup folder in Drive. Returns folder ID."""
        service = self._get_service()
        query = (
            f"name='{self.folder_name}' and mimeType='application/vnd.google-apps.folder' "
            "and trashed=false"
        )
        results = service.files().list(q=query, fields="files(id, name)").execute()
        files = results.get("files", [])

        if files:
            return files[0]["id"]

        # Create folder
        metadata = {
            "name": self.folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        folder = service.files().create(body=metadata, fields="id").execute()
        log.info(f"Created Drive folder '{self.folder_name}' (id: {folder['id']})")
        return folder["id"]

    def _upload_file(self, local_path: Path, folder_id: str) -> dict:
        """Upload or update a file in Drive. Returns file metadata."""
        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError:
            raise ImportError(
                "Google Drive backup requires: pip install oc-memory[drive]"
            )

        service = self._get_service()
        filename = local_path.name

        # Check if file already exists in folder
        query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
        results = service.files().list(q=query, fields="files(id, name)").execute()
        existing = results.get("files", [])

        media = MediaFileUpload(str(local_path), resumable=True)

        if existing:
            # Update existing file
            file_id = existing[0]["id"]
            updated = (
                service.files()
                .update(fileId=file_id, media_body=media, fields="id, name, size, modifiedTime")
                .execute()
            )
            log.info(f"Updated Drive file: {filename} (id: {file_id})")
            return updated
        else:
            # Create new file
            metadata = {"name": filename, "parents": [folder_id]}
            created = (
                service.files()
                .create(body=metadata, media_body=media, fields="id, name, size, modifiedTime")
                .execute()
            )
            log.info(f"Uploaded Drive file: {filename} (id: {created['id']})")
            return created

    def upload_backup(
        self,
        export_dir: Optional[str | Path] = None,
        db_path: Optional[str | Path] = None,
    ) -> list[dict]:
        """Upload memory-export.json and memory.db to Google Drive.

        Args:
            export_dir: Directory containing memory-export.json.
                        Defaults to OC_MEMORY_EXPORT env var or ~/.oc-memory/export.
            db_path: Path to memory.db.
                     Defaults to OC_MEMORY_DB env var or ~/.oc-memory/memory.db.

        Returns:
            List of uploaded file metadata dicts (id, name, size, modifiedTime).
        """
        if export_dir is None:
            export_dir = Path(
                os.environ.get("OC_MEMORY_EXPORT", DEFAULT_DATA_DIR / "export")
            )
        if db_path is None:
            db_path = Path(
                os.environ.get("OC_MEMORY_DB", DEFAULT_DATA_DIR / "memory.db")
            )

        export_dir = Path(export_dir)
        db_path = Path(db_path)

        folder_id = self._get_or_create_folder()
        uploaded = []

        # Upload memory-export.json
        json_path = export_dir / "memory-export.json"
        if json_path.exists():
            result = self._upload_file(json_path, folder_id)
            uploaded.append(result)
        else:
            log.warning(f"memory-export.json not found at {json_path} — skipping")

        # Upload memory.db
        if db_path.exists():
            result = self._upload_file(db_path, folder_id)
            uploaded.append(result)
        else:
            log.warning(f"memory.db not found at {db_path} — skipping")

        return uploaded

    def list_backups(self) -> list[dict]:
        """List files in the Drive backup folder."""
        service = self._get_service()
        query = (
            f"name='{self.folder_name}' and mimeType='application/vnd.google-apps.folder' "
            "and trashed=false"
        )
        results = service.files().list(q=query, fields="files(id, name)").execute()
        folders = results.get("files", [])

        if not folders:
            return []

        folder_id = folders[0]["id"]
        results = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="files(id, name, size, modifiedTime)",
                orderBy="modifiedTime desc",
            )
            .execute()
        )
        return results.get("files", [])
