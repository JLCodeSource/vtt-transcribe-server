import { vi, type Mock } from 'vitest';

/** Replace globalThis.fetch with a vi.fn() and return it, typed. */
export function installFetchMock(): Mock {
  const mock = vi.fn();
  globalThis.fetch = mock as unknown as typeof fetch;
  return mock;
}

/** Response the Login component's OAuth-providers fetch gets on mount. */
export const noProviders = { ok: false, json: async () => ({ providers: [] }) };
