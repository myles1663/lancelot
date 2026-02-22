# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

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

# Copy requirements.txt first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium + OS-level dependencies (fonts, libs)
# Install as root for system deps, then set shared browser path
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN playwright install --with-deps chromium

# Copy VERSION file first (separate layer for cache efficiency)
COPY VERSION /app/VERSION

# Copy the rest of the application code
COPY . .

# Copy seed / onboarding data into the data directory.
# When a named Docker volume is mounted here on first run, Docker
# auto-populates it with these files (CAPABILITIES.md, RULES.md, etc.)
RUN mkdir -p /home/lancelot/data && \
    cp -r lancelot_data/* /home/lancelot/data/ 2>/dev/null || true

# Build War Room React SPA
RUN cd src/warroom && npm ci && npm run build && rm -rf node_modules

# Change ownership of the application directory to the non-root user
RUN chown -R lancelot:lancelot /home/lancelot

# F-001: Docker group no longer needed — socket proxy used instead of direct mount

# Install gosu for dropping privileges in entrypoint
RUN apt-get update && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/*

# Copy and set entrypoint (runs as root, drops to lancelot user)
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["entrypoint.sh"]
CMD ["bash"]
