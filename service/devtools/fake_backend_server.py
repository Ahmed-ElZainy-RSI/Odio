"""Serve the contract fake as a real HTTP server, for driving the service by hand.

The test suite mounts `tests/fakes/backend.py` behind an ASGI transport. That is the right
thing for tests and the wrong thing for a person: you cannot curl it, you cannot point a
worker process at it, and you cannot watch the protocol happen. This serves the *same*
fake -- deliberately not a second implementation, which would drift -- on a real socket.

    python devtools/fake_backend_server.py --pdf "../assets/PDF Samples/النجوم.pdf"

It stands in for all three things the service talks to: Keycloak's token endpoint, the
Backend's five book endpoints, and presigned blob storage. Extra `_`-prefixed routes exist
for the operator, not the protocol:

    GET  /_state                what the fake has seen: page count, reports, uploads, order
    GET  /_artifact/{file_id}   download exactly the bytes we uploaded
    POST /_faults               inject a fault: {"put_status": 500, "put_failures": 2}
    POST /_reset                forget everything

Nothing here is importable by the service, and none of it is on the V1 contract.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# `tests` is not an installed package; this script lives next to it on purpose.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import Request
from fastapi.responses import Response

from tests.fakes.backend import FakeBackend

DEFAULT_PORT = 9100


def build(
    *,
    pdf: Path | None,
    text: Path | None,
    public_base_url: str,
) -> FakeBackend:
    """One fake serving a PDF for Job A and a page of text for Job B.

    `fileId` selects which: `book-1` is the PDF, `page-text` is the text. Those are the
    same ids the e2e suite uses, so a job body copied from the docs works unchanged.
    """
    sources: dict[str, bytes] = {}
    if pdf is not None:
        sources["book-1"] = pdf.read_bytes()
    if text is not None:
        sources["page-text"] = text.read_bytes()

    fake = FakeBackend(
        source_bytes=sources.get("book-1", b"%PDF-1.4 fake"),
        sources=sources,
        public_base_url=public_base_url,
    )

    @fake.app.get("/_state")
    async def state() -> dict[str, Any]:
        """Everything the fake knows, in the order it learned it."""
        return {
            "pagesCountReported": fake.metadata,
            "terminalReports": _reports(fake),
            "jobFailures": [{"jobId": job, "reason": reason} for job, reason in fake.job_failures],
            "uploads": [
                {
                    "fileId": file_id,
                    "contentType": upload["content_type"],
                    "sizeInBytes": upload["size"],
                    "confirmed": file_id in fake.confirmed,
                    "bytesReceived": len(upload["received"] or b""),
                }
                for file_id, upload in fake.uploads.items()
            ],
            "tokensIssued": fake.tokens_issued,
            "callOrder": list(dict.fromkeys(fake.ops())),
            "callCount": len(fake.calls),
        }

    @fake.app.get("/_artifact/{file_id}")
    async def artifact(file_id: str) -> Response:
        """The exact bytes we uploaded. Useful for eyeballing a page image or the text."""
        upload = fake.uploads.get(file_id)
        if upload is None or upload["received"] is None:
            return Response(status_code=404, content=b"no such upload")
        return Response(content=upload["received"], media_type=upload["content_type"])

    @fake.app.post("/_faults")
    async def faults(request: Request) -> dict[str, Any]:
        """Inject a fault at runtime, e.g. {"put_status": 500, "put_failures": 2}.

        The field names are `FaultConfig`'s. This is how you watch a retry happen without
        writing a test.
        """
        body = await request.json()
        unknown = [key for key in body if not hasattr(fake.faults, key)]
        if unknown:
            return {"error": f"unknown fault fields: {unknown}"}
        for key, value in body.items():
            setattr(fake.faults, key, set(value) if isinstance(value, list) else value)
        return {"faults": {k: _plain(v) for k, v in vars(fake.faults).items()}}

    @fake.app.post("/_reset")
    async def reset() -> dict[str, str]:
        fake.calls.clear()
        fake.uploads.clear()
        fake.confirmed.clear()
        fake.metadata.clear()
        fake.page_reports.clear()
        fake.job_failures.clear()
        fake.tokens_issued = 0
        return {"status": "reset"}

    return fake


def _reports(fake: FakeBackend) -> dict[str, str]:
    """Page -> verdict, tolerating the duplicate that `terminal_reports()` refuses.

    The assertion helper raises on a double report, which is correct in a test and
    unhelpful in a server whose job is to show you what happened.
    """
    out: dict[str, str] = {}
    for _job, page, kind, _payload in fake.page_reports:
        key = str(page)
        out[key] = kind if key not in out else f"{out[key]}+{kind} (DOUBLE REPORT)"
    return out


def _plain(value: object) -> object:
    return sorted(value) if isinstance(value, set) else value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--pdf", type=Path, help="served as the source for fileId 'book-1'")
    parser.add_argument("--text", type=Path, help="served as the source for fileId 'page-text'")
    parser.add_argument(
        "--public-base-url",
        default=None,
        help="how the worker should reach this server (default http://<host>:<port>)",
    )
    args = parser.parse_args()

    public = args.public_base_url or f"http://{args.host}:{args.port}"
    for label, path in (("--pdf", args.pdf), ("--text", args.text)):
        if path is not None and not path.exists():
            parser.error(f"{label}: no such file: {path}")

    fake = build(pdf=args.pdf, text=args.text, public_base_url=public)

    print(f"fake Backend + Keycloak + storage on {public}", flush=True)
    known = sorted(fake.sources) or "(none -- pass --pdf and/or --text)"
    print(f"  sources     : {known}", flush=True)
    realm = "/realms/reading-first/protocol/openid-connect/token"
    print(f"  token url   : {public}{realm}", flush=True)
    print(f"  inspect     : curl -s {public}/_state | python -m json.tool", flush=True)

    import uvicorn

    uvicorn.run(fake.app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
