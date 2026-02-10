# ==========================================================
# local-llm — llama.cpp inference server for Lancelot
# ==========================================================
# Serves the local utility model via llama-server (HTTP API).
# Model weights are mounted at runtime, never baked into image.
#
# Fix Pack V8: CUDA runtime image + pre-built CUDA wheel
# Uses pre-built llama-cpp-python wheel (no CUDA compile needed)
# ==========================================================

FROM nvidia/cuda:12.3.2-runtime-ubuntu22.04 AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Install Python 3.11 and minimal dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3-pip \
    curl \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Make python3.11 the default python3
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# Create non-root user
RUN groupadd -r llm && useradd -r -g llm -m -d /home/llm -s /bin/bash llm

WORKDIR /home/llm/app

# Install llama-cpp-python with pre-built CUDA 12.3 wheel (no compile needed)
# Then install remaining dependencies from requirements
COPY requirements-llm.txt .
RUN pip install --no-cache-dir llama-cpp-python \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu123 \
    && pip install --no-cache-dir fastapi uvicorn "pyyaml>=6.0"

# Copy the local_models package and server code
COPY __init__.py .
COPY lockfile.py .
COPY smoke_test.py .
COPY fetch_model.py .
COPY server.py .
COPY models.lock.yaml .
COPY prompts/ ./prompts/

# Create weights directory (model mounted at runtime)
RUN mkdir -p /home/llm/models && chown -R llm:llm /home/llm

USER llm

# Health check — hits the /health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

EXPOSE 8080

CMD ["python3", "server.py"]
