import { describe, expect, it } from 'vitest';
import { withRequestTimeout } from '../src/api/queries';

describe('SDO API request timeouts', () => {
  it('always settles and aborts even when transport ignores the signal', async () => {
    let captured: AbortSignal | undefined;
    const started = Date.now();
    await expect(
      withRequestTimeout(
        (signal) => {
          captured = signal;
          return new Promise<string>(() => {});
        },
        25,
      ),
    ).rejects.toThrow('request_timeout');
    expect(captured?.aborted).toBe(true);
    expect(Date.now() - started).toBeLessThan(1_000);
  });

  it('keeps the successful response and does not abort before completion', async () => {
    let captured: AbortSignal | undefined;
    const value = await withRequestTimeout((signal) => {
      captured = signal;
      return Promise.resolve({ state: 'queued' });
    }, 50);
    expect(value).toEqual({ state: 'queued' });
    expect(captured?.aborted).toBe(false);
  });
});
