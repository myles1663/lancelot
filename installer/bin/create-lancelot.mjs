#!/usr/bin/env node

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
