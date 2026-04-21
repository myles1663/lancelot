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

ARG LOCAL_LLM_WHEEL_VARIANT=cpu
ARG LOCAL_LLM_WHEEL_VERSION=0.3.19

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Install Python 3.11 and minimal dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    ninja-build \
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

# Install llama-cpp-python from the selected wheel channel, then install the
# remaining local-model service dependencies. CPU is the safe default; CUDA is
# an explicit opt-in because some hosts can load the model but still fail during
# inference smoke inside ggml-cuda.
COPY requirements-llm.txt .
RUN case "$LOCAL_LLM_WHEEL_VARIANT" in \
        cpu) WHEEL_INDEX="" ;; \
        cu123) WHEEL_INDEX="https://abetlen.github.io/llama-cpp-python/whl/cu123" ;; \
        *) echo "Unsupported LOCAL_LLM_WHEEL_VARIANT=$LOCAL_LLM_WHEEL_VARIANT" >&2; exit 1 ;; \
    esac \
    && if [ -n "$WHEEL_INDEX" ]; then \
        pip install --no-cache-dir "llama-cpp-python==${LOCAL_LLM_WHEEL_VERSION}" --extra-index-url "$WHEEL_INDEX"; \
       else \
        pip install --no-cache-dir "llama-cpp-python==${LOCAL_LLM_WHEEL_VERSION}"; \
       fi \
    && pip install --no-cache-dir -r requirements-llm.txt

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
