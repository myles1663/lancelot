# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create a non-root user
RUN groupadd -r lancelot && useradd -r -g lancelot -m -d /home/lancelot -s /bin/bash lancelot

# Set the working directory
WORKDIR /home/lancelot/app

# Install system dependencies if needed (e.g., for chromadb)
# build-essential for compiling some python extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements.txt first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Change ownership of the application directory to the non-root user
RUN chown -R lancelot:lancelot /home/lancelot

# Switch to non-root user
USER lancelot

# Expose port (if needed for FastAPI later, though not explicitly asked, it's good practice)
EXPOSE 8000

# Default command can be bash or python
CMD ["bash"]
