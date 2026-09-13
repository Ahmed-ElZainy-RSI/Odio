# Deployment Architecture

## 1. Topology

```mermaid
flowchart TB
    subgraph THEIRS["Backend team's infrastructure"]
        BE["Book APIs :6060"]
        S3["Object storage<br/>presigned GET / PUT"]
        KC["Keycloak :9090<br/>realm reading-first"]
    end

    subgraph OURS["AI Engine deployment"]
        direction TB
        LB{{"ingress<br/>(internal only)"}}
        API1["api :9099"]
        API2["api :9099<br/>(replica 2)"]
        PG[("postgres :5432")]
        MIG["migrate<br/>(one-shot, run first)"]
        W1["worker 1"]
        W2["worker N"]
        RC["reconciler"]
        VS[("scratch volume<br/>5 GB per worker")]
        VM[("model-cache volume<br/>shared, persistent")]
    end

    VENDOR["Gemini · LightOn"]

    BE -->|"POST /api/jobs"| LB
    LB --> API1 & API2
    API1 & API2 --> PG
    MIG -.->|"must complete before<br/>api or worker start"| PG
    PG --> W1 & W2 & RC
    W1 & W2 --- VS
    W1 & W2 --- VM
    W1 & W2 --> VENDOR
    W1 & W2 --> S3
    W1 & W2 & RC --> BE
    W1 & W2 & RC --> KC
    API1 -.->|"JWKS"| KC
```

One image, three entrypoints. Nothing else.

## 2. Container inventory

| Container | Role | Command | Ports | Volumes | Health check | Restart | Replicas |
|---|---|---|---|---|---|---|---|
| `migrate` | schema | `ai-engine-migrate` | — | — | exit 0 | `on-failure` | 1, one-shot |
| `api` | HTTP | `ai-engine-api` | `9099` | none | `GET /ready` | `unless-stopped` | **1–2** |
| `worker` | compute | `ai-engine-worker` | none | scratch (rw), model cache (rw) | `GET /ready` on an internal port, or a liveness file | `unless-stopped` | **N (start at 2)** |
| `reconciler` | INVARIANT-1 | `ai-engine-reconciler` | none | none | liveness file | `unless-stopped` | **exactly 1** (it takes an advisory lock, so >1 is harmless but pointless) |
| `postgres` | state | `postgres:16` | `5432` (internal) | pgdata | `pg_isready` | `unless-stopped` | 1 |

The API needs almost no capacity: it does one indexed insert per request and is not on any bottleneck
path ([05-target-architecture.md](05-target-architecture.md) §7). Two replicas are for rolling deploys,
not throughput.

## 3. `docker-compose.yml`

```yaml
name: ai-engine

x-common: &common
  image: reading-first/ai-engine:${TAG:-1.0.0}
  env_file: [./secrets/ai-engine.env]      # host path, OUTSIDE the image
  environment:
    DATABASE_URL: postgresql://ai_engine:${DB_PASSWORD}@postgres:5432/ai_engine
    AI_ENGINE_VERSION: ${TAG:-1.0.0}
  restart: unless-stopped
  user: "10001:10001"                       # non-root
  read_only: true                           # rootfs read-only; only volumes are writable
  tmpfs: [/tmp]
  logging:
    driver: json-file
    options: { max-size: "50m", max-file: "5" }

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: ai_engine
      POSTGRES_USER: ai_engine
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ai_engine -d ai_engine"]
      interval: 10s
      timeout: 3s
      retries: 10
    restart: unless-stopped

  migrate:
    <<: *common
    command: ["ai-engine-migrate"]
    environment:
      DATABASE_URL: postgresql://ai_engine_migrator:${DB_MIGRATOR_PASSWORD}@postgres:5432/ai_engine
    depends_on:
      postgres: { condition: service_healthy }
    restart: on-failure
    read_only: false

  api:
    <<: *common
    command: ["ai-engine-api"]
    environment:
      AI_ENGINE_ROLE: api
      AI_ENGINE_PORT: "9099"
    ports: ["9099:9099"]
    depends_on:
      postgres: { condition: service_healthy }
      migrate:  { condition: service_completed_successfully }
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9099/ready',timeout=3).status==200 else 1)"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 20s
    deploy:
      replicas: 2
      resources:
        limits:   { cpus: "1.0", memory: 512M }
        reservations: { cpus: "0.25", memory: 256M }

  worker:
    <<: *common
    command: ["ai-engine-worker"]
    environment:
      AI_ENGINE_ROLE: worker
      SCRATCH_DIR: /var/tmp/ai-engine
      MODEL_CACHE_DIR: /var/cache/ai-engine/models
      HF_HOME: /var/cache/ai-engine/models/hf
      WORKER_JOB_CONCURRENCY: "2"
      WORKER_PAGE_CONCURRENCY: "4"
    volumes:
      - scratch:/var/tmp/ai-engine
      - modelcache:/var/cache/ai-engine/models       # PERSISTENT: 464 MB of weights
    depends_on:
      postgres: { condition: service_healthy }
      migrate:  { condition: service_completed_successfully }
    stop_grace_period: 120s                          # let in-flight page tasks finish
    deploy:
      replicas: 2
      resources:
        limits:   { cpus: "4.0", memory: 4G }
        reservations: { cpus: "1.0", memory: 2G }

  reconciler:
    <<: *common
    command: ["ai-engine-reconciler"]
    environment:
      AI_ENGINE_ROLE: reconciler
      RECONCILE_INTERVAL_SECONDS: "60"
    depends_on:
      postgres: { condition: service_healthy }
      migrate:  { condition: service_completed_successfully }
    deploy:
      replicas: 1
      resources:
        limits: { cpus: "0.5", memory: 256M }

volumes:
  pgdata:
  scratch:
  modelcache:
```

