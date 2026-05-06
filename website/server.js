// Bonk conlang dictionary website.
// Serves the JSON the Python tracker writes; allows the UI to update sync
// settings and request a forced sync. The bot does the heavy lifting — this
// process is intentionally tiny.

import express from 'express';
import chokidar from 'chokidar';
import { spawn } from 'node:child_process';
import { promises as fs } from 'node:fs';
import fsSync from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const PORT = parseInt(process.env.PORT ?? '3000', 10);
const DB_PATH = path.resolve(
  process.env.CONLANG_DB_PATH ?? path.join(__dirname, '..', 'data', 'conlang_dictionary.json'),
);
const ADMIN_TOKEN = (process.env.WEBSITE_ADMIN_TOKEN ?? '').trim();

// In-process mutex so concurrent PATCH /api/meta calls serialize their
// read-modify-write. The bot uses os.replace + an asyncio lock on its side;
// this guards us against the rare web-side race.
let writeChain = Promise.resolve();
function withLock(fn) {
  const next = writeChain.then(fn, fn);
  // Don't let one failure poison the chain.
  writeChain = next.catch(() => {});
  return next;
}

async function readDb() {
  const text = await fs.readFile(DB_PATH, 'utf-8');
  return JSON.parse(text);
}

async function writeDbAtomic(data) {
  const tmp = DB_PATH + '.tmp';
  await fs.writeFile(tmp, JSON.stringify(data, null, 2), 'utf-8');
  await fs.rename(tmp, DB_PATH);
}

function requireAdmin(req, res, next) {
  if (!ADMIN_TOKEN) return next(); // auth disabled
  const provided = req.get('X-Admin-Token') ?? '';
  if (provided === ADMIN_TOKEN) return next();
  res.status(401).json({ error: 'admin token required' });
}

const app = express();
app.use(express.json({ limit: '64kb' }));
app.use(express.static(path.join(__dirname, 'public'), { index: 'index.html' }));

// ---- API ----------------------------------------------------------------

app.get('/api/dictionary', async (_req, res) => {
  try {
    const data = await readDb();
    res.set('Cache-Control', 'no-store');
    res.json(data);
  } catch (err) {
    if (err.code === 'ENOENT') {
      res.status(503).json({ error: 'dictionary not yet created — wait for first sync' });
    } else {
      console.error('GET /api/dictionary failed', err);
      res.status(500).json({ error: 'read failed' });
    }
  }
});

// PATCH meta — supports {sync_interval_hours, auto_sync_enabled, force_sync}.
// Re-reads disk under the lock to avoid clobbering changes the bot just wrote.
app.patch('/api/meta', requireAdmin, async (req, res) => {
  const body = req.body ?? {};
  const updates = {};

  if ('sync_interval_hours' in body) {
    const v = Number(body.sync_interval_hours);
    if (!Number.isFinite(v) || v < 1 || v > 168) {
      return res.status(400).json({ error: 'sync_interval_hours must be 1..168' });
    }
    updates.sync_interval_hours = v;
  }
  if ('auto_sync_enabled' in body) {
    if (typeof body.auto_sync_enabled !== 'boolean') {
      return res.status(400).json({ error: 'auto_sync_enabled must be boolean' });
    }
    updates.auto_sync_enabled = body.auto_sync_enabled;
  }
  const wantsForce = body.force_sync === true;

  if (Object.keys(updates).length === 0 && !wantsForce) {
    return res.status(400).json({ error: 'no recognized fields' });
  }

  try {
    const result = await withLock(async () => {
      const data = await readDb();
      data.meta = data.meta ?? {};
      Object.assign(data.meta, updates);
      if (wantsForce) {
        data.meta.force_sync_requested_at = new Date().toISOString();
      }
      await writeDbAtomic(data);
      return data.meta;
    });
    res.json({ ok: true, meta: result });
  } catch (err) {
    console.error('PATCH /api/meta failed', err);
    res.status(500).json({ error: 'write failed' });
  }
});

// ---- Server-Sent Events: notify clients when the file changes -----------

const sseClients = new Set();

app.get('/api/events', (req, res) => {
  res.set({
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-store',
    Connection: 'keep-alive',
  });
  res.flushHeaders?.();
  res.write(`event: hello\ndata: ${JSON.stringify({ ts: Date.now() })}\n\n`);

  sseClients.add(res);
  req.on('close', () => sseClients.delete(res));
});

function broadcast(event, data) {
  const payload = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const client of sseClients) {
    try {
      client.write(payload);
    } catch {
      sseClients.delete(client);
    }
  }
}

// Start the watcher only if the file exists; chokidar handles later creation.
const watcher = chokidar.watch(DB_PATH, {
  ignoreInitial: true,
  awaitWriteFinish: { stabilityThreshold: 200, pollInterval: 50 },
});
watcher.on('all', (evt) => {
  broadcast('dictionary-updated', { event: evt, ts: Date.now() });
});

// ---- Tunnel helper ------------------------------------------------------

function startTunnel() {
  const args = ['tunnel', '--url', `http://localhost:${PORT}`];

  const tunnelName = (process.env.CLOUDFLARE_TUNNEL_NAME ?? 'bonk-conlang').trim();
  if (tunnelName) {
    args.length = 0;
    args.push('tunnel', 'run', tunnelName);
  }

  const proc = spawn('cloudflared', args, { stdio: ['ignore', 'pipe', 'pipe'] });

  proc.stderr.on('data', (buf) => {
    const line = buf.toString().trim();
    // Quick tunnels print the URL to stderr.
    const match = line.match(/https:\/\/[^\s]+\.trycloudflare\.com/);
    if (match) console.log(`  tunnel: ${match[0]}`);
    if (tunnelName && line.includes('Registered tunnel connection')) {
      console.log(`  tunnel: named tunnel "${tunnelName}" connected`);
    }
  });

  proc.on('error', (err) => {
    if (err.code === 'ENOENT') {
      console.error('  tunnel: cloudflared not found — install it or run with --notunnel');
      console.error('         https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/');
    } else {
      console.error(`  tunnel: ${err.message}`);
    }
  });

  proc.on('exit', (code) => {
    if (code) console.error(`  tunnel: cloudflared exited with code ${code}`);
  });

  return proc;
}

// ---- Startup ------------------------------------------------------------

const noTunnel = process.argv.includes('--notunnel');

app.listen(PORT, () => {
  const exists = fsSync.existsSync(DB_PATH);
  console.log(`bonk-conlang-web listening on http://localhost:${PORT}`);
  console.log(`  db: ${DB_PATH} ${exists ? '' : '(not yet created — bot will write on first sync)'}`);
  console.log(`  admin auth: ${ADMIN_TOKEN ? 'enabled (X-Admin-Token required for PATCH)' : 'disabled'}`);

  if (!noTunnel) {
    startTunnel();
  } else {
    console.log('  tunnel: disabled (--notunnel)');
  }
});
