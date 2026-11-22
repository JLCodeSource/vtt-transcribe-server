import { test as base, expect } from '@playwright/test';

/**
 * Extended test fixture that provides authenticated session
 * Registers a test user and logs in before each test
 */
export const test = base.extend({
  // Automatically login before each test
  page: async ({ page }, use) => {
    await page.route('**/auth/me', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ username: 'testuser', email: 'test@example.com' }),
      });
    });

    await page.route('**/oauth/providers', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ providers: [] }),
      });
    });

    // The profile page and the upload form read /api/profile; the stand-in session has no real account behind it.
    await page.route('**/api/profile', async (route) => {
      if (route.request().method() !== 'GET') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          username: 'testuser', email: 'test@example.com', oauth_provider: null, retention_mode: 'default', retention_days: null,
          effective_retention: { mode: 'hold', days: 7 }, platform_retention: { mode: 'hold', days: 7 }, notify_job_ready: true,
          email_enabled: false, default_options: {}, has_openai_key: false, openai_key_hint: null,
        }),
      });
    });

    await page.addInitScript(() => {
      window.localStorage.setItem('access_token', 'e2e-test-token');
      window.localStorage.setItem('username', 'testuser');
    });

    await page.goto('/');
    await expect(page.getByText(/Drop your video or audio files here/i)).toBeVisible({ timeout: 15000 });

    // Use the authenticated page
    await use(page);
  },
});

export { expect };
