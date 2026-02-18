#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────
# Lancelot Launcher — starts Docker and opens the War Room
# ──────────────────────────────────────────────────────────
set -e

WAR_ROOM_URL="http://localhost:8501"
HEALTH_URL="http://localhost:8000/health/live"
MAX_WAIT=120  # seconds

echo ""
echo "  Starting Lancelot..."
echo ""

# Start containers in background
docker compose up -d "$@"

echo ""
echo "  Waiting for Lancelot to become healthy..."

elapsed=0
while [ $elapsed -lt $MAX_WAIT ]; do
    if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
        echo "  Lancelot is ready!"
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
echo "  Warning: Health check timed out after ${MAX_WAIT}s."
echo "  Lancelot may still be starting. Check: docker compose logs -f lancelot-core"
echo "  War Room: $WAR_ROOM_URL"
echo ""
