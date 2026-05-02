# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ARG UV_VERSION=0.8.22

# Create a non-root user
RUN groupadd -r lancelot && useradd -r -g lancelot -m -d /home/lancelot -s /bin/bash lancelot

# Set the working directory
WORKDIR /home/lancelot/app

# Install system dependencies
# build-essential for compiling some python extensions
# docker-cli so the sandbox provider can spawn sibling containers
# Node.js 20 for building the War Room React SPA
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    gnupg \
    ca-certificates \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @openai/codex@0.118.0

# Install a pinned uv release from PyPI. Avoid COPY --from=ghcr.io/astral-sh/uv:latest:
# that path is both floating and can fail on hosts with broken Docker credential helpers.
RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}"

# Copy dependency files first to leverage Docker cache
COPY pyproject.toml uv.lock ./

# Install Python dependencies via uv (frozen = use lockfile exactly)
RUN uv sync --frozen --no-dev --no-editable

# Add venv to PATH so uvicorn, playwright, etc. are available system-wide
ENV PATH="/home/lancelot/app/.venv/bin:$PATH"

# Install Playwright Chromium + OS-level dependencies (fonts, libs)
# Install as root for system deps, then set shared browser path
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN playwright install --with-deps chromium

# Copy VERSION file first (separate layer for cache efficiency)
COPY VERSION /app/VERSION

# Copy the rest of the application code
COPY . .

# Create an empty runtime data directory. Clean bootstrap templates are kept in
# config/bootstrap and seeded by the application when a new data volume starts.
# Never bake operator runtime state, receipts, memory, topology, or onboarding
# data into the image.
RUN mkdir -p /home/lancelot/data

# Build War Room React SPA
RUN cd src/warroom && npm ci && npm run build && rm -rf node_modules

# Runtime-writable paths are owned by the non-root user. The application,
# virtualenv, and browser binaries remain root-owned/readable, which avoids
# an expensive recursive chown over the full image during every rebuild.
RUN mkdir -p /home/lancelot/data /home/lancelot/workspace /home/lancelot/.codex && \
    chown -R lancelot:lancelot /home/lancelot/data /home/lancelot/workspace /home/lancelot/.codex

# F-001: Docker group no longer needed — socket proxy used instead of direct mount

# Install gosu for dropping privileges in entrypoint
RUN apt-get update && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/*

# Copy and set entrypoint (runs as root, drops to lancelot user)
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["entrypoint.sh"]
CMD ["bash"]
