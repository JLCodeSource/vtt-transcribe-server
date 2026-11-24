import { vi, type Mock } from 'vitest';

/** Replace globalThis.fetch with a vi.fn() and return it, typed. */
export function installFetchMock(): Mock {
  const mock = vi.fn();
  globalThis.fetch = mock as unknown as typeof fetch;
  return mock;
}

/** Response the Login component's OAuth-providers fetch gets on mount. */
export const noProviders = { ok: false, json: async () => ({ providers: [] }) };

/** A successful fetch Response stub whose json() resolves to `body`. */
export const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

/** The /api/profile payload; override any field per test. */
export const makeProfile = (over: Record<string, unknown> = {}) => ({
  username: 'alice',
  email: 'a@example.com',
  oauth_provider: null,
  retention_mode: 'default',
  retention_days: null,
  effective_retention: { mode: 'hold', days: 7 },
  platform_retention: { mode: 'hold', days: 7 },
  notify_job_ready: true,
  email_enabled: false,
  default_options: {},
  has_openai_key: true,
  openai_key_hint: '****abcd',
  ...over,
});
