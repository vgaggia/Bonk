# Bonk Conlang Dictionary — Website

Tiny Express + Alpine.js site that serves the JSON file the Bonk conlang tracker writes. Runs on Node 18+, no build step.

## What it does

- Reads `data/conlang_dictionary.json` (relative to the bot repo root by default — override with `CONLANG_DB_PATH`).
- Renders entries, conjugations, grammar rules, and example sentences with search + category/confidence filters.
- Lets you toggle auto-sync on/off, change the sync interval, and request an immediate sync. The bot picks these up within ~60s.
- Pushes a server-sent event to the browser whenever the JSON file changes (so the page re-renders without needing a manual refresh).

The bot is the only writer of vocabulary data. The website's only writes are to `meta.sync_interval_hours`, `meta.auto_sync_enabled`, and `meta.force_sync_requested_at`.

## Run locally

```bash
cd website
npm install
npm start            # http://localhost:3000
# or
npm run dev          # node --watch
```

Environment variables:

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `3000` | HTTP port |
| `CONLANG_DB_PATH` | `../data/conlang_dictionary.json` | JSON file produced by the bot |
| `WEBSITE_ADMIN_TOKEN` | _(unset)_ | If set, all `PATCH /api/meta` requests must send `X-Admin-Token: <value>`. The browser prompts for it on first 401 and stashes it in `localStorage`. |

## Sharing the URL safely (no port forwarding)

The bot runs on your machine, so the dictionary file is local. Cloudflare Tunnel is the cleanest way to expose `localhost:3000` to one specific person without opening a port on your router.

### Quick tunnel (60-second setup, throwaway URL)

```bash
# one-time install on Windows
winget install --id Cloudflare.cloudflared

# every session
npm run tunnel
# or:  cloudflared tunnel --url http://localhost:3000
```

Cloudflare prints a `https://<random>.trycloudflare.com` URL. Share that. URL changes every restart, no auth — the security model is "URL is unguessable enough for one private demo".

### Stable URL on a custom domain

Requires the domain to be on Cloudflare DNS (free tier is fine).

```bash
cloudflared tunnel login
cloudflared tunnel create bonk-conlang
cloudflared tunnel route dns bonk-conlang conlang.example.com
cloudflared tunnel run bonk-conlang
```

Once that's running, `https://conlang.example.com` always reaches `localhost:3000` over Cloudflare's network.

### Adding email-gated auth (Cloudflare Access, free for ≤50 users)

In the Cloudflare Zero Trust dashboard:

1. Create an Access Application → self-hosted → `conlang.example.com`.
2. Policy → Allow → include emails: yourself + Jorn.
3. Save. Cloudflare now requires email-OTP login before forwarding any request to your tunnel.

This pairs naturally with `WEBSITE_ADMIN_TOKEN`: Access guards reads, the admin token guards writes.

### Run the tunnel as a Windows service

```bash
cloudflared service install <token-from-dashboard>
```

So you don't need to keep a terminal open.

## API

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /api/dictionary` | none | Returns the full JSON. `503` until the bot writes the file the first time. |
| `PATCH /api/meta` | admin token if set | Body accepts `{sync_interval_hours?: 1..168, auto_sync_enabled?: bool, force_sync?: true}`. Validates, re-reads the file under a lock, applies, atomic-renames. |
| `GET /api/events` | none | Server-Sent Events. Emits `dictionary-updated` when the file changes. |

## Notes

- The bot's write cycle takes seconds to a minute (Anthropic call). Both bot and server use atomic temp+rename writes plus a re-read-merge step on each side, so a UI toggle that lands mid-cycle never gets clobbered.
- `data/conlang_dictionary.json` is gitignored. If you want backups, copy or commit it manually.
- This site is read-mostly. Don't add admin editing here — the right place to fix bad extractions is the analyzer prompt in `src/conlang/analyzer.py`.
