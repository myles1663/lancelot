/**
 * Host Network Detection
 *
 * Detects the host gateway IP that a VM (Co-work, WSL2) can use
 * to reach UABServer on the host machine.
 *
 * Also generates API keys for authenticated access.
 */

import { networkInterfaces, platform } from 'os';
import { randomBytes } from 'crypto';
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { homedir } from 'os';
import { createLogger } from '../logger.js';

const log = createLogger('host-network');

export interface HostNetworkInfo {
  /** IP address that VMs can reach the host on */
  hostIp: string;
  /** How the IP was detected */
  method: 'wsl-adapter' | 'hyperv-adapter' | 'vm-adapter' | 'lan' | 'fallback';
  /** Name of the network adapter used */
  adapterName: string;
}

/**
 * Detect the host IP that a VM can use to reach the host.
 *
 * Priority:
 * 1. WSL / Hyper-V virtual ethernet adapter (172.x.x.x range)
 * 2. Any vEthernet / vmnet adapter
 * 3. LAN IP (Wi-Fi or Ethernet)
 * 4. Fallback to 127.0.0.1
 */
export function detectHostGatewayIp(): HostNetworkInfo {
  const interfaces = networkInterfaces();

  // Pass 1: Look for WSL / Hyper-V virtual adapter
  for (const [name, addrs] of Object.entries(interfaces)) {
    if (!addrs) continue;
    const lname = name.toLowerCase();
    if (lname.includes('wsl') || lname.includes('hyper-v')) {
      for (const addr of addrs) {
        if (addr.family === 'IPv4' && !addr.internal) {
          log.info(`Found WSL/Hyper-V adapter: ${name} → ${addr.address}`);
          return { hostIp: addr.address, method: 'wsl-adapter', adapterName: name };
        }
      }
    }
  }

  // Pass 2: Any virtual ethernet / vmnet adapter
  for (const [name, addrs] of Object.entries(interfaces)) {
    if (!addrs) continue;
    const lname = name.toLowerCase();
    if (lname.includes('vethernet') || lname.includes('vmnet') || lname.includes('vbox')) {
      for (const addr of addrs) {
        if (addr.family === 'IPv4' && !addr.internal) {
          log.info(`Found VM adapter: ${name} → ${addr.address}`);
          return { hostIp: addr.address, method: 'vm-adapter', adapterName: name };
        }
      }
    }
  }

  // Pass 3: LAN IP (Wi-Fi or Ethernet — non-virtual, non-loopback)
  for (const [name, addrs] of Object.entries(interfaces)) {
    if (!addrs) continue;
    const lname = name.toLowerCase();
    // Skip virtual and loopback
    if (lname.includes('vethernet') || lname.includes('vmnet') || lname.includes('vbox') ||
        lname.includes('docker') || lname.includes('loopback')) continue;
    for (const addr of addrs) {
      if (addr.family === 'IPv4' && !addr.internal) {
        log.info(`Using LAN adapter: ${name} → ${addr.address}`);
        return { hostIp: addr.address, method: 'lan', adapterName: name };
      }
    }
  }

  // Pass 4: Fallback
  log.warn('No suitable network adapter found, falling back to 127.0.0.1');
  return { hostIp: '127.0.0.1', method: 'fallback', adapterName: 'loopback' };
}

/**
 * Generate a random API key for UABServer authentication.
 */
export function generateApiKey(): string {
  return `uab_${randomBytes(24).toString('base64url')}`;
}

/**
 * Path to the persisted API key file.
 */
function getKeyFilePath(): string {
  const home = homedir();
  if (platform() === 'darwin') {
    return join(home, 'Library', 'Application Support', 'UAB Bridge', 'api-key');
  }
  const localAppData = process.env.LOCALAPPDATA || join(home, 'AppData', 'Local');
  return join(localAppData, 'UAB Bridge', 'api-key');
}

/**
 * Get or create a persisted API key.
 * The same key is used across installs so existing skill files stay valid.
 */
export function getOrCreateApiKey(): string {
  const keyFile = getKeyFilePath();

  if (existsSync(keyFile)) {
    const key = readFileSync(keyFile, 'utf-8').trim();
    if (key.length > 10) {
      log.info('Using existing API key');
      return key;
    }
  }

  const key = generateApiKey();
  mkdirSync(join(keyFile, '..'), { recursive: true });
  writeFileSync(keyFile, key, 'utf-8');
  log.info('Generated new API key');
  return key;
}
