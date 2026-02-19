#!/usr/bin/env node

// Lancelot — A Governed Autonomous System
// Copyright (c) 2026 Myles Russell Hamilton
// Licensed under AGPL-3.0. See LICENSE for details.
// Patent Pending: US Provisional Application #63/982,183

import { readFileSync } from 'node:fs';
import { Command } from 'commander';
import { run } from '../src/index.mjs';

const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));

const program = new Command()
  .name('create-lancelot')
  .description('Install and configure Lancelot — your AI-powered autonomous agent')
  .version(pkg.version)
  .option('-d, --directory <path>', 'Installation directory', './lancelot')
  .option('--provider <name>', 'LLM provider (gemini|openai|anthropic)')
  .option('--skip-model', 'Skip local model download')
  .option('--resume', 'Resume a previously interrupted install')
  .parse(process.argv);

run(program.opts());
