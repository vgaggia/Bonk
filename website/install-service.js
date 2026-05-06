// Run from an admin CMD:  node install-service.js
// To uninstall later:     node install-service.js --uninstall

import { Service } from 'node-windows';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const svc = new Service({
  name: 'Bonk Conlang Website',
  description: 'Express server for the Bonk conlang dictionary (seamenese.com)',
  script: path.join(__dirname, 'server.js'),
  scriptOptions: '--notunnel',
  workingDirectory: __dirname,
  env: [
    { name: 'PORT', value: '3000' },
  ],
});

if (process.argv.includes('--uninstall')) {
  svc.on('uninstall', () => console.log('Service uninstalled.'));
  svc.uninstall();
} else {
  svc.on('install', () => {
    console.log('Service installed. Starting...');
    svc.start();
  });
  svc.on('alreadyinstalled', () => console.log('Service is already installed.'));
  svc.on('start', () => console.log('Service started.'));
  svc.on('error', (err) => console.error('Error:', err));
  svc.install();
}
