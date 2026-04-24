// ============================================================
// Docker — clone, build, compose up, health check
// ============================================================

import { execa } from 'execa';
import ora from 'ora';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import {
  CORE_IMAGE,
  CORE_SERVICE,
  REPO_URL,
  HEALTH_CHECK_URL,
  HEALTH_CHECK_INTERVAL_MS,
  HEALTH_CHECK_MAX_ATTEMPTS,
  HOST_AGENT_HEALTH_URL,
  LOCAL_IMAGE_SERVICES,
} from './constants.mjs';

export async function cloneRepo(targetDir) {
  // If directory exists with a .git folder, do a pull instead
  if (fs.existsSync(`${targetDir}/.git`)) {
    const spinner = ora('  Updating existing repository...').start();
    try {
      await execa('git', ['pull', '--ff-only'], { cwd: targetDir, timeout: 60000 });
      spinner.succeed('  Repository updated');
    } catch (e) {
      spinner.warn('  Could not update repository — using existing files');
    }
    return;
  }

  const spinner = ora('  Cloning repository...').start();
  try {
    await execa('git', ['clone', '--depth', '1', REPO_URL, targetDir], { timeout: 120000 });
    spinner.succeed('  Repository cloned');
  } catch (e) {
    spinner.fail('  Failed to clone repository');
    throw new Error(`Git clone failed: ${e.message}`);
  }
}

export async function dockerBuild(projectDir) {
  const pullTargets = [CORE_SERVICE, ...LOCAL_IMAGE_SERVICES];
  const imageLabels = {
    [CORE_SERVICE]: CORE_IMAGE,
    'local-llm': 'configured local model image',
  };
  const buildTargets = [];
  const pullSpinner = ora('  Pulling prebuilt Docker images...').start();

  for (const service of pullTargets) {
    try {
      pullSpinner.text = `  Pulling ${service} (${imageLabels[service] || 'configured image'})...`;
      await execa('docker', ['compose', 'pull', service], {
        cwd: projectDir,
        timeout: 300000,
      });
    } catch {
      buildTargets.push(service);
    }
  }

  if (buildTargets.length === 0) {
    pullSpinner.succeed('  Prebuilt Docker images ready');
    return;
  }

  pullSpinner.warn(
    `  Could not pull ${buildTargets.join(', ')} - falling back to a local build for those service(s)`
  );

  const buildLabel = buildTargets.includes(CORE_SERVICE)
    ? '  Building required Docker images (this may take a few minutes)...'
    : '  Building the local model image fallback (this may take a few minutes)...';
  const spinner = ora(buildLabel).start();

  try {
    const proc = execa('docker', ['compose', 'build', ...buildTargets], {
      cwd: projectDir,
      timeout: 600000, // 10 min
    });

    // Show build progress in spinner text
    proc.stdout?.on('data', (data) => {
      const line = data.toString().trim().split('\n').pop();
      if (line && line.length < 80) {
        spinner.text = `  Building Docker images... ${line}`;
      }
    });

    await proc;
    if (buildTargets.includes(CORE_SERVICE)) {
      spinner.succeed('  Required Docker images built');
    } else {
      spinner.succeed('  Local model image built');
    }
  } catch (e) {
    spinner.fail('  Docker build failed');
    // Show stderr for debugging
    if (e.stderr) {
      console.log('\n  Build output:');
      console.log(e.stderr.split('\n').slice(-20).map(l => `    ${l}`).join('\n'));
    }
    throw new Error(
      `Docker build failed for ${buildTargets.join(', ')}. Try: docker compose build --no-cache ${buildTargets.join(' ')}`
    );
  }
}

export async function dockerUp(projectDir) {
  const spinner = ora('  Starting services...').start();
  try {
    await execa('docker', ['compose', 'up', '-d'], {
      cwd: projectDir,
      timeout: 120000,
    });
    spinner.succeed('  Services started');
  } catch (e) {
    spinner.fail('  Failed to start services');

    // Check for port conflicts
    if (e.stderr?.includes('port is already allocated') || e.stderr?.includes('address already in use')) {
      throw new Error('Port 8000 or 8080 is already in use. Stop the conflicting service and try again.');
    }
    throw new Error(`Docker compose up failed: ${e.message}`);
  }
}

