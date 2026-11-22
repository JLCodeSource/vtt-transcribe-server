import { test, expect } from './fixtures';

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

const JOBS = {
  queue_length: 0,
  running: null,
  jobs: [{ job_id: 'j1', filename: 'meeting.mp3', status: 'completed', step: 'done', progress: 1, queue_position: null, expires_at: null }],
};

const VERSIONS = {
  job_id: 'j1',
  original: { language: 'en', segments: 1 },
  translations: [{ language: 'Spanish', created_at: '2026-01-01T10:00:00Z' }],
  diarizations: [
    { id: 'd1', label: 'Auto (2 found)', num_speakers: null, speakers: 2, active: false, created_at: '2026-01-01T09:00:00Z' },
    { id: 'd2', label: '3 requested, 2 found', num_speakers: 3, speakers: 2, active: true, created_at: '2026-01-01T11:00:00Z' },
  ],
  active_diarization: 'd2',
};

const view = (language: string) => ({
  job_id: 'j1',
  language,
  translated_to: language === 'original' ? null : language,
  diarization: 'd2',
  active_diarization: 'd2',
  engine: 'openai',
  speakers: [],
  segments: [
    { index: 0, start: 0, end: 4, text: 'Hello there', translation: language === 'original' ? null : 'Hola', speaker_id: 'SPEAKER_00', speaker: 'Speaker 1' },
  ],
});

test.describe('Versions', () => {
  test('the language switcher shows the original and each stored translation, and the speaker versions say requested vs found', async ({ page }) => {
    const requested: string[] = [];
    await page.route('**/api/jobs', (route) => route.fulfill(json(JOBS)));
    await page.route('**/api/jobs/j1/versions', (route) => route.fulfill(json(VERSIONS)));
    await page.route('**/api/jobs/j1/segments**', (route) => {
      const language = new URL(route.request().url()).searchParams.get('language') ?? 'original';
      requested.push(language);
      return route.fulfill(json(view(language)));
    });

    await page.getByRole('button', { name: 'Jobs' }).click();
    await page.getByRole('button', { name: 'Open' }).click();

    await expect(page.getByText('Hello there')).toBeVisible();
    const language = page.getByLabel('Language', { exact: true });
    await expect(language.locator('option')).toHaveText(['Original (en)', 'Spanish']);
    await expect(page.getByLabel('Speaker version')).toContainText('3 requested, 2 found');

    await language.selectOption('Spanish');
    await expect(page.getByText('Hola')).toBeVisible();
    expect(requested).toContain('Spanish');

    await page.getByLabel('Side by side').check();
    await expect(page.getByText('Hello there')).toBeVisible();
    await expect(page.getByText('Hola')).toBeVisible();
  });
});
