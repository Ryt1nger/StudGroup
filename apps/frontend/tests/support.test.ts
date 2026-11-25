import { describe, expect, it } from 'vitest';
import { isSupportMessage } from '../src/session/support';

describe('embedded support handshake', () => {
  it('accepts only the expected parent origin, source and message type', () => {
    const origin = 'https://api.studgroup.example';
    const frame = document.createElement('iframe');
    document.body.append(frame);
    try {
      const peer = frame.contentWindow!;
      const event = (source: Window, from: string, type = 'studgroup-support-session') =>
        new MessageEvent('message', { source, origin: from, data: { type } });
      expect(isSupportMessage(event(peer, origin), peer, origin)).toBe(true);
      expect(isSupportMessage(event(window, origin), peer, origin)).toBe(false);
      expect(isSupportMessage(event(peer, 'https://evil.example'), peer, origin)).toBe(false);
      expect(isSupportMessage(event(peer, origin, 'other'), peer, origin)).toBe(false);
    } finally {
      frame.remove();
    }
  });
});
