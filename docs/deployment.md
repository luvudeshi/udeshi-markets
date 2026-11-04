# Deployment

The terminal and the API are deployed separately, because they need different kinds of
hosting:

| Part | Where | Why there |
| --- | --- | --- |
| Terminal (`frontend/`) | Vercel project `udeshi-markets`: https://udeshi-markets.vercel.app | A Next.js app with server-rendered pages, which is exactly what Vercel hosts. |
| API (`backend/`, with the C++ engine) | Render web service `udeshi-markets-api`: https://udeshi-markets-api.onrender.com, from [`render.yaml`](../render.yaml) | It needs a long-lived process: the live runner is an asyncio task, simulations live in memory, the stream is a WebSocket, and experiments run in worker processes. Serverless functions provide none of these. |

The browser talks to the API directly, over HTTPS and WSS. The terminal learns the API's
address from `UDESHI_API_URL`, which the server reads at request time (see
[frontend.md](frontend.md)). The API allows the terminal's origin in `UDESHI_CORS_ORIGINS`.

## The terminal (Vercel)

```bash
cd frontend
vercel link --project udeshi-markets              # once
vercel env add UDESHI_API_URL production          # the API's public URL
vercel deploy --prod
```

* **Uploads.** `frontend/.vercelignore` keeps local secrets (`.env*.local`, including the
  token `vercel link` writes) and build or test output out of every upload.
* **Configuration.** Production's `UDESHI_API_URL` is `https://udeshi-markets-api.onrender.com`.
  Changing it takes effect with the next deployment.
* **No Git integration.** The project is not connected to Git, so a change to the terminal
  ships with `vercel deploy --prod`. Run it from `frontend/`: a deploy started from the
  repository root fails, because it finds no Next.js app there.
* **Hobby plan.** The terminal runs on Vercel's free plan, which is for personal,
  non-commercial use.
  * The live production deployment is never removed by retention.
  * Going over a usage limit pauses the project rather than billing. A paused project stays
    paused until it is resumed from the dashboard. The limits (1M function invocations and
    4 h of active CPU a month) are shared by every project on the account.
  * A deprecated Node.js version blocks new deploys, not the running one. `engines` asks for
    Node `>=22.12`, which builds on 26.x today.

## The API (Render)

In the Render dashboard, choose **New → Blueprint**, then this repository. Render reads
`render.yaml`, builds `backend/Dockerfile` (compiling the engine), and checks
`/api/health`.
* **Repository access.** Render's GitHub app is installed on this repository only.
* **Deploys.** The service is blueprint-managed. A push to `main` redeploys it only when it
  changes what the image is built from (`buildFilter` in `render.yaml`): the backend, the
  engine, the Python package and their build files. Frontend, docs, tools and test changes do
  not, because every redeploy restarts the service and loses its in-memory simulations. Put
  `[skip render]` in a commit message to skip one that would.
* **A failed deploy changes nothing.** If a build or its health check fails, the previous
  deploy keeps serving.

Its settings:

| Variable | Value | Why |
| --- | --- | --- |
| `PORT` | `8000` | The image listens on 8000; Render routes traffic to `PORT`. |
| `UDESHI_CORS_ORIGINS` | `https://udeshi-markets.vercel.app` | The terminal's origin. |
| `UDESHI_MAX_SESSIONS` | `4` | Live simulations kept in memory (512 MB on the free plan). |
| `UDESHI_EXPERIMENT_WORKERS` | `1` | One experiment worker process, for the same reason. |
| `UDESHI_MAX_BARS` | `100000` | Bars one live simulation may keep, about 180 bytes each. This allows five symbols' 1-second bars for 5.5 simulated hours, or a day at 5-second bars. Above it, creation is refused with a request for wider bars. |

**Memory does not grow with a run's length.** Measured with five symbols and 26 agents:
* **Live observer.** A live session's analytics observer keeps only running aggregates
  (`MarketObserver(record=False)`). With the full record, peak RSS grew about 28 MB per
  simulated hour, enough to exhaust 512 MB within a simulated day.
* **What remains.** Without the record, peak RSS grows about 5 MB per simulated hour, mostly
  bars, which `UDESHI_MAX_BARS` caps. The trade tape and order histories are bounded.
* **Experiment runs.** A run keeps one bar per instrument, a 1,000-trade tape and no
  observer record. Its digest and metrics match a run with the full record.

**What the free plan means** (Render's docs and terms, checked 2026-11-04):
* **Idle instances sleep.** After 15 minutes without an inbound request or WebSocket message
  the instance stops. The next request waits about a minute for it to start. The stream
  only sends to the browser, so watching alone does not count as activity. The terminal's
  polling while a simulation runs does.
* **Restarts lose everything in memory.** Render may restart a free instance at any time,
  and sleeping and redeploying also restart it. Live simulations are lost, and so are
  experiments, because no database is configured. Setting `UDESHI_DATABASE_URL` to a
  PostgreSQL URL (`postgresql+psycopg://…`) keeps experiments.
* **Render's free PostgreSQL is not a fix.** It expires 30 days after creation and is
  deleted 14 days after that.
* **Monthly allowances are per workspace:**
  * **Instance hours:** 750 free instance hours. One service awake all month uses at
    most 744. Running out suspends every free service in the workspace until the next
    month.
  * **Outbound bandwidth:** 5 GB, and WebSocket data counts. Without a payment method,
    going over suspends the workspace's services until the next month; with one, it costs
    $0.15 per GB. This is the allowance most likely to run out.
  * **Build minutes:** 500. When they run out, builds stop until the next month, and the
    running service is unaffected.
* **Free plans carry no guarantee.** Render may change or end the free plan without notice.
  The cheapest paid instance, Starter at $7 a month, also has 512 MB. It removes sleeping,
  the instance-hour cap and the restart-at-any-time rule, but not the bandwidth allowance.