## 4. No GPU is required

Stated as a deployment fact with its proof, because it materially changes the cost of hosting this
service:

| Local model | Device | Evidence |
|---|---|---|
| PaddleOCR PP-OCRv5 mobile det + Arabic rec | **CPU** | `PADDLE_DEVICE` defaults to `"cpu"` at `walidapro/ocr_verifier/config.py:32`, and `enable_mkldnn=False` at `bbox_detector.py:52`. Setting it to `gpu` would require a CUDA `paddlepaddle` build that nothing in this repo pins. |
| faster-whisper `small` | **CPU, hardcoded** | `WhisperModel(size, device="cpu", compute_type="int8")` at `src/pipeline/align_whisper.py:22`. **Device and compute type are not parameterized** — there is no code path to CUDA without an edit. |
| `arabic-diacritizer` BiLSTM | **CPU** | `src/pipeline/tashkel.py:14-17` documents it as "CPU-only, 6.6% DER on Tashkeela … no extra infrastructure" |

The expensive models — Gemini and LightOn — are remote. The engine is a CPU-bound orchestrator with a
464 MB local ASR model.

## 5. Resource sizing

### Worker

The binding constraint is memory, and it comes from a specific stack of things:

```text
faster-whisper small, int8, resident            ~500 MB   (464 MB weights, measured)
PaddleOCR weights (verified tier only)          ~100 MB
arabic-diacritizer BiLSTM                        ~50 MB
Python + deps baseline                          ~250 MB
per concurrent page task:
   decoded page image, RGB @ 220 DPI              14 MB    (1863x2573x3)
   PCM audio buffer                              1.3 MB    (median), 8.9 MB worst observed
   x WORKER_PAGE_CONCURRENCY=4                   ~96 MB    worst case
                                               ---------
                                                ~1.0 GB
```

**Recommendation: 4 GB limit, 2 GB reservation, 4 CPU.** The 4× headroom over the computed figure covers
an uncapped source PDF (`Q-08`), the verified tier's unbounded crop writes (`cropper.py:205` writes 7
PNGs *per suspicious word*), and fragmentation.

CPU: alignment is single-threaded per page at **RTF ≈ 0.20 measured**, so 4 concurrent page tasks can
saturate ~4 cores during the alignment phase.

**Scratch: 5 GB per worker.** The computed high-water mark is ~100 MB
([11-data-and-storage.md](11-data-and-storage.md) §4); the headroom is for the same three reasons.

### API

512 MB, 1 CPU. It does an indexed insert and a JWT verification. Nothing else.

### Postgres

The measured growth is **~128 rows per book** ([11-data-and-storage.md](11-data-and-storage.md) §4), so
at 100 books/day this is ~4.7 M rows/year — **megabytes**. 2 GB memory and 20 GB disk is generous. Do
not partition, shard, or reach for anything else.

## 6. Startup dependencies and ordering

```text
1. postgres          → healthy (pg_isready)
2. migrate           → exit 0     [BLOCKS both api and worker]
3. api  ∥  worker    → start in parallel
     api    ready when: Postgres reachable + JWKS available
     worker ready when: Postgres reachable + local models WARMED
4. reconciler        → start (no ordering constraint beyond migrate)
```

**The model warm-up is the one non-obvious ordering hazard.** Measured cold costs:
`import pipeline.align_whisper` = **2.56 s**, `_get_model()` from a warm disk cache = **1.54 s** — and
that is *after* the 464 MB download. If the `modelcache` volume is missing or empty, every worker start
becomes a multi-minute, bandwidth-bound event, and readiness will (correctly) fail throughout.

Consequences to design for:

- The `modelcache` volume is **not optional**. Losing it is a self-inflicted outage.
- Better still, pre-bake the weights into the image at build time. That makes startup deterministic and
  removes a network dependency from the boot path — and it also pins the model, which matters for
  reproducibility and supply-chain reasons ([12-security.md](12-security.md) §8).
- Worker `/ready` must gate on warm-up, so an unwarmed worker does not claim a job and then fail every
  page while the model loads.

## 7. Scaling and rollout

