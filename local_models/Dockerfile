# ==========================================================
# local-llm — llama.cpp inference server for Lancelot
# ==========================================================
# Serves the local utility model via llama-server (HTTP API).
# Model weights are mounted at runtime, never baked into image.
#
# Fix Pack V8: CUDA base image for GPU offload (GTX 1080+)
# ==========================================================

FROM nvidia/cuda:12.3.2-runtime-ubuntu22.04 AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Install Python 3.11 and build dependencies for llama-cpp-python
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3-pip \
    build-essential \
    cmake \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Make python3.11 the default python3
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# Create non-root user
RUN groupadd -r llm && useradd -r -g llm -m -d /home/llm -s /bin/bash llm

WORKDIR /home/llm/app

# Install Python dependencies with CUDA support for llama-cpp-python
COPY requirements-llm.txt .
ENV CMAKE_ARGS="-DGGML_CUDA=on"
ENV FORCE_CMAKE=1
RUN pip install --no-cache-dir --break-system-packages -r requirements-llm.txt

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
