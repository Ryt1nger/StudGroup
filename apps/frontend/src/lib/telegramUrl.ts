/** Slice 1 contract: source URLs are backend-built, https only, exact `t.me` host. */
export function isAllowedTelegramUrl(value: string | null | undefined): value is string {
  if (!value) return false;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.hostname === 't.me' && !url.username && !url.password && !url.port;
  } catch {
    return false;
  }
}
