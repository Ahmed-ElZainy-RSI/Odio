#!/usr/bin/env bash
# Bring up the whole thing locally: fake Backend + API + worker, three real processes
# talking over real sockets. No network, no credentials, no paid API calls.
#
#     ./devtools/local_stack.sh              # start, wait for health, print what to curl
#     ./devtools/local_stack.sh stop         # stop everything
#     ./devtools/local_stack.sh logs worker  # tail one process
#
# Why `fake.localhost` and not `127.0.0.1`: the API refuses a `sourceDownloadUrl` whose
# host is literally `localhost`/`127.0.0.1`/`::1` -- an SSRF control that should NOT be
# switched off to make a demo work. `fake.localhost` resolves to loopback (RFC 6761)
# without being on that blocklist, so the control stays on and the loop still runs.
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$SERVICE_DIR/.." && pwd)"
RUN_DIR="${AI_ENGINE_RUN_DIR:-/tmp/ai-engine-local}"
PYTHON="${PYTHON:-python}"
API_PORT="${API_PORT:-9099}"
FAKE_PORT="${FAKE_PORT:-9100}"
PDF="${PDF:-$REPO_ROOT/assets/PDF Samples/النجوم.pdf}"

export BACKEND_BASE_URL="http://fake.localhost:$FAKE_PORT"
export TOKEN_URL="$BACKEND_BASE_URL/realms/reading-first/protocol/openid-connect/token"
export AI_ENGINE_CLIENT_ID=reading-first-ocr-engine
export AI_ENGINE_CLIENT_SECRET=dev-secret
export SOURCE_URL_ALLOWED_HOSTS=localhost
# The fake speaks http, on loopback. Both of these are LOCAL ONLY -- in production the
# scheme check and the resolve-then-reject check are the SSRF controls that matter.
export SOURCE_URL_REQUIRE_HTTPS=false
export SOURCE_URL_RESOLVE_AND_CHECK=false
export JOB_STORE=sqlite
export JOB_STORE_PATH="$RUN_DIR/jobs.db"
export SCRATCH_DIR="$RUN_DIR/scratch"
export LEGACY_REPO_ROOT="$REPO_ROOT"
export OCR_ENGINE="${OCR_ENGINE:-auto}"
export TTS_ENGINE="${TTS_ENGINE:-synthetic}"
export AI_ENGINE_JSON_LOGS=false
export AI_ENGINE_LOG_LEVEL="${AI_ENGINE_LOG_LEVEL:-INFO}"
export AI_ENGINE_PORT="$API_PORT"

stop() {
  for name in fake api worker; do
    if [[ -f "$RUN_DIR/$name.pid" ]]; then
      kill "$(cat "$RUN_DIR/$name.pid")" 2>/dev/null || true
      rm -f "$RUN_DIR/$name.pid"
    fi
  done
  echo "stopped"
}

case "${1:-start}" in
  stop) stop; exit 0 ;;
  logs) tail -f "$RUN_DIR/${2:-worker}.log" ; exit 0 ;;
  start) ;;
  *) echo "usage: $0 [start|stop|logs <fake|api|worker>]" >&2; exit 2 ;;
esac

stop >/dev/null 2>&1 || true
# A fresh database each time: the schema is versioned, and a stale file from an older
# build would fail the version check rather than migrate.
rm -rf "$RUN_DIR"
mkdir -p "$RUN_DIR/scratch"

cd "$SERVICE_DIR"
"$PYTHON" devtools/fake_backend_server.py --port "$FAKE_PORT" --pdf "$PDF" \
  > "$RUN_DIR/fake.log" 2>&1 & echo $! > "$RUN_DIR/fake.pid"
"$PYTHON" -c 'from ai_engine.main import run_api; run_api()' \
  > "$RUN_DIR/api.log" 2>&1 & echo $! > "$RUN_DIR/api.pid"
"$PYTHON" -c 'from ai_engine.main import run_worker; run_worker()' \
  > "$RUN_DIR/worker.log" 2>&1 & echo $! > "$RUN_DIR/worker.pid"

for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1 \
     && curl -sf "http://127.0.0.1:$FAKE_PORT/_state" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -sf "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1; then
  echo "the API did not come up. Last 20 lines of $RUN_DIR/api.log:" >&2
  tail -20 "$RUN_DIR/api.log" >&2
  exit 1
fi

cat <<EOF

  up.  logs in $RUN_DIR/{fake,api,worker}.log

    API           http://127.0.0.1:$API_PORT      (/health /ready /metrics /ops/budget /docs)
    fake Backend  http://127.0.0.1:$FAKE_PORT      (/_state /_artifact/{fileId} /_faults /_reset)
    store         $JOB_STORE_PATH

  submit a book:

    JOB=\$(uuidgen | tr 'A-Z' 'a-z')
    curl -s -X POST http://127.0.0.1:$API_PORT/api/jobs -H 'content-type: application/json' -d '{
      "jobId": "'\$JOB'",
      "jobType": "PAGES_IMG_AND_TEXT_EXTRACTION",
      "fileId": "book-1",
      "folderId": "books/reading-first/grade-1",
      "pathPrefix": "s3-prefix/2026/09",
      "sourceDownloadUrl": "http://fake.localhost:$FAKE_PORT/_source/book-1"
    }'

    curl -s http://127.0.0.1:$API_PORT/api/jobs/\$JOB      # our view
    curl -s http://127.0.0.1:$FAKE_PORT/_state             # the Backend's view

  break it on purpose, then watch it recover:

    curl -s -X POST http://127.0.0.1:$FAKE_PORT/_faults -H 'content-type: application/json' \\
      -d '{"put_failures": 3, "confirm_failures": 2, "callback_failures": 2, "source_expired_times": 1}'

  stop: ./devtools/local_stack.sh stop

EOF
