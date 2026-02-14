// ============================================================
// Docker — clone, build, compose up, health check
// ============================================================

import { execa } from 'execa';
import ora from 'ora';
import fs from 'node:fs';
import { REPO_URL, HEALTH_CHECK_URL, HEALTH_CHECK_INTERVAL_MS, HEALTH_CHECK_MAX_ATTEMPTS } from './constants.mjs';

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
  const spinner = ora('  Building Docker images (this may take a few minutes)...').start();
  try {
    const proc = execa('docker', ['compose', 'build'], {
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
    spinner.succeed('  Docker images built');
  } catch (e) {
    spinner.fail('  Docker build failed');
    // Show stderr for debugging
    if (e.stderr) {
      console.log('\n  Build output:');
      console.log(e.stderr.split('\n').slice(-20).map(l => `    ${l}`).join('\n'));
    }
    throw new Error('Docker build failed. Try: docker compose build --no-cache');
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
