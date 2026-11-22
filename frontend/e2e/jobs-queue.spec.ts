import { test, expect } from './fixtures';

const JOBS = {
  queue_length: 1,
  running: 'j1',
  jobs: [
    { job_id: 'j2', filename: 'second.mp3', status: 'pending', step: 'queued', progress: 0, queue_position: 1, expires_at: null },
    { job_id: 'j1', filename: 'first.mp3', status: 'processing', step: 'transcribing', progress: 0.5, queue_position: null, expires_at: null, message: 'Transcribing audio' },
  ],
};

test.describe('Jobs page', () => {
  test('shows the running job with progress and the queued job with its position', async ({ page }) => {
    await page.route('**/api/jobs', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(JOBS) }));
    await page.getByRole('button', { name: 'Jobs' }).click();
    await expect(page.getByText('first.mp3')).toBeVisible();
    await expect(page.getByText('50%')).toBeVisible();
    await expect(page.getByText(/position 1 in the queue/)).toBeVisible();
  });

  test('submitting a file lands on the Jobs page', async ({ page }) => {
    await page.route('**/api/profile', (route) => route.fulfill({ status: 404, body: '{}' }));
    await page.route('**/api/transcribe', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'j2', status: 'pending' }) }));
    await page.route('**/api/jobs', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(JOBS) }));
    await page.locator('#file-input').setInputFiles({ name: 'second.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('audio') });
    await page.getByLabel('Engine').selectOption('openai');
    await page.getByPlaceholder('sk-...').fill('sk-test-key-0000');
    await page.getByRole('button', { name: /Add to queue/ }).click();
    await expect(page.getByRole('heading', { name: /Transcription Jobs/ })).toBeVisible();
    await expect(page.getByText('second.mp3')).toBeVisible();
  });
});
