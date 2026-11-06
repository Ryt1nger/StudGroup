import type { SessionContext, TodayResponse } from '@studgroup/shared-types';
import { dayKey } from '../lib/format';
import type { QueryClient, QueryKey } from '@tanstack/react-query';

/**
 * A small, encrypted, *read-only* snapshot of the academic feed for a returning student.
 *
 * The snapshot is never decrypted or hydrated before a fresh server-side Telegram
 * bootstrap confirms the user and active membership. Tokens, profiles, photos,
 * notifications, editable drafts and review queues are never persisted.
 *
 * Encryption uses a non-extractable WebCrypto AES-GCM key stored as a CryptoKey in
 * IndexedDB. No plaintext student data, tokens or keys go to localStorage.
 * Browser storage is still device-local: the app must not treat it as authorization.
 */
const DB_NAME = 'studgroup-academic-cache-v1';
const STORE = 'private';
const CACHE_VERSION = 1;
const MAX_AGE_MS = 24 * 60 * 60 * 1000;
const MAX_PAYLOAD_BYTES = 400_000;
const MAX_QUERIES = 32;
const WRITE_DEBOUNCE_MS = 250;

interface CachedQuery {
  key: QueryKey;
  data: unknown;
  updatedAt: number;
}
interface CachedPayload {
  version: number;
  owner: string;
  queries: CachedQuery[];
}
interface EncryptedRecord {
  version: number;
  owner: string;
  savedAt: number;
  iv: Uint8Array<ArrayBuffer>;
  ciphertext: ArrayBuffer;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Fail closed on unexpected keys; never persist profile, images, sessions or staff drafts. */
export function canCacheAcademicQuery(key: readonly unknown[]): boolean {
  if (key.length === 2 && key[0] === 'today') return key[1] === 'today' || key[1] === 'tomorrow';
  if (key.length === 3 && key[0] === 'schedule') {
    return typeof key[1] === 'string' && typeof key[2] === 'string'
      && /^\d{4}-\d{2}-\d{2}$/.test(key[1]) && /^\d{4}-\d{2}-\d{2}$/.test(key[2]);
  }
  if (key.length === 3 && key[0] === 'academic-deadlines') return key[1] === false && key[2] === false;
  if (key.length === 1) return key[0] === 'homework-counts' || key[0] === 'subjects';
  if (key.length === 3 && key[0] === 'group-events' && key[1] === 'list' && isRecord(key[2])) {
    const opts = key[2];
    return opts.history !== true && (opts.start === undefined || typeof opts.start === 'string')
      && (opts.end === undefined || typeof opts.end === 'string');
  }
  return false;
}

export function academicCacheOwner(session: SessionContext): string | null {
  if (session.access_state !== 'active' || !session.group || !session.membership
    || session.membership.status !== 'active') return null;
  // Distinguish accounts, groups, memberships and role changes, without depending
  // on the Telegram username or a rotating access token.
  return [session.user.id, session.group.id, session.membership.id, session.membership.role].join(':');
}

function available(): boolean {
  return typeof indexedDB !== 'undefined' && typeof crypto !== 'undefined' &&
    typeof crypto.subtle !== 'undefined';
}

function openDatabase(): Promise<IDBDatabase | null> {
  if (!available()) return Promise.resolve(null);
  return new Promise((resolve) => {
    try {
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(STORE)) request.result.createObjectStore(STORE);
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(null);
      request.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

function readValue(db: IDBDatabase, name: string): Promise<unknown> {
  return new Promise((resolve) => {
    try {
      const request = db.transaction(STORE, 'readonly').objectStore(STORE).get(name);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(undefined);
    } catch { resolve(undefined); }
  });
}

function writeValue(db: IDBDatabase, name: string, value: unknown): Promise<boolean> {
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).put(value, name);
      tx.oncomplete = () => resolve(true);
      tx.onerror = () => resolve(false);
      tx.onabort = () => resolve(false);
    } catch { resolve(false); }
  });
}

async function keyFor(db: IDBDatabase, create: boolean): Promise<CryptoKey | null> {
  const existing = await readValue(db, 'key');
  if (existing instanceof CryptoKey) return existing;
  if (!create) return null;
  const key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
  return (await writeValue(db, 'key', key)) ? key : null;
}

