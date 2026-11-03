# syntax=docker/dockerfile:1.7
# Builds the API service image. Build context: the repository root.
#   docker build -f backend/Dockerfile -t udeshi-api .

# ---- Build: compile the C++ engine extension and build both wheels --------------------------
FROM python:3.12-slim-bookworm AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential cmake ninja-build \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md CMakeLists.txt CMakePresets.json ./
COPY engine engine
COPY python python
COPY backend backend
RUN uv build --wheel --out-dir /dist . \
 && uv build --wheel --out-dir /dist backend \
 && uv export --frozen --no-dev --all-packages --no-emit-workspace -o /dist/requirements.txt

# ---- Runtime: pinned, hash-checked dependencies; non-root ------------------------------------
FROM python:3.12-slim-bookworm AS runtime
RUN useradd --create-home --uid 10001 app
COPY --from=build /dist /dist
RUN pip install --no-cache-dir --require-hashes -r /dist/requirements.txt \
 && pip install --no-cache-dir --no-deps /dist/*.whl \
 && rm -rf /dist
USER app
ENV UDESHI_HOST=0.0.0.0 \
    UDESHI_PORT=8000 \
    PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["python", "-m", "udeshi_api"]
