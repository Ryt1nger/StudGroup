import { describe, expect, it, vi } from 'vitest';
import { requestSupportSession } from '../src/session/support';

describe('embedded owner support', () => {
  it('receives credentials only from the exact admin origin and window', async () => {
    const origin = 'https://api.studgroup.example';
    const host = window;
    const send = vi.spyOn(host, 'postMessage').mockImplementation(() => undefined);
    const result = { access_token: 'sgsupport1.test', mode: 'view' };
    const promise = requestSupportSession(host, origin);
    let resolved = false;
    void promise.then(() => { resolved = true; });
    try {
      expect(send).toHaveBeenCalledWith({ type: 'studgroup-support-ready' }, origin);
      const data = { type: 'studgroup-support-session', result };
      window.dispatchEvent(new MessageEvent('message', { source: host, origin: 'https://wrong.example', data }));
      window.dispatchEvent(new MessageEvent('message', { source: null, origin, data }));
      await Promise.resolve();
      expect(resolved).toBe(false);
      window.dispatchEvent(new MessageEvent('message', { source: host, origin, data }));
      expect(await promise).toEqual(result);
    } finally {
      send.mockRestore();
    }
  });
});
