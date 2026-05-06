// Alpine.js component driving the dictionary UI.
// All data flow: GET /api/dictionary on load + on SSE notifications;
// PATCH /api/meta when toggles/buttons change.

document.addEventListener('alpine:init', () => {
  Alpine.data('dictionary', () => ({
    raw: null,
    error: '',
    forcing: false,

    search: '',
    categories: [],
    confidences: [],

    autoSync: true,
    syncIntervalHours: 6,

    adminTokenKey: 'bonk-conlang-admin-token',

    async init() {
      await this.refresh();
      this.subscribeEvents();
      // Re-render relative-time labels every 30s.
      setInterval(() => {
        this.$el.dispatchEvent(new Event('refresh-rel'));
      }, 30000);
    },

    async refresh() {
      try {
        const res = await fetch('/api/dictionary', { cache: 'no-store' });
        if (!res.ok) {
          this.error = res.status === 503 ? 'Dictionary not yet generated.' : `Fetch failed: ${res.status}`;
          return;
        }
        this.raw = await res.json();
        this.autoSync = this.raw.meta?.auto_sync_enabled ?? true;
        this.syncIntervalHours = this.raw.meta?.sync_interval_hours ?? 6;
        this.error = '';
      } catch (err) {
        this.error = `Network error: ${err.message}`;
      }
    },

    subscribeEvents() {
      try {
        const es = new EventSource('/api/events');
        es.addEventListener('dictionary-updated', () => this.refresh());
        es.onerror = () => {
          // Browser auto-retries; nothing to do.
        };
      } catch {
        // SSE unsupported — fall back to polling every 60s.
        setInterval(() => this.refresh(), 60000);
      }
    },

    // ---- Computed ----------------------------------------------------

    get entries() {
      return this.raw?.entries ?? [];
    },
    get grammar() {
      return [...(this.raw?.grammar_rules ?? [])].sort(
        (a, b) => this.confidenceRank(b.confidence) - this.confidenceRank(a.confidence),
      );
    },
    get examples() {
      return this.raw?.example_sentences ?? [];
    },
    get conjugations() {
      return this.raw?.conjugations ?? {};
    },
    get conjugationList() {
      return Object.values(this.conjugations);
    },
    get entryCount() {
      return this.raw?.meta?.total_entries ?? this.entries.length;
    },
    get confirmedCount() {
      return (
        this.raw?.meta?.confirmed_count ??
        this.entries.filter((e) => e.confidence === 'confirmed').length
      );
    },
    get rulesCount() {
      return this.grammar.length;
    },
    get examplesCount() {
      return this.examples.length;
    },
    get schemaVersion() {
      return this.raw?.meta?.schema_version ?? 1;
    },
    get availableCategories() {
      const set = new Set(this.entries.map((e) => e.category).filter(Boolean));
      return [...set].sort();
    },
    get availableConfidences() {
      return ['confirmed', 'high', 'medium', 'uncertain', 'guess', 'unknown'];
    },
    get filteredEntries() {
      const q = this.search.trim().toLowerCase();
      return this.entries.filter((e) => {
        if (this.categories.length && !this.categories.includes(e.category)) return false;
        if (this.confidences.length && !this.confidences.includes(e.confidence)) return false;
        if (q) {
          const blob = `${e.word} ${e.meaning} ${e.notes ?? ''} ${e.plural ?? ''} ${e.pronunciation ?? ''}`.toLowerCase();
          if (!blob.includes(q)) return false;
        }
        return true;
      });
    },
    get lastSyncedRelative() {
      const ts = this.raw?.meta?.last_updated;
      if (!ts) return 'never';
      const ms = Date.now() - new Date(ts).getTime();
      if (ms < 60_000) return 'just now';
      if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
      if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)}h ago`;
      return `${Math.floor(ms / 86_400_000)}d ago`;
    },

    // ---- Filter handlers --------------------------------------------

    toggleCategory(cat) {
      this.categories = this.categories.includes(cat)
        ? this.categories.filter((c) => c !== cat)
        : [...this.categories, cat];
    },
    toggleConfidence(conf) {
      this.confidences = this.confidences.includes(conf)
        ? this.confidences.filter((c) => c !== conf)
        : [...this.confidences, conf];
    },

    // ---- API mutations ----------------------------------------------

    async patchMeta(body) {
      try {
        const res = await fetch('/api/meta', {
          method: 'PATCH',
          headers: this.adminHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify(body),
        });
        if (res.status === 401) {
          this.promptAdminToken();
          return;
        }
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          this.error = data.error ?? `PATCH failed: ${res.status}`;
          return;
        }
        await this.refresh();
        this.error = '';
      } catch (err) {
        this.error = `Network error: ${err.message}`;
      }
    },

    async syncNow() {
      this.forcing = true;
      try {
        await this.patchMeta({ force_sync: true });
      } finally {
        this.forcing = false;
      }
    },

    // ---- Admin auth (opt-in) ----------------------------------------

    adminHeaders(extra = {}) {
      const token = localStorage.getItem(this.adminTokenKey);
      return token ? { ...extra, 'X-Admin-Token': token } : extra;
    },
    promptAdminToken() {
      const token = window.prompt('Admin token required:');
      if (token && token.trim()) {
        localStorage.setItem(this.adminTokenKey, token.trim());
        this.error = 'Token saved — try again.';
      }
    },

    // ---- Display helpers --------------------------------------------

    confidenceRank(c) {
      const order = ['unknown', 'guess', 'uncertain', 'medium', 'high', 'confirmed'];
      return order.indexOf(c);
    },
    confidenceClass(conf, solid = false) {
      const map = solid
        ? {
            confirmed: 'bg-emerald-600 text-white',
            high: 'bg-sky-600 text-white',
            medium: 'bg-amber-500 text-slate-900',
            uncertain: 'bg-orange-500 text-white',
            guess: 'bg-rose-600 text-white',
            unknown: 'bg-slate-600 text-white',
          }
        : {
            confirmed: 'text-emerald-400',
            high: 'text-sky-400',
            medium: 'text-amber-400',
            uncertain: 'text-orange-400',
            guess: 'text-rose-400',
            unknown: 'text-slate-400',
          };
      return map[conf] ?? (solid ? 'bg-slate-600' : 'text-slate-400');
    },
    discordLink(messageId) {
      const guild = this.raw?.meta?.guild_id;
      const channel = this.raw?.meta?.channel_id;
      if (!guild || !channel || !messageId) return '#';
      return `https://discord.com/channels/${guild}/${channel}/${messageId}`;
    },
  }));
});
