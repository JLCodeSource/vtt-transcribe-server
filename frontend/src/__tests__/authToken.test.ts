import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { authHeaders, clipUrl, fetchEngines, setAuthToken, withToken } from '../api';

describe('auth token on API calls', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ engines: [], diarizers: [], max_speakers: 12 }) })));
  });
  afterEach(() => setAuthToken(null));

  it('sends no Authorization header before sign-in', async () => {
    await fetchEngines();
    const init = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it('sends the bearer token on every request after sign-in', async () => {
    setAuthToken('tok-123');
    await fetchEngines();
    const init = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok-123');
    expect(authHeaders()).toEqual({ Authorization: 'Bearer tok-123' });
  });

  it('forgets the token on sign-out', () => {
    setAuthToken('tok-123');
    setAuthToken(null);
    expect(authHeaders()).toEqual({});
  });

  it('puts the token in the query string for URLs that cannot carry headers', () => {
    expect(withToken('/ws/jobs/j1')).toBe('/ws/jobs/j1');
    setAuthToken('a b+c');
    expect(withToken('/ws/jobs/j1')).toBe('/ws/jobs/j1?access_token=a%20b%2Bc');
    expect(withToken('/x?y=1')).toBe('/x?y=1&access_token=a%20b%2Bc');
  });

  it('adds the token to speaker clip URLs, with or without a time range', () => {
    setAuthToken('tok');
    expect(clipUrl('j1', 'SPEAKER_00')).toBe('/api/jobs/j1/speakers/SPEAKER_00/clip?access_token=tok');
    expect(clipUrl('j1', 'SPEAKER_00', 1, 4)).toBe('/api/jobs/j1/speakers/SPEAKER_00/clip?start=1&end=4&access_token=tok');
  });
});
