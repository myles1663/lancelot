#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# Lancelot Launcher — starts Docker and opens the War Room
# ──────────────────────────────────────────────────────────
set -e

WAR_ROOM_URL="http://localhost:8501"
HEALTH_URL="http://localhost:8000/health/live"
MAX_WAIT=120  # seconds
ISSUES_URL="https://github.com/myles1663/lancelot/issues"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
GRAY='\033[0;90m'
NC='\033[0m'  # No color

fatal_error() {
    local message="$1"
    local fix="$2"
    echo ""
    echo -e "  ${RED}ERROR: ${message}${NC}"
    if [ -n "$fix" ]; then
        echo -e "  ${YELLOW}Fix:   ${fix}${NC}"
    fi
    echo ""
    echo -e "  ${GRAY}If this doesn't resolve the issue, open a ticket:${NC}"
    echo -e "  ${CYAN}${ISSUES_URL}${NC}"
    echo ""
    exit 1
}

# ── Pre-flight checks ──────────────────────────────────────

echo ""
echo -e "  ${CYAN}Lancelot — Pre-flight checks${NC}"
echo ""

# 1. Docker CLI
if ! command -v docker &> /dev/null; then
    fatal_error "Docker is not installed." "Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
fi
echo -e "  ${GREEN}[OK]${NC} Docker CLI found"

# 2. Docker daemon running
if ! docker info &> /dev/null; then
    fatal_error "Docker is not running." "Start Docker Desktop and try again."
fi
echo -e "  ${GREEN}[OK]${NC} Docker daemon running"

# 3. curl available (needed for health check)
if ! command -v curl &> /dev/null; then
    fatal_error "curl is not installed." "Install curl: sudo apt install curl (Linux) or brew install curl (macOS)"
fi
echo -e "  ${GREEN}[OK]${NC} curl available"

# 4. Port 8000 available
check_port() {
    local port=$1
    # Try ss first (Linux), then lsof (macOS/Linux), then /dev/tcp (bash built-in)
    if command -v ss &> /dev/null; then
        ss -tln 2>/dev/null | grep -q ":${port} " && return 1
    elif command -v lsof &> /dev/null; then
        lsof -i :"${port}" -sTCP:LISTEN &> /dev/null && return 1
    elif (echo >/dev/tcp/localhost/"${port}") &>/dev/null; then
        return 1
    fi
    return 0
}

if ! check_port 8000; then
    fatal_error "Port 8000 is already in use." "Stop the service using port 8000 and try again."
fi
echo -e "  ${GREEN}[OK]${NC} Port 8000 available"

# 5. Port 8080 available
if ! check_port 8080; then
    fatal_error "Port 8080 is already in use." "Stop the service using port 8080 and try again."
fi
echo -e "  ${GREEN}[OK]${NC} Port 8080 available"

echo ""

# ── Start containers ────────────────────────────────────────

echo -e "  ${CYAN}Starting Lancelot...${NC}"
echo ""

if ! docker compose up -d "$@"; then
    fatal_error "docker compose up failed." "Check output above. Run 'docker compose logs' for details."
fi

echo ""
echo -e "  ${GRAY}Waiting for Lancelot to become healthy...${NC}"

elapsed=0
while [ $elapsed -lt $MAX_WAIT ]; do
    if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
        echo ""
        echo -e "  ${GREEN}Lancelot is ready!${NC}"
        echo ""
        echo "  War Room: $WAR_ROOM_URL"
        echo "  API:      http://localhost:8000"
        echo ""

        # Open War Room in default browser
        case "$(uname -s)" in
            MINGW*|MSYS*|CYGWIN*) start "$WAR_ROOM_URL" ;;
            Darwin*)               open "$WAR_ROOM_URL" ;;
            *)                     xdg-open "$WAR_ROOM_URL" 2>/dev/null || true ;;
        esac
        exit 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
    printf "\r  Waiting... %ds / %ds" "$elapsed" "$MAX_WAIT"
done

echo ""
echo ""
echo -e "  ${YELLOW}WARNING: Health check timed out after ${MAX_WAIT}s.${NC}"
echo -e "  ${YELLOW}Lancelot may still be starting. Check: docker compose logs -f lancelot-core${NC}"
echo "  War Room: $WAR_ROOM_URL"
echo ""
echo -e "  ${GRAY}If this doesn't resolve the issue, open a ticket:${NC}"
echo -e "  ${CYAN}${ISSUES_URL}${NC}"
echo ""