export async function waitForHealthy() {
  const spinner = ora('  Waiting for services to be healthy...').start();

  for (let i = 0; i < HEALTH_CHECK_MAX_ATTEMPTS; i++) {
    try {
      const res = await fetch(HEALTH_CHECK_URL, { signal: AbortSignal.timeout(3000) });
      if (res.ok) {
        const body = await res.json();
        if (body.status === 'online' || body.status === 'ok') {
          spinner.succeed('  Services are healthy');
          return body;
        }
      }
    } catch {
      // Connection refused — service not up yet
    }

    spinner.text = `  Waiting for services to be healthy... (${i + 1}/${HEALTH_CHECK_MAX_ATTEMPTS})`;
    await new Promise(r => setTimeout(r, HEALTH_CHECK_INTERVAL_MS));
  }

  spinner.fail('  Services did not become healthy in time');
  throw new Error(
    `Health check failed after ${Math.round(HEALTH_CHECK_MAX_ATTEMPTS * HEALTH_CHECK_INTERVAL_MS / 1000)}s. ` +
    'Check logs with: docker compose logs -f'
  );
}

export async function dockerDown(projectDir) {
  try {
    await execa('docker', ['compose', 'down'], { cwd: projectDir, timeout: 30000 });
  } catch {
    // Best effort cleanup
  }
}

export async function startHostAgent(projectDir) {
  const spinner = ora('  Starting Host Agent...').start();

  const agentScript = path.join(projectDir, 'host_agent', 'agent.py');
  if (!fs.existsSync(agentScript)) {
    spinner.warn('  Host Agent script not found — skipping');
    return;
  }

  // Read the explicit token from .env. The legacy default is rejected.
  let token = '';
  try {
    const envPath = path.join(projectDir, '.env');
    if (fs.existsSync(envPath)) {
      const envContent = fs.readFileSync(envPath, 'utf-8');
      const match = envContent.match(/^HOST_AGENT_TOKEN=(.+)$/m);
      if (match) token = match[1].trim();
    }
  } catch {
    // Fall through to validation below
  }

  if (!token || token === 'lancelot-host-agent') {
    spinner.warn('  Host Agent token missing or still using the legacy default — skipping auto-start');
    return;
  }

  try {
    const isWindows = os.platform() === 'win32';

    if (isWindows) {
      // Use pythonw (no console window) for background execution on Windows
      let pythonCmd = 'pythonw';
      try {
        await execa('where', ['pythonw'], { timeout: 5000 });
      } catch {
        // Fall back to python if pythonw not found
        pythonCmd = 'python';
      }

      // Spawn detached — survives installer exit
      const child = spawn(pythonCmd, [agentScript, '--token', token], {
        detached: true,
        stdio: 'ignore',
        cwd: projectDir,
        windowsHide: true,
      });
      child.unref();
    } else {
      // Unix: spawn detached with nohup-like behavior
      const child = spawn('python3', [agentScript, '--token', token], {
        detached: true,
        stdio: 'ignore',
        cwd: projectDir,
      });
      child.unref();
    }

    // Wait briefly and verify it's reachable
    await new Promise(r => setTimeout(r, 2000));

    try {
      const res = await fetch(HOST_AGENT_HEALTH_URL, {
        signal: AbortSignal.timeout(3000),
      });
      if (res.ok) {
        spinner.succeed('  Host Agent started (port 9111)');
        return;
      }
    } catch {
      // Not reachable yet — try once more
    }

    // Second attempt after another second
    await new Promise(r => setTimeout(r, 1500));
    try {
      const res = await fetch(HOST_AGENT_HEALTH_URL, {
        signal: AbortSignal.timeout(3000),
      });
      if (res.ok) {
        spinner.succeed('  Host Agent started (port 9111)');
        return;
      }
    } catch {
      // Still not reachable
    }

    spinner.warn('  Host Agent started but not yet reachable — it may need a moment');
  } catch (e) {
    spinner.warn(`  Could not start Host Agent: ${e.message}`);
    console.log('');
    console.log('    You can start it manually later:');
    if (os.platform() === 'win32') {
      console.log('      host_agent\\start_agent.bat');
    } else {
      console.log('      python3 host_agent/agent.py &');
    }
  }
}
