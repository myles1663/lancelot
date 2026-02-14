// ============================================================
// UI — terminal display helpers
// ============================================================

import chalk from 'chalk';

export function showBanner() {
  console.log('');
  console.log(chalk.cyan('  ╔══════════════════════════════════════════╗'));
  console.log(chalk.cyan('  ║') + chalk.white.bold('        LANCELOT INSTALLER               ') + chalk.cyan('║'));
  console.log(chalk.cyan('  ║') + chalk.gray('   Autonomous AI System Setup             ') + chalk.cyan('║'));
  console.log(chalk.cyan('  ╚══════════════════════════════════════════╝'));
  console.log('');
}

export function showStep(number, total, description) {
  console.log('');
  console.log(chalk.cyan(`  [${number}/${total}]`) + chalk.white(` ${description}`));
}

export function showSuccess(config) {
  console.log('');
  console.log(chalk.green.bold('  ╔══════════════════════════════════════════╗'));
  console.log(chalk.green.bold('  ║') + chalk.white.bold('        LANCELOT IS READY!                ') + chalk.green.bold('║'));
  console.log(chalk.green.bold('  ╠══════════════════════════════════════════╣'));
  console.log(chalk.green.bold('  ║') + chalk.white(`  War Room: ${chalk.underline('http://localhost:8000/war-room')}`) + chalk.green.bold(' ║'));
  console.log(chalk.green.bold('  ║') + chalk.white(`  API:      ${chalk.underline('http://localhost:8000')}        `) + chalk.green.bold(' ║'));
  console.log(chalk.green.bold('  ║') + chalk.white('                                          ') + chalk.green.bold('║'));
  console.log(chalk.green.bold('  ║') + chalk.gray(`  Provider: ${config.providerName || 'Unknown'}`) + ' '.repeat(Math.max(0, 29 - (config.providerName || 'Unknown').length)) + chalk.green.bold('║'));
  console.log(chalk.green.bold('  ║') + chalk.gray(`  Comms:    ${config.commsName || 'Not configured'}`) + ' '.repeat(Math.max(0, 29 - (config.commsName || 'Not configured').length)) + chalk.green.bold('║'));
  console.log(chalk.green.bold('  ╚══════════════════════════════════════════╝'));
  console.log('');
  console.log(chalk.gray('  Quick commands:'));
  console.log(chalk.gray(`    cd ${config.directory || 'lancelot'}`));
  console.log(chalk.gray('    docker compose up -d      # Start'));
  console.log(chalk.gray('    docker compose down        # Stop'));
  console.log(chalk.gray('    docker compose logs -f     # View logs'));
  console.log('');
}

export function showError(message, hint) {
  console.log('');
  console.log(chalk.red.bold('  Error: ') + chalk.red(message));
  if (hint) {
    console.log(chalk.yellow('  Hint: ') + chalk.yellow(hint));
  }
  console.log('');
}

export function showWarning(message) {
  console.log(chalk.yellow('  Warning: ') + chalk.yellow(message));
}

export function showCheck(label, value, ok = true) {
  const icon = ok ? chalk.green('  ✓') : chalk.red('  ✗');
  console.log(`${icon} ${label}${value ? chalk.gray(` ${value}`) : ''}`);
}

export function showInfo(message) {
  console.log(chalk.gray(`  ${message}`));
}
