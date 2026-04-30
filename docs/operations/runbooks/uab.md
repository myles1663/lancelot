# UAB Runbook

Operational procedures for the Universal Application Bridge.

**Feature flag:** `FEATURE_TOOLS_UAB` (default: `false`)

---

## Checking Daemon Status

```bash
# From the host machine (direct)
curl http://localhost:7900 \
  -d '{"jsonrpc":"2.0","method":"getStatus","params":{},"id":1}'

# From the Lancelot API
curl http://localhost:8000/api/flags/uab-status

# From the general health endpoint
curl http://localhost:8000/health
```

Expected response when healthy:
```json
{
  "reachable": true,
  "version": "1.3.0",
  "connected_apps": 2,
  "supported_frameworks": ["electron","qt5","qt6","gtk3","gtk4","wpf","flutter","java-swing","javafx","office"],
  "uptime_seconds": 3600,
  "transport": "json-rpc-compat",
  "standalone_features": ["scan","apps","find","focused","findByPath","watchChanges","atomicChain","smartInvoke"]
}
```

## Listing Connected Apps

```bash
curl http://localhost:8000/api/flags/uab-apps
```

Returns connected applications with PID, name, framework, and connection method.

The live status payload now also includes `transport` and `standalone_features`. `transport: "json-rpc-compat"` means the host daemon is fronting the standalone UAB core while preserving the legacy JSON-RPC surface that Lancelot already governs.

When `FEATURE_TOOLS_UAB=true`, the general `/health` response also includes a
`components.uab_bridge` entry. `ok` means the daemon was reachable during the
Tool Fabric health probe. `offline` means UAB is enabled but the host daemon
could not be reached; core `/health/ready` can still be ready because desktop
control is an optional provider, but operators should not start desktop-control
workflows until the daemon is healthy.

## Starting the Daemon

**Windows (auto-start - recommended):**
```batch
scripts\install-uab.bat
```
Installs as a Windows Scheduled Task (`LancelotUABDaemon`) that auto-starts on login, starts the daemon immediately, and verifies the health check. The task launches `scripts\run-uab-daemon.bat`, which keeps the startup command stable even when the repo path contains spaces. Idempotent - safe to re-run.

**Windows (foreground - for debugging):**
```batch
scripts\start-uab.bat
```

**Linux/macOS:**
```bash
cd packages/uab
node dist/daemon.js --port 7900
```

**With install (first time - Linux/macOS):**
```bash
./scripts/install-uab.sh --start
```

## Stopping the Daemon

**If installed via `install-uab.bat` (background):**
```batch
scripts\uninstall-uab.bat
```
This stops the daemon and removes the Scheduled Task.

**If running in foreground (`start-uab.bat`):** Stop with `Ctrl+C`.

**Manual kill:**
```bash
# Find the process
ps aux | grep "daemon.js"
# Or on Windows
tasklist | findstr node
```

## Verifying the Scheduled Task

```batch
schtasks /Query /TN "LancelotUABDaemon"
```

---

## Troubleshooting

### Daemon unreachable from container

**Symptom:** `/api/flags/uab-status` returns `reachable: false`

**Check:**
1. Daemon running on host? `curl http://localhost:7900 -d '{"jsonrpc":"2.0","method":"getStatus","params":{},"id":1}'`
2. `host.docker.internal` resolving? `docker exec lancelot_core ping host.docker.internal`
3. Port 7900 not blocked by firewall?
4. `UAB_DAEMON_URL` env var correct in `.env`?

### Framework detection failures

**Symptom:** App not detected or detected as `unknown`

**Check:**
1. Run `detect` manually: `curl -d '{"jsonrpc":"2.0","method":"detect","params":{},"id":1}' http://localhost:7900`
2. Is the app running? Check PID exists.
3. For Electron apps: was it launched with `--remote-debugging-port`?
4. For Java apps: is Java Accessibility Bridge enabled?
5. For Office: is the COM server registered?

### Connection drops

**Symptom:** Previously connected app shows as disconnected

**Check:**
1. App still running? PID may have changed (app restarted).
2. Health summary: `curl -d '{"jsonrpc":"2.0","method":"health","params":{},"id":1}' http://localhost:7900`
3. Check failure count - auto-reconnect uses exponential backoff (1s -> 2s -> 4s -> 8s)
4. Stale connections are cleaned up after 5 minutes of continuous failure.

### Windows install fails

**Symptom:** `scripts\install-uab.bat` fails before creating the Scheduled Task

**Check:**
1. Confirm Node.js 18+: `node -p "process.versions.node"`
2. Confirm the build exists: `dir packages\uab\dist\daemon.js`
3. Run the launcher directly: `scripts\run-uab-daemon.bat`
4. Verify the task: `schtasks /Query /TN "LancelotUABDaemon"`
