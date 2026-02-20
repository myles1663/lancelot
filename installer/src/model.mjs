// ============================================================
// Model — local AI model download with progress and resume
// ============================================================

import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import https from 'node:https';
import http from 'node:http';
import crypto from 'node:crypto';

function parseLockfile(content) {
  const url = content.match(/url:\s*"([^"]+)"/)?.[1];
  const filename = content.match(/filename:\s*"([^"]+)"/)?.[1];
  const hash = content.match(/hash:\s*"([^"]+)"/)?.[1];
  const sizeMb = parseInt(content.match(/size_mb:\s*(\d+)/)?.[1] || '5000');
  return { url, filename, hash, sizeMb };
}

function isPlaceholderHash(hash) {
  return !hash || /^0+$/.test(hash);
}

async function hashFile(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256');
    const stream = fs.createReadStream(filePath);
    stream.on('data', chunk => hash.update(chunk));
    stream.on('end', () => resolve(hash.digest('hex')));
    stream.on('error', reject);
  });
}

function formatBytes(bytes) {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  return `${(bytes / 1e3).toFixed(0)} KB`;
}

function followRedirects(url, headers, maxRedirects = 5) {
  return new Promise((resolve, reject) => {
    if (maxRedirects <= 0) return reject(new Error('Too many redirects'));
    const client = url.startsWith('https') ? https : http;
    const req = client.get(url, { headers }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        resolve(followRedirects(res.headers.location, headers, maxRedirects - 1));
      } else {
        resolve(res);
      }
    });
    req.on('error', reject);
  });
}

export async function downloadModel(installDir, onProgress) {
  const lockfilePath = path.join(installDir, 'local_models', 'models.lock.yaml');
  const lockContent = await fsp.readFile(lockfilePath, 'utf8');
  const { url, filename, hash, sizeMb } = parseLockfile(lockContent);

  if (!url || !filename) {
    throw new Error('Could not parse model URL/filename from models.lock.yaml');
  }

  const weightsDir = path.join(installDir, 'local_models', 'weights');
  await fsp.mkdir(weightsDir, { recursive: true });

  const finalPath = path.join(weightsDir, filename);
  const tempPath = finalPath + '.download';

  // Check if already present and valid
  if (fs.existsSync(finalPath)) {
    const stat = fs.statSync(finalPath);
    // If file is reasonably sized, consider it valid
    if (stat.size > sizeMb * 1024 * 1024 * 0.8) {
      if (!isPlaceholderHash(hash)) {
        const actualHash = await hashFile(finalPath);
        if (actualHash === hash) {
          onProgress?.({ done: true, message: 'Model already downloaded and verified' });
          return;
        }
      } else {
        onProgress?.({ done: true, message: 'Model already downloaded' });
        return;
      }
    }
  }

  // Check for partial download (resume support)
  let startByte = 0;
  if (fs.existsSync(tempPath)) {
    const stat = fs.statSync(tempPath);
    startByte = stat.size;
  }

  const headers = {};
  if (startByte > 0) {
    headers['Range'] = `bytes=${startByte}-`;
  }

  const res = await followRedirects(url, headers);

  // If server doesn't support range, start fresh
  if (startByte > 0 && res.statusCode === 200) {
    startByte = 0;
    // Truncate the temp file
    await fsp.writeFile(tempPath, '');
  }

  if (res.statusCode !== 200 && res.statusCode !== 206) {
    throw new Error(`Model download failed: HTTP ${res.statusCode}`);
  }

  const totalBytes = res.statusCode === 206
    ? startByte + parseInt(res.headers['content-length'] || '0')
    : parseInt(res.headers['content-length'] || String(sizeMb * 1024 * 1024));

  let downloadedBytes = startByte;
  let lastReportTime = Date.now();
  let lastReportBytes = startByte;

  const fileStream = fs.createWriteStream(tempPath, { flags: startByte > 0 ? 'a' : 'w' });

  await new Promise((resolve, reject) => {
    res.on('data', (chunk) => {
      downloadedBytes += chunk.length;
      const now = Date.now();
      const elapsed = (now - lastReportTime) / 1000;

      if (elapsed >= 0.5) {
        const speed = (downloadedBytes - lastReportBytes) / elapsed;
        const remaining = totalBytes > 0 ? (totalBytes - downloadedBytes) / speed : 0;
        const percent = totalBytes > 0 ? Math.round((downloadedBytes / totalBytes) * 100) : 0;

        onProgress?.({
          done: false,
          percent,
          downloaded: formatBytes(downloadedBytes),
          total: formatBytes(totalBytes),
          speed: `${formatBytes(speed)}/s`,
          eta: remaining > 60 ? `${Math.round(remaining / 60)}m` : `${Math.round(remaining)}s`,
        });

        lastReportTime = now;
        lastReportBytes = downloadedBytes;
      }
    });

    res.pipe(fileStream);
    // Wait for the write stream to finish flushing to disk, not just the
    // response read end.  Listening on res 'end' caused a race on Windows
    // where the file handle wasn't released before the rename below.
    fileStream.on('finish', resolve);
    fileStream.on('error', reject);
    res.on('error', (err) => { fileStream.destroy(); reject(err); });
  });

  // Verify checksum
  if (!isPlaceholderHash(hash)) {
    onProgress?.({ done: false, message: 'Verifying checksum...' });
    const actualHash = await hashFile(tempPath);
    if (actualHash !== hash) {
      await fsp.unlink(tempPath);
      throw new Error('Model checksum mismatch — file corrupted during download');
    }
  }

  // Rename to final location
  await fsp.rename(tempPath, finalPath);
  onProgress?.({ done: true, message: 'Model downloaded successfully' });
}
