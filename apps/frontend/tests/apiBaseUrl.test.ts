import { describe, expect, it } from 'vitest';
import { normalizeApiBaseUrl } from '../src/api/client';

describe('normalizeApiBaseUrl', () => {
  it('adds the versioned API prefix when Render supplies only an origin', () => {
    expect(normalizeApiBaseUrl('https://studgroup-api-v2.onrender.com')).toBe(
      'https://studgroup-api-v2.onrender.com/v1',
    );
  });

  it('does not duplicate the version prefix', () => {
    expect(normalizeApiBaseUrl('https://studgroup-api-v2.onrender.com/v1')).toBe(
      'https://studgroup-api-v2.onrender.com/v1',
    );
    expect(normalizeApiBaseUrl('https://studgroup-api-v2.onrender.com/v1/')).toBe(
      'https://studgroup-api-v2.onrender.com/v1',
    );
  });

  it('preserves local development and the optional no-config mode', () => {
    expect(normalizeApiBaseUrl('http://localhost:8000')).toBe('http://localhost:8000/v1');
    expect(normalizeApiBaseUrl(undefined)).toBeUndefined();
    expect(normalizeApiBaseUrl('')).toBeUndefined();
  });
});
