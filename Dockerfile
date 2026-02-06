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
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements.txt first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium + OS-level dependencies (fonts, libs)
# Install as root for system deps, then set shared browser path
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers
RUN playwright install --with-deps chromium

# Copy the rest of the application code
COPY . .

# Change ownership of the application directory to the non-root user
RUN chown -R lancelot:lancelot /home/lancelot

# Add lancelot to docker group so it can use the mounted socket
RUN groupadd docker 2>/dev/null; usermod -aG docker lancelot

# Switch to non-root user
USER lancelot

# Expose port (if needed for FastAPI later, though not explicitly asked, it's good practice)
EXPOSE 8000

# Default command can be bash or python
CMD ["bash"]