| Action | How | Notes |
|---|---|---|
| More throughput | `docker compose up -d --scale worker=N` | Stateless; they coordinate through Postgres `SKIP LOCKED`. **But check the provider budget first** — the first bottleneck is provider quota, not workers ([05-target-architecture.md](05-target-architecture.md) §7). |
| More API capacity | `--scale api=N` | Almost never needed. |
| Rolling deploy | Replace workers one at a time | See graceful shutdown below. |
| Rollback (code) | Redeploy the previous image tag; migrations must be backward-compatible for one version | Standard. |
| **Rollback (model)** | **Change a config value and restart workers.** No code deploy. | This is why model ids must be configuration, not the constants they are today ([08-ai-integration.md](08-ai-integration.md) §6). |

### Graceful shutdown

`stop_grace_period: 120s`, and on `SIGTERM` a worker:

1. Stops claiming new jobs and new page tasks.
2. Lets in-flight page tasks run to their natural end, including their upload cycles and their outbox
   writes.
3. Drains the outbox one final time.
4. Exits.

If a page task cannot finish within the grace period, the container is killed and the lease expires —
and the page is re-claimed by another worker, with the reconciler as the final backstop. **So a hard kill
is safe, and that is by design**: the correctness of INVARIANT-1 does not depend on cooperative
shutdown. Graceful shutdown is an optimization that avoids re-doing paid work, not a correctness
mechanism. Worked trace in [09-async-jobs.md](09-async-jobs.md) §7.5.

## 8. Environments

| | Local dev | Staging | Production |
|---|---|---|---|
| Backend | **The contract fake** (`tests/fakes/backend/`) | The real Backend's staging | Real |
| Keycloak | Fake `/token` + `/certs` | Real staging realm | Real `reading-first` realm |
| Storage | Fake presigned endpoints | Real staging bucket | Real |
| AI providers | Stubbed by default; real with a flag | Real, low budget | Real |
| Postgres | Compose | Managed or Compose | Managed, with PITR |
| Workers | 1 | 1 | 2+ |
| `ENABLE_VERIFIED_TIER` | `true` for experimentation | `false` | `false` |
| Model cache | Host bind-mount | Volume | Baked into the image |
| TLS | off | on | **on, end to end** (`Q-15`) |

The contract fake is what makes local development possible at all: without it, nothing can be tested
against the V1 protocol until the Backend team is available.

## 9. Pre-flight checklist for the first integration with the Backend team

Run through this **before** the first joint test, not during it.

**Answers needed first** (from [22-open-questions.md](22-open-questions.md)):

- [ ] `Q-01` — inbound token validation: which client, which `aud`/`azp`, JWKS URL. **Blocks auth
      entirely.**
- [ ] `Q-04` — is the `Text` artifact raw or final diacritized text.
- [ ] `Q-05` — `pageNumber` is 1-based (we assume yes).
- [ ] `Q-02`, `Q-03` — are `confirm-upload` and the `completed` PATCH idempotent.

**Configuration:**

- [ ] `BACKEND_BASE_URL`, `TOKEN_URL`, `AI_ENGINE_CLIENT_ID`, `AI_ENGINE_CLIENT_SECRET` set and verified
      with a real token fetch.
- [ ] `BACKEND_ALLOWED_CLIENT_IDS` set to the Backend's actual Keycloak client.
- [ ] `SOURCE_URL_ALLOWED_HOSTS` set to the real storage hostnames. **Without this the SSRF guard either
      blocks every job or is disabled.**
- [ ] `ENABLE_VERIFIED_TIER=false`.
- [ ] Model-cache volume mounted and warm.

**Network reachability matrix** — note the PDF's own mixed schemes:

| From | To | Port | Protocol | Direction |
|---|---|---|---|---|
| Backend | AI Engine `api` | 9099 | HTTPS | inbound to us |
| AI Engine `worker` | Backend | 6060 | HTTPS | outbound |
| AI Engine `worker` | Keycloak | 9090 | **the PDF shows `http://`** — `Q-15` | outbound |
| AI Engine `api` | Keycloak (JWKS) | 9090 | same | outbound |
| AI Engine `worker` | Object storage | 443 | HTTPS | outbound |
| AI Engine `worker` | `generativelanguage.googleapis.com`, `api.lighton.ai` | 443 | HTTPS | outbound |
| AI Engine | Postgres | 5432 | — | internal only |

Everything else denied — which is also a second layer of SSRF defence
([12-security.md](12-security.md) §5).

**Smoke sequence:**

- [ ] `GET /health` → 200; `GET /ready` → 200 on every role.
- [ ] Fetch a Backend token with the real credentials → 200, `expires_in: 300`.
- [ ] `POST /api/jobs` with a Backend-issued token → **202**.
- [ ] The same `jobId` again → **202, `duplicate: true`**, and no second execution.
- [ ] A one-page PDF end to end: `metadata` → image → text → `completed`. Verify with
      `GET /api/jobs/{jobId}` that exactly one page reported.
- [ ] A Job B for that page: audio → json → `completed`.
- [ ] A deliberately corrupt PDF → whole-job `/failed` with a safe reason, and **no page callbacks**.
- [ ] Confirm `ai_engine_outbox_pending == 0` and
      `ai_engine_pages_total{outcome="force_failed"} == 0`.
