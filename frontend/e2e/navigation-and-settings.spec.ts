import { test, expect } from './fixtures';

test.describe('VTT Transcribe - Navigation', () => {
  test('should display left navigation menu', async ({ page }) => {
    // Check navigation exists
    const nav = page.locator('nav.navigation');
    await expect(nav).toBeVisible();

    // Check menu items
    await expect(page.getByRole('button', { name: 'Home' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Jobs' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Profile' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'About' })).toBeVisible();
  });

  test('should navigate between pages', async ({ page }) => {
    // Navigate to About page
    await page.getByRole('button', { name: 'About' }).click();
    await expect(page.getByText('About VTT Transcribe')).toBeVisible();

    // Navigate to Jobs page
    await page.getByRole('button', { name: 'Jobs' }).click();
    await expect(page.getByText('Transcription Jobs')).toBeVisible();

    // Navigate back to Home
    await page.getByRole('button', { name: 'Home' }).click();
    await expect(page.getByText('AI-Powered Video Transcription')).toBeVisible();
  });

  test('should open the profile (settings) page from navigation', async ({ page }) => {
    await page.getByRole('list').getByRole('button', { name: 'Profile' }).click();
    await expect(page.getByRole('heading', { name: 'Defaults for new jobs' })).toBeVisible();
    await expect(page.getByLabel('Default translation language')).toBeVisible();
  });
});

test.describe('VTT Transcribe - User Menu', () => {
  test('should display user menu in top right', async ({ page }) => {
    // Check user menu exists
    const userMenu = page.locator('.user-menu');
    await expect(userMenu).toBeVisible();
    await expect(page.getByText('testuser')).toBeVisible();
  });

  test('should open user menu dropdown', async ({ page }) => {
    // Click user menu button
    await page.getByRole('button', { name: 'User menu' }).click();

    // Dropdown should appear - check Logout button in the dropdown menu
    await expect(page.getByRole('banner').getByRole('button', { name: 'Logout' })).toBeVisible();
  });

  test('should open the profile page from user menu', async ({ page }) => {
    await page.getByRole('button', { name: 'User menu' }).click();
    await page.getByRole('banner').getByRole('button', { name: 'Settings' }).click();
    await expect(page.getByRole('heading', { name: 'Defaults for new jobs' })).toBeVisible();
  });
});
