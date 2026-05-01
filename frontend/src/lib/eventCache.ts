/**
 * Offline event-tape cache (Track 3 B5).
 *
 * Keeps the last 200 spine events in IndexedDB so the cockpit's tape
 * remains readable when the backend is down — the operator can scrub
 * the recent audit trail even on a flaky link / on a plane / during a
 * deploy.
 *
 * IndexedDB is preferred over localStorage because the tape can grow
 * past localStorage's 5MB quota on long-running sessions and
 * IndexedDB's transactional writes are robust against tab close
 * during a refresh.
 *
 * Falls back to a tiny in-memory cache when IndexedDB is unavailable
 * (private browsing, some corporate locked-down environments). The
 * fallback survives navigation but not a full reload — that's the
 * cost of running without IDB.
 */

import type { SpineEvent } from "./api";

const DB_NAME = "ai-retail-os";
const STORE_NAME = "event_tape";
const DB_VERSION = 1;
const MAX_CACHED = 200;

let _dbPromise: Promise<IDBDatabase | null> | null = null;
let _memCache: SpineEvent[] = [];

function _openDb(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === "undefined") return Promise.resolve(null);
  if (_dbPromise) return _dbPromise;
  _dbPromise = new Promise((resolve) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "id" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => resolve(null);
    req.onblocked = () => resolve(null);
  });
  return _dbPromise;
}

/**
 * Persist a batch of events to the cache. Caller is expected to feed
 * fresh rows from `listEvents`; the cache trims to the most recent
 * `MAX_CACHED` ids by id-descending order so the operator's tape view
 * stays bounded regardless of how long the session has been running.
 */
export async function cacheEvents(events: SpineEvent[]): Promise<void> {
  if (!events.length) return;
  // Always update the in-memory mirror — it's our fallback when
  // IndexedDB is unavailable AND it's a cheap fast-path read.
  const merged = mergeAndTrim(_memCache, events);
  _memCache = merged;

  const db = await _openDb();
  if (!db) return;
  await new Promise<void>((resolve) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    for (const e of events) {
      try {
        store.put(e);
      } catch {
        // Skip malformed rows; preserve transactional integrity for
        // the rest of the batch.
      }
    }
    tx.oncomplete = () => resolve();
    tx.onerror = () => resolve();
    tx.onabort = () => resolve();
  });
  // After write, prune to MAX_CACHED. Reads are id-desc so trimming
  // happens by deleting the oldest ids.
  await trimCache();
}

async function trimCache(): Promise<void> {
  const db = await _openDb();
  if (!db) return;
  const all = await new Promise<SpineEvent[]>((resolve) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const req = tx.objectStore(STORE_NAME).getAll();
    req.onsuccess = () => resolve((req.result as SpineEvent[]) ?? []);
    req.onerror = () => resolve([]);
  });
  if (all.length <= MAX_CACHED) return;
  all.sort((a, b) => b.id - a.id);
  const drop = all.slice(MAX_CACHED).map((e) => e.id);
  await new Promise<void>((resolve) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    for (const id of drop) {
      try {
        store.delete(id);
      } catch {
        /* skip */
      }
    }
    tx.oncomplete = () => resolve();
    tx.onerror = () => resolve();
  });
}

/**
 * Read the cached events back, ordered most-recent-first. Used by the
 * EventTape when the network listEvents call fails — the operator
 * still sees the last 200 events without flicker.
 */
export async function readCachedEvents(limit = MAX_CACHED): Promise<SpineEvent[]> {
  const db = await _openDb();
  if (!db) {
    return _memCache.slice(0, limit);
  }
  const rows = await new Promise<SpineEvent[]>((resolve) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const req = tx.objectStore(STORE_NAME).getAll();
    req.onsuccess = () => resolve((req.result as SpineEvent[]) ?? []);
    req.onerror = () => resolve([]);
  });
  rows.sort((a, b) => b.id - a.id);
  return rows.slice(0, limit);
}

function mergeAndTrim(prev: SpineEvent[], fresh: SpineEvent[]): SpineEvent[] {
  const seen = new Map<number, SpineEvent>();
  for (const e of prev) seen.set(e.id, e);
  for (const e of fresh) seen.set(e.id, e);
  const out = Array.from(seen.values());
  out.sort((a, b) => b.id - a.id);
  return out.slice(0, MAX_CACHED);
}

/** Wipe the cache — for tests and a possible future "clear local
 *  history" operator action. */
export async function clearCache(): Promise<void> {
  _memCache = [];
  const db = await _openDb();
  if (!db) return;
  await new Promise<void>((resolve) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).clear();
    tx.oncomplete = () => resolve();
    tx.onerror = () => resolve();
  });
}
