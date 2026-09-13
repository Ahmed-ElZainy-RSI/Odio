from __future__ import annotations

import threading
import time

import requests

from .config import AI_CONTRACT_SETTINGS as SETTINGS


class BackendClientError(RuntimeError):
    """Raised when a call to the backend fails after retries."""


class BackendClient:
    """
    Everything AI Engine needs to call INTO the backend, per the
    "Steps For Communication Between AI Engine To Backend And Vice Versa"
    spec: token acquisition, download-URL refresh, the 3-step upload
    cycle, and job status callbacks (metadata / completed / failed).
    """

    def __init__(self) -> None:
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = threading.Lock()

    # ============================================================
    # TOKEN (client_credentials against Keycloak)
    # ============================================================

    def _fetch_new_token(self) -> tuple[str, float]:
        if not SETTINGS.token_url:
            raise BackendClientError("TOKEN_URL is not configured.")
        if not SETTINGS.client_secret:
            raise BackendClientError("AI_ENGINE_CLIENT_SECRET is not configured.")

        response = requests.post(
            SETTINGS.token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": SETTINGS.client_id,
                "client_secret": SETTINGS.client_secret,
            },
            timeout=SETTINGS.http_timeout,
        )
        response.raise_for_status()

        payload = response.json()
        access_token = payload["access_token"]
        expires_in = float(payload.get("expires_in", 300))

        expires_at = time.monotonic() + max(
            0.0, expires_in - SETTINGS.token_expiry_safety_seconds
        )
        return access_token, expires_at

    def get_backend_token(self) -> str:
        """
        Cached client_credentials token. Per the spec: don't call the token
        endpoint before every request, only when the cached one is missing
        or about to expire.
        """
        with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token

            token, expires_at = self._fetch_new_token()
            self._token = token
            self._token_expires_at = expires_at
            return token

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.get_backend_token()}"}

    # ============================================================
    # DOWNLOAD (source PDF / source free-text file)
    # ============================================================

    def get_fresh_download_url(self, file_id: str) -> str:
        response = requests.get(
            f"{SETTINGS.backend_base_url}/api/files/{file_id}/get-download-url",
            headers=self._auth_headers(),
            timeout=SETTINGS.http_timeout,
        )
        response.raise_for_status()
        return response.json()["url"]

    def download_file(self, url: str, file_id: str) -> bytes:
        """
        Download via the given S3 URL. If it looks expired (403/410 are the
        common presigned-URL-expiry signals), ask the backend for a fresh
        URL once and retry.
        """
        response = requests.get(url, timeout=SETTINGS.http_timeout)

        if response.status_code in (403, 410):
            fresh_url = self.get_fresh_download_url(file_id)
            response = requests.get(fresh_url, timeout=SETTINGS.http_timeout)

        response.raise_for_status()
        return response.content

    # ============================================================
    # UPLOAD CYCLE (initialize -> PUT to S3 -> confirm)
    # ============================================================

    def initialize_upload(
        self,
        file_name: str,
        content_type: str,
        size_in_bytes: int,
        folder_id: str | None,
        path_prefix: str | None,
    ) -> dict:
        response = requests.post(
            f"{SETTINGS.backend_base_url}/api/files/initialize-upload",
            headers=self._auth_headers(),
            json={
                "fileName": file_name,
                "contentType": content_type,
                "sizeInBytes": size_in_bytes,
                "folderId": folder_id,
                "pathPrefix": path_prefix,
            },
            timeout=SETTINGS.http_timeout,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def upload_to_s3(url: str, required_headers: dict, content: bytes) -> None:
        response = requests.put(
            url,
            headers=required_headers,
            data=content,
            timeout=SETTINGS.http_timeout,
        )
        response.raise_for_status()

    def confirm_upload(self, file_id: str) -> None:
        response = requests.post(
            f"{SETTINGS.backend_base_url}/api/files/{file_id}/confirm-upload",
            headers=self._auth_headers(),
            timeout=SETTINGS.http_timeout,
        )
        response.raise_for_status()

    def upload_file(
        self,
        file_name: str,
        content_type: str,
        content: bytes,
        folder_id: str | None,
        path_prefix: str | None,
    ) -> str:
        """
        Full 3-step cycle. Returns the backend fileId to use in an
        `artifacts` entry.
        """
        init = self.initialize_upload(
            file_name=file_name,
            content_type=content_type,
            size_in_bytes=len(content),
            folder_id=folder_id,
            path_prefix=path_prefix,
        )

        file_id = init["fileId"]
        upload = init["upload"]

        self.upload_to_s3(
            url=upload["url"],
            required_headers=upload["requiredHeaders"],
            content=content,
        )

        self.confirm_upload(file_id)

        return file_id

    # ============================================================
    # JOB STATUS CALLBACKS
    # ============================================================

    def set_job_metadata(self, job_id: str, pages_count: int) -> None:
        response = requests.patch(
            f"{SETTINGS.backend_base_url}/api/jobs/{job_id}/metadata",
            headers=self._auth_headers(),
            json={"pagesCount": pages_count},
            timeout=SETTINGS.http_timeout,
        )
        response.raise_for_status()

    def mark_page_completed(
        self,
        job_id: str,
        page_number: int,
        artifacts: list[dict],
    ) -> None:
        response = requests.patch(
            f"{SETTINGS.backend_base_url}/api/jobs/{job_id}/{page_number}/completed",
            headers=self._auth_headers(),
            json={"artifacts": artifacts},
            timeout=SETTINGS.http_timeout,
        )
        response.raise_for_status()

    def mark_page_failed(self, job_id: str, page_number: int, reason: str) -> None:
        response = requests.patch(
            f"{SETTINGS.backend_base_url}/api/jobs/{job_id}/{page_number}/failed",
            headers=self._auth_headers(),
            json={"reason": reason},
            timeout=SETTINGS.http_timeout,
        )
        response.raise_for_status()

    def mark_job_failed(self, job_id: str, reason: str) -> None:
        response = requests.patch(
            f"{SETTINGS.backend_base_url}/api/jobs/{job_id}/failed",
            headers=self._auth_headers(),
            json={"reason": reason},
            timeout=SETTINGS.http_timeout,
        )
        response.raise_for_status()


backend_client = BackendClient()
