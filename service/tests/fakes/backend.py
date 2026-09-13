"""A contract fake for everything the Backend team owns, plus Keycloak and storage.

This is the single highest-leverage piece of test infrastructure in the project: the whole
V1 risk is protocol conformance, and without this nothing could be verified until the
Backend team was available.

It does three things a mock cannot:

1. **Records call order.** Four of the mandated rules are ordering rules -- metadata before
   any page work, image before text, both files before `completed`, and the three upload
   steps in sequence. Those are only testable against something that remembers.
2. **Injects faults on demand** -- 401, 403-as-expiry, 5xx, 429 with and without
   `Retry-After`, and expiring upload tickets.
3. **Enforces the contract back at us.** It rejects an unknown content type, a
   `Content-Length` that disagrees with the body, a missing bearer token, and a
   `confirm-upload` for an unknown file id -- so our bugs surface here rather than in a
   joint debugging session.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response

ALLOWED_CONTENT_TYPES = {"image/jpeg", "text/plain", "audio/wav", "application/json"}


@dataclass
class Call:
    """One recorded inbound request."""

    op: str
    path: str
    body: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class FaultConfig:
    """What the fake should do wrong, and how many times."""

    token_status: int | None = None
    initialize_upload_status: int | None = None
    initialize_upload_failures: int = 0
    put_status: int | None = None
    put_failures: int = 0
    confirm_status: int | None = None
    confirm_failures: int = 0
    callback_status: int | None = None
    callback_failures: int = 0
    source_expired_times: int = 0
    retry_after: str | None = None
    #: Fail the source download for these page-agnostic file ids permanently.
    source_missing: set[str] = field(default_factory=set)


class FakeBackend:
    """State and behaviour of the fake. Mount :attr:`app` behind an ASGI transport."""

    def __init__(
        self,
        *,
        source_bytes: bytes = b"%PDF-1.4 fake",
        sources: dict[str, bytes] | None = None,
        public_base_url: str = "https://fake-backend.test",
    ) -> None:
        #: The host this fake tells the worker to come back to, for presigned download and
        #: upload URLs. Behind an ASGI transport any absolute URL works, because the
        #: transport ignores the host -- but `devtools/fake_backend_server.py` serves this
        #: same app over a real socket, where the URL has to actually resolve.
        self._public_base_url = public_base_url.rstrip("/")
        #: Optional per-fileId sources, so one fake can serve a PDF for Job A and a text
        #: file for Job B in the same test.
        self.sources = dict(sources or {})
        self.calls: list[Call] = []
        self.faults = FaultConfig()
        self.uploads: dict[str, dict[str, Any]] = {}
        self.confirmed: set[str] = set()
        self.metadata: dict[str, int] = {}
        self.page_reports: list[tuple[str, int, str, Any]] = []
        self.job_failures: list[tuple[str, str]] = []
        self.tokens_issued = 0
        self.source_bytes = source_bytes
        self._source_attempts = 0
        self.app = self._build_app()

    def pre_confirm(self, file_id: str, *, content_type: str, payload: bytes = b"x") -> None:
        """Record a file as already uploaded and confirmed by an earlier worker.

        Needed for crash-recovery tests: our ledger remembers ids from the attempt that
        died, and the fake rejects a `completed` naming a file it never confirmed -- which
        is correct, and is why the recovery path has to be set up on both sides.
        """
        self.uploads[file_id] = {
            "content_type": content_type,
            "size": len(payload),
            "received": payload,
        }
        self.confirmed.add(file_id)

    # ── Assertions the tests use ──────────────────────────────────────────────────

    def ops(self) -> list[str]:
        return [call.op for call in self.calls]

    def terminal_reports(self) -> dict[int, str]:
        """page -> 'completed' | 'failed'. Duplicates are visible as a raised error."""
        reports: dict[int, str] = {}
        for _job, page, kind, _payload in self.page_reports:
            if page in reports:
                raise AssertionError(f"page {page} reported twice: {reports[page]} then {kind}")
            reports[page] = kind
        return reports

    def artifacts_for(self, page: int) -> list[tuple[str, str]]:
        for _job, reported_page, kind, payload in self.page_reports:
            if reported_page == page and kind == "completed":
                return [(a["artifactType"], a["fileId"]) for a in payload["artifacts"]]
        return []

    # ── The app ───────────────────────────────────────────────────────────────────

    def _require_auth(self, authorization: str | None) -> None:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="Fake Reading First Backend")
        fake = self

        # ---- Keycloak ------------------------------------------------------------
        @app.post("/realms/reading-first/protocol/openid-connect/token")
        async def token(request: Request) -> Any:
            form = await request.form()
            fake.calls.append(Call(op="token", path=str(request.url.path)))
            if fake.faults.token_status:
                raise HTTPException(status_code=fake.faults.token_status)
            if form.get("grant_type") != "client_credentials":
                raise HTTPException(status_code=400, detail="unsupported grant_type")
            if not form.get("client_id") or not form.get("client_secret"):
                raise HTTPException(status_code=401, detail="missing credentials")
            fake.tokens_issued += 1
            return {
                "access_token": f"fake-token-{fake.tokens_issued}",
                "expires_in": 300,
                "token_type": "Bearer",
            }

        # ---- Source download (presigned stand-in) --------------------------------
        @app.get("/_source/{file_id}")
        async def source(file_id: str) -> Response:
            fake.calls.append(Call(op="source_get", path=f"/_source/{file_id}"))
            if file_id in fake.faults.source_missing:
                raise HTTPException(status_code=404)
            fake._source_attempts += 1
            if fake._source_attempts <= fake.faults.source_expired_times:
                # A presigned URL that has expired answers 403, not 404.
                raise HTTPException(status_code=403, detail="expired")
            payload = fake.sources.get(file_id, fake.source_bytes)
            media = (
                "text/plain"
                if file_id in fake.sources and not payload.startswith(b"%PDF-")
                else "application/pdf"
            )
            return Response(content=payload, media_type=media)

        @app.get("/api/files/{file_id}/get-download-url")
        async def get_download_url(
            file_id: str, authorization: str | None = Header(default=None)
        ) -> Any:
            fake._require_auth(authorization)
            fake.calls.append(
                Call(op="get_download_url", path=f"/api/files/{file_id}/get-download-url")
            )
            return {"url": f"{fake._public_base_url}/_source/{file_id}"}

        # ---- Upload cycle --------------------------------------------------------
        @app.post("/api/files/initialize-upload")
        async def initialize_upload(
            request: Request, authorization: str | None = Header(default=None)
        ) -> Any:
            fake._require_auth(authorization)
            body = await request.json()
            fake.calls.append(
                Call(op="initialize_upload", path="/api/files/initialize-upload", body=body)
            )
            if fake.faults.initialize_upload_failures > 0:
                fake.faults.initialize_upload_failures -= 1
                raise HTTPException(status_code=fake.faults.initialize_upload_status or 503)
            if fake.faults.initialize_upload_status:
                raise HTTPException(status_code=fake.faults.initialize_upload_status)

            content_type = body.get("contentType")
            if content_type not in ALLOWED_CONTENT_TYPES:
                # Hold our side of the contract to account.
                raise HTTPException(
                    status_code=422, detail=f"unsupported contentType {content_type}"
                )
            for required in ("fileName", "sizeInBytes", "folderId", "pathPrefix"):
                if body.get(required) in (None, ""):
                    raise HTTPException(status_code=422, detail=f"missing {required}")

            file_id = str(uuid.uuid4())
            fake.uploads[file_id] = {
                "content_type": content_type,
                "size": body["sizeInBytes"],
                "received": None,
            }
            return {
                "fileId": file_id,
                "upload": {
                    "url": f"{fake._public_base_url}/_upload/{file_id}",
                    "requiredHeaders": {
                        "Content-Type": content_type,
                        "Content-Length": str(body["sizeInBytes"]),
                    },
                },
            }

        @app.put("/_upload/{file_id}")
        async def put_upload(file_id: str, request: Request) -> Response:
            payload = await request.body()
            fake.calls.append(
                Call(
                    op="put_upload",
                    path=f"/_upload/{file_id}",
                    headers={k.lower(): v for k, v in request.headers.items()},
                )
            )
            if fake.faults.put_failures > 0:
                fake.faults.put_failures -= 1
                headers = (
                    {"Retry-After": fake.faults.retry_after} if fake.faults.retry_after else None
                )
                return Response(status_code=fake.faults.put_status or 503, headers=headers)
            if fake.faults.put_status:
                return Response(status_code=fake.faults.put_status)

            record = fake.uploads.get(file_id)
            if record is None:
                raise HTTPException(status_code=404, detail="unknown upload ticket")
            # A presigned PUT is signed over its headers, so a mismatch is a real error.
            declared = request.headers.get("content-length")
            if declared is not None and int(declared) != len(payload):
                raise HTTPException(status_code=400, detail="Content-Length mismatch")
            if request.headers.get("content-type") != record["content_type"]:
                raise HTTPException(status_code=400, detail="Content-Type mismatch")
            if "authorization" in {k.lower() for k in request.headers}:
                # Sending our Backend token to storage would leak it.
                raise HTTPException(
                    status_code=400, detail="presigned PUT must not carry Authorization"
                )
            record["received"] = payload
            return Response(status_code=200)

        @app.post("/api/files/{file_id}/confirm-upload")
        async def confirm_upload(
            file_id: str, authorization: str | None = Header(default=None)
        ) -> Response:
            fake._require_auth(authorization)
            fake.calls.append(
                Call(op="confirm_upload", path=f"/api/files/{file_id}/confirm-upload")
            )
            if fake.faults.confirm_failures > 0:
                fake.faults.confirm_failures -= 1
                raise HTTPException(status_code=fake.faults.confirm_status or 503)
            if fake.faults.confirm_status:
                raise HTTPException(status_code=fake.faults.confirm_status)
            record = fake.uploads.get(file_id)
            if record is None:
                raise HTTPException(status_code=404, detail="unknown file id")
            if record["received"] is None:
                raise HTTPException(status_code=409, detail="confirm before PUT")
            fake.confirmed.add(file_id)
            return Response(status_code=200)

        # ---- Job progress callbacks ----------------------------------------------
        def _maybe_fail_callback() -> None:
            if fake.faults.callback_failures > 0:
                fake.faults.callback_failures -= 1
                raise HTTPException(status_code=fake.faults.callback_status or 503)
            if fake.faults.callback_status:
                raise HTTPException(status_code=fake.faults.callback_status)

        @app.patch("/api/jobs/{job_id}/metadata")
        async def job_metadata(
            job_id: str, request: Request, authorization: str | None = Header(default=None)
        ) -> Response:
            fake._require_auth(authorization)
            body = await request.json()
            fake.calls.append(Call(op="metadata", path=f"/api/jobs/{job_id}/metadata", body=body))
            _maybe_fail_callback()
            if not isinstance(body.get("pagesCount"), int) or body["pagesCount"] < 1:
                raise HTTPException(status_code=422, detail="pagesCount must be a positive int")
            fake.metadata[job_id] = body["pagesCount"]
            return Response(status_code=200)

        @app.patch("/api/jobs/{job_id}/failed")
        async def job_failed(
            job_id: str, request: Request, authorization: str | None = Header(default=None)
        ) -> Response:
            fake._require_auth(authorization)
            body = await request.json()
            fake.calls.append(Call(op="job_failed", path=f"/api/jobs/{job_id}/failed", body=body))
            _maybe_fail_callback()
            if not body.get("reason"):
                raise HTTPException(status_code=422, detail="reason is required")
            fake.job_failures.append((job_id, body["reason"]))
            return Response(status_code=200)

        @app.patch("/api/jobs/{job_id}/{page_number}/completed")
        async def page_completed(
            job_id: str,
            page_number: int,
            request: Request,
            authorization: str | None = Header(default=None),
        ) -> Response:
            fake._require_auth(authorization)
            body = await request.json()
            fake.calls.append(
                Call(
                    op="page_completed",
                    path=f"/api/jobs/{job_id}/{page_number}/completed",
                    body=body,
                )
            )
            _maybe_fail_callback()
            artifacts = body.get("artifacts")
            if not isinstance(artifacts, list) or len(artifacts) != 2:
                raise HTTPException(status_code=422, detail="expected exactly 2 artifacts")
            for artifact in artifacts:
                if artifact.get("artifactType") not in {"Image", "Text", "Audio", "JSON"}:
                    raise HTTPException(status_code=422, detail="bad artifactType")
                if artifact["fileId"] not in fake.confirmed:
                    # The spec says completed is sent "after both file success".
                    raise HTTPException(
                        status_code=409,
                        detail=f"artifact {artifact['fileId']} was never confirmed",
                    )
            fake.page_reports.append((job_id, page_number, "completed", body))
            return Response(status_code=200)

        @app.patch("/api/jobs/{job_id}/{page_number}/failed")
        async def page_failed(
            job_id: str,
            page_number: int,
            request: Request,
            authorization: str | None = Header(default=None),
        ) -> Response:
            fake._require_auth(authorization)
            body = await request.json()
            fake.calls.append(
                Call(
                    op="page_failed",
                    path=f"/api/jobs/{job_id}/{page_number}/failed",
                    body=body,
                )
            )
            _maybe_fail_callback()
            if not body.get("reason"):
                raise HTTPException(status_code=422, detail="reason is required")
            fake.page_reports.append((job_id, page_number, "failed", body))
            return Response(status_code=200)

        return app
