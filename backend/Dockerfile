# syntax=docker/dockerfile:1.7
# Builds the API service image. Build context: the repository root.
#   docker build -f backend/Dockerfile -t udeshi-api .
#
# Layers go from least to most frequently changed. Third-party dependencies are reinstalled
# only when uv.lock changes, the C++ engine is recompiled only when engine/, python/ or the
# build files change, and an edit under backend/ rebuilds only the API wheel.

# ---- Requirements: pinned, hashed third-party dependencies, from the lock file alone --------
FROM python:3.12-slim-bookworm AS requirements
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/
RUN mkdir /requirements \
 && uv export --frozen --no-dev --all-packages --no-emit-workspace --quiet \
      -o /requirements/requirements.txt

# ---- Build: compile the engine extension, then build the API wheel ---------------------------
FROM python:3.12-slim-bookworm AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential cmake ninja-build \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
# The uv cache is a mount on another filesystem, so uv copies instead of hard-linking.
ENV UV_LINK_MODE=copy
WORKDIR /src
# uv build reads the pinned build environments from pyproject.toml; it does not need uv.lock,
# so a dependency change does not recompile the engine.
COPY pyproject.toml README.md CMakeLists.txt CMakePresets.json ./
COPY backend/pyproject.toml backend/
COPY engine engine
COPY python python
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --out-dir /dist .
COPY backend backend
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --out-dir /dist backend

# ---- Runtime: hash-checked dependencies and the two project wheels; non-root ---------------
FROM python:3.12-slim-bookworm AS runtime
RUN useradd --create-home --uid 10001 app
RUN --mount=type=bind,from=requirements,source=/requirements,target=/requirements \
    --mount=type=cache,target=/root/.cache/pip \
    pip install --require-hashes -r /requirements/requirements.txt
# Mounted, not copied, so the wheels leave nothing behind in the image's layers.
RUN --mount=type=bind,from=build,source=/dist,target=/dist \
    pip install --no-cache-dir --no-deps /dist/*.whl
USER app
ENV UDESHI_HOST=0.0.0.0 \
    UDESHI_PORT=8000 \
    PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["python", "-m", "udeshi_api"]
