// ============================================================
// Prerequisites — cross-platform system checks
// ============================================================

import { execa } from 'execa';
import ora from 'ora';
import { showCheck, showError, showWarning, showInfo } from './ui.mjs';
import {
  getPlatform, getDockerInstallUrl, getDockerStartInstructions,
  getGitInstallHint, checkDiskSpace, getTotalRAM,
  isPortAvailable, checkNetworkConnectivity, canWriteToDir,
  getDockerSocketAccessible, getGpuVram,
} from './platform.mjs';
import { MIN_DISK_GB, MIN_RAM_GB } from './constants.mjs';

async function tryCommand(cmd, args) {
  try {
    const result = await execa(cmd, args, { timeout: 10000 });
    return { ok: true, stdout: result.stdout.trim() };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

function parseVersion(output) {
  const match = output.match(/(\d+\.\d+[\.\d]*)/);
  return match ? match[1] : null;
}

export async function runAllChecks(installDir) {
  console.log('');
  console.log('  Checking prerequisites...');

  const results = { hasGpu: false, gpuLayers: 0, gpuName: null };

  // Node.js
  const nodeVersion = process.version;
  const nodeMajor = parseInt(nodeVersion.slice(1));
  if (nodeMajor < 18) {
    showCheck('Node.js', nodeVersion, false);
    showError(
      `Node.js 18+ required (you have ${nodeVersion})`,
      'Download from https://nodejs.org/'
    );
    process.exit(1);
  }
  showCheck('Node.js', nodeVersion);

  // Git
  const git = await tryCommand('git', ['--version']);
  if (!git.ok) {
    showCheck('Git', 'not found', false);
    showError('Git is required', getGitInstallHint());
    process.exit(1);
  }
  showCheck('Git', parseVersion(git.stdout));

  // Docker CLI
  const docker = await tryCommand('docker', ['--version']);
  if (!docker.ok) {
    showCheck('Docker', 'not found', false);
    showError(
      'Docker is required',
      `Download: ${getDockerInstallUrl()}`
    );
    process.exit(1);
  }
  showCheck('Docker', parseVersion(docker.stdout));

  // Docker running
  const dockerInfo = await tryCommand('docker', ['info']);
  if (!dockerInfo.ok) {
    showCheck('Docker daemon', 'not running', false);
    showInfo(getDockerStartInstructions());

    // Poll until Docker is running
    const spinner = ora('  Waiting for Docker to start...').start();
    let attempts = 0;
    const maxAttempts = 60; // 5 minutes
    while (attempts < maxAttempts) {
      await new Promise(r => setTimeout(r, 5000));
      const retry = await tryCommand('docker', ['info']);
      if (retry.ok) {
        spinner.succeed('  Docker is running');
        break;
      }
      attempts++;
      if (attempts >= maxAttempts) {
        spinner.fail('  Docker did not start in time');
        showError('Docker is not running. Please start Docker Desktop and try again.');
        process.exit(1);
      }
    }
  } else {
    showCheck('Docker daemon', 'running');
  }

  // Docker Compose v2
  const compose = await tryCommand('docker', ['compose', 'version']);
  if (!compose.ok) {
    showCheck('Docker Compose v2', 'not found', false);
    showError(
      'Docker Compose v2 is required',
      'Update Docker Desktop to the latest version'
    );
    process.exit(1);
  }
  showCheck('Docker Compose', parseVersion(compose.stdout));

  // Disk space
  const disk = await checkDiskSpace(installDir || '.');
  if (disk.availableGB > 0) {
    if (disk.availableGB < 5) {
      showCheck('Disk space', `${disk.availableGB} GB available`, false);
      showError(`At least 5 GB required (${disk.availableGB} GB available)`);
      process.exit(1);
    } else if (disk.availableGB < MIN_DISK_GB) {
      showCheck('Disk space', `${disk.availableGB} GB available`);
      showWarning(`${MIN_DISK_GB} GB recommended. You have ${disk.availableGB} GB.`);
    } else {
      showCheck('Disk space', `${disk.availableGB} GB available`);
    }
  }

  // RAM
  const ramGB = getTotalRAM();
  if (ramGB < MIN_RAM_GB) {
    showCheck('RAM', `${ramGB} GB`);
    showWarning(`${MIN_RAM_GB} GB recommended. Performance may be degraded.`);
  } else {
    showCheck('RAM', `${ramGB} GB`);
  }

  // GPU (informational)
  const gpu = await tryCommand('nvidia-smi', ['--query-gpu=name', '--format=csv,noheader']);
  if (gpu.ok && gpu.stdout) {
    const gpuName = gpu.stdout.split('\n')[0].trim();
    results.hasGpu = true;
    results.gpuLayers = 15;
    results.gpuName = gpuName;
    showCheck('GPU', gpuName);

    // GPU VRAM check
    const vramMb = await getGpuVram();
    if (vramMb !== null) {
      const vramGb = (vramMb / 1024).toFixed(1);
      if (vramMb < 2048) {
        showWarning(`GPU VRAM is ${vramGb} GB — may be too low for model inference. Falling back to CPU.`);
        results.hasGpu = false;
        results.gpuLayers = 0;
      } else if (vramMb < 4096) {
        showCheck('GPU VRAM', `${vramGb} GB (minimum — may be slow)`);
      } else {
        showCheck('GPU VRAM', `${vramGb} GB`);
      }
    }
  } else {
    showInfo('No NVIDIA GPU detected — local model will use CPU (works fine, just slower)');
    results.hasGpu = false;
    results.gpuLayers = 0;
  }

  // Network connectivity
  const online = await checkNetworkConnectivity();
  if (!online) {
    showCheck('Internet', 'not reachable', false);
    showError(
      'Internet connection required to clone repository, download model, and validate API keys.',
      'Check your network connection and try again.'
    );
    process.exit(1);
  }
  showCheck('Internet', 'connected');

  // Port availability
  const port8000 = await isPortAvailable(8000);
  const port8080 = await isPortAvailable(8080);
  if (!port8000 || !port8080) {
    const blocked = [];
    if (!port8000) blocked.push('8000');
    if (!port8080) blocked.push('8080');
    showCheck('Ports', `${blocked.join(', ')} in use`, false);
    showWarning(
      `Port${blocked.length > 1 ? 's' : ''} ${blocked.join(' and ')} ${blocked.length > 1 ? 'are' : 'is'} already in use. ` +
      'Lancelot needs these ports. Stop the conflicting service or it will fail to start.'
    );
  } else {
    showCheck('Ports', '8000, 8080 available');
  }

  // Docker socket access (Linux-specific)
  const socketCheck = await getDockerSocketAccessible();
  if (!socketCheck.accessible) {
    showCheck('Docker socket', 'permission denied', false);
    showError(
      'Cannot access Docker socket. Lancelot needs socket access for sandboxed tool execution.',
      socketCheck.hint
    );
    process.exit(1);
  }

  // Write permissions on install directory
  const targetDir = installDir || '.';
  if (!canWriteToDir(targetDir)) {
    showCheck('Write access', targetDir, false);
    showError(
      `Cannot write to ${targetDir}`,
      'Choose a different directory or fix permissions.'
    );
    process.exit(1);
  }
  showCheck('Write access', 'OK');

  return results;
}
