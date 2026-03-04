/**
 * UAB Logger — Self-contained structured logger for the Universal App Bridge.
 *
 * This is a lightweight logger that works standalone without any
 * dependency on ClaudeClaw's logger infrastructure.
 * Writes to console only by default; file logging can be enabled
 * by setting UAB_LOG_FILE environment variable.
 */

import fs from 'fs';
import path from 'path';

type LogLevel = 'debug' | 'info' | 'warn' | 'error';

const LEVEL_PRIORITY: Record<LogLevel, number> = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
};

const minLevel: LogLevel = (process.env.UAB_LOG_LEVEL as LogLevel) || (process.env.LOG_LEVEL as LogLevel) || 'info';
const logFilePath = process.env.UAB_LOG_FILE || '';

let logStream: fs.WriteStream | null = null;

function getLogStream(): fs.WriteStream | null {
  if (!logFilePath) return null;
  if (!logStream) {
    const dir = path.dirname(logFilePath);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    logStream = fs.createWriteStream(logFilePath, { flags: 'a' });
  }
  return logStream;
}

function shouldLog(level: LogLevel): boolean {
  return LEVEL_PRIORITY[level] >= LEVEL_PRIORITY[minLevel];
}

function formatMessage(level: LogLevel, module: string, message: string, data?: Record<string, unknown>): string {
  const ts = new Date().toISOString();
  const base = `${ts} [${level.toUpperCase().padEnd(5)}] [uab:${module}] ${message}`;
  if (data && Object.keys(data).length > 0) {
    return `${base} ${JSON.stringify(data)}`;
  }
  return base;
}

export function createLogger(module: string) {
  function log(level: LogLevel, message: string, data?: Record<string, unknown>): void {
    if (!shouldLog(level)) return;

    const formatted = formatMessage(level, module, message, data);

    switch (level) {
      case 'error':
        console.error(formatted);
        break;
      case 'warn':
        console.warn(formatted);
        break;
      default:
        console.log(formatted);
    }

    try {
      const stream = getLogStream();
      stream?.write(formatted + '\n');
    } catch {
      // Don't crash on log write failure
    }
  }

  return {
    debug: (msg: string, data?: Record<string, unknown>) => log('debug', msg, data),
    info: (msg: string, data?: Record<string, unknown>) => log('info', msg, data),
    warn: (msg: string, data?: Record<string, unknown>) => log('warn', msg, data),
    error: (msg: string, data?: Record<string, unknown>) => log('error', msg, data),
  };
}

export function closeLogger(): void {
  logStream?.end();
  logStream = null;
}
