#!/usr/bin/env node

// Lancelot — A Governed Autonomous System
// Copyright (c) 2026 Myles Russell Hamilton
// Licensed under AGPL-3.0. See LICENSE for details.
// Patent Pending: US Provisional Application #63/982,183

import { Command } from 'commander';
import { run } from '../src/index.mjs';

const program = new Command()
  .name('create-lancelot')
  .description('Install and configure Lancelot — your AI-powered autonomous agent')
  .version('1.0.0')
  .option('-d, --directory <path>', 'Installation directory', './lancelot')
  .option('--provider <name>', 'LLM provider (gemini|openai|anthropic)')
  .option('--skip-model', 'Skip local model download')
  .option('--resume', 'Resume a previously interrupted install')
  .parse(process.argv);

run(program.opts());