function collect(client: QueryClient, owner: string): CachedPayload | null {
  // Largest / oldest optional views are removed first if the browser storage
  // budget is tight; the Today feed always has priority.
  const queries = client.getQueryCache().getAll()
    .filter((query) => canCacheAcademicQuery(query.queryKey) && query.state.status === 'success' && query.state.data !== undefined)
    .map((query) => ({ key: query.queryKey, data: query.state.data, updatedAt: query.state.dataUpdatedAt }))
    .filter((query) => query.updatedAt > 0 && Date.now() - query.updatedAt <= MAX_AGE_MS)
    .sort((a, b) => (a.key[0] === 'today' ? -1 : b.key[0] === 'today' ? 1 : b.updatedAt - a.updatedAt))
    .slice(0, MAX_QUERIES);
  if (!queries.length) return null;
  const payload: CachedPayload = { version: CACHE_VERSION, owner, queries };
  while (payload.queries.length) {
    try {
      if (new TextEncoder().encode(JSON.stringify(payload)).byteLength <= MAX_PAYLOAD_BYTES) return payload;
    } catch { return null; }
    payload.queries.pop();
  }
  return null;
}

async function store(client: QueryClient, owner: string): Promise<void> {
  const payload = collect(client, owner);
  if (!payload) return;
  const db = await openDatabase();
  if (!db) return;
  try {
    const key = await keyFor(db, true);
    if (!key) return;
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const plaintext = new TextEncoder().encode(JSON.stringify(payload));
    const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, plaintext);
    const record: EncryptedRecord = { version: CACHE_VERSION, owner, savedAt: Date.now(), iv, ciphertext };
    await writeValue(db, 'snapshot', record);
  } catch {
    // Disabled storage, quota, private mode, interrupted write: normal online behaviour wins.
  } finally {
    db.close();
  }
}

/** Returns a count; an unavailable or invalid cache never blocks login. */
export async function restoreAcademicCache(client: QueryClient, owner: string, serverTime: string, isCurrent: () => boolean = () => true): Promise<number> {
  const db = await openDatabase();
  if (!db) return 0;
  try {
    const record = await readValue(db, 'snapshot');
    if (!isRecord(record) || record.version !== CACHE_VERSION || record.owner !== owner
      || typeof record.savedAt !== 'number' || record.savedAt > Date.now()
      || Date.now() - record.savedAt > MAX_AGE_MS
      || !(record.iv instanceof Uint8Array) || !(record.ciphertext instanceof ArrayBuffer)) return 0;
    const key = await keyFor(db, false);
    if (!key) return 0;
    const decrypted = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: new Uint8Array(record.iv) },
      key,
      record.ciphertext,
    );
    const payload: unknown = JSON.parse(new TextDecoder().decode(decrypted));
    if (!isRecord(payload) || payload.version !== CACHE_VERSION || payload.owner !== owner
      || !Array.isArray(payload.queries) || payload.queries.length > MAX_QUERIES) return 0;
    let restored = 0;
    for (const item of payload.queries) {
      if (!isRecord(item) || !Array.isArray(item.key) || !canCacheAcademicQuery(item.key)
        || typeof item.updatedAt !== 'number' || !Number.isFinite(item.updatedAt)
        || item.updatedAt > Date.now() || Date.now() - item.updatedAt > MAX_AGE_MS
        || item.data === undefined) continue;
      if (!isCurrent()) return restored;
      // A "today" snapshot from yesterday is never a safe representation of
      // today's lesson/deadline state. Compare using trusted bootstrap server
      // time and the group's timezone, not the device's potentially wrong clock.
      if (item.key[0] === 'today') {
        const feed = item.data as Partial<TodayResponse> | null;
        if (!feed || typeof feed.server_time !== 'string' || typeof feed.group_timezone !== 'string') continue;
        try {
          if (dayKey(feed.server_time, feed.group_timezone) !== dayKey(serverTime, feed.group_timezone)) continue;
        } catch { continue; }
      }
      // Never overwrite a fresher response that arrived while IndexedDB was opening.
      const current = client.getQueryState(item.key);
      if (current && current.dataUpdatedAt >= item.updatedAt) continue;
      client.setQueryData(item.key, item.data, { updatedAt: item.updatedAt });
      restored++;
    }
    return restored;
  } catch {
    return 0;
  } finally {
    db.close();
  }
}

export function watchAcademicCache(client: QueryClient, owner: string): () => void {
  if (!available()) return () => undefined;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let chain = Promise.resolve();
  const flush = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    chain = chain.then(() => store(client, owner), () => store(client, owner));
  };
  const unsubscribe = client.getQueryCache().subscribe((event) => {
    if (event.type !== 'updated' || event.action.type !== 'success'
      || !canCacheAcademicQuery(event.query.queryKey)) return;
    if (timer === null) timer = setTimeout(flush, WRITE_DEBOUNCE_MS);
  });
  const onHide = () => { if (document.visibilityState === 'hidden' && timer !== null) flush(); };
  const onPageHide = () => { if (timer !== null) flush(); };
  document.addEventListener('visibilitychange', onHide);
  window.addEventListener('pagehide', onPageHide);
  return () => {
    unsubscribe();
    document.removeEventListener('visibilitychange', onHide);
    window.removeEventListener('pagehide', onPageHide);
    if (timer !== null) flush();
  };
}
