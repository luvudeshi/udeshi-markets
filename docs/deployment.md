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
  ships with `vercel deploy --prod`.

## The API (Render)

In the Render dashboard, choose **New → Blueprint**, then this repository. Render reads
`render.yaml`, builds `backend/Dockerfile` (compiling the engine), and checks
`/api/health`.
* **Repository access.** Render's GitHub app is installed on this repository only.
* **Deploys.** The service is blueprint-managed, and every push to `main` redeploys it.

Its settings:

| Variable | Value | Why |
| --- | --- | --- |
| `PORT` | `8000` | The image listens on 8000; Render routes traffic to `PORT`. |
| `UDESHI_CORS_ORIGINS` | `https://udeshi-markets.vercel.app` | The terminal's origin. |
| `UDESHI_MAX_SESSIONS` | `4` | Live simulations kept in memory (512 MB on the free plan). |
| `UDESHI_EXPERIMENT_WORKERS` | `1` | One experiment worker process, for the same reason. |

**What the free plan means:**
* **Idle instances sleep**, and the first request after that waits for the instance to start.
* **Restarts lose everything in memory.** Live simulations do, and so do experiments, because
  no database is configured. Setting `UDESHI_DATABASE_URL` to a PostgreSQL URL
  (`postgresql+psycopg://…`) keeps experiments.
