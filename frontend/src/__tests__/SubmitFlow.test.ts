import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import App from '../App.svelte';

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
const profile = {
  username: 'alice', email: 'a@example.com', oauth_provider: null, retention_mode: 'default', retention_days: null,
  effective_retention: { mode: 'hold', days: 7 }, platform_retention: { mode: 'hold', days: 7 }, notify_job_ready: true,
  email_enabled: false, default_options: {}, has_openai_key: true, openai_key_hint: '****abcd',
};
const queued = { jobs: [{ job_id: 'j9', filename: 'talk.mp3', status: 'pending', step: 'queued', progress: 0, queue_position: 1, expires_at: null }], queue_length: 1, running: null };

describe('submitting a file', () => {
  const requests: Array<[string, RequestInit?]> = [];

  beforeEach(() => {
    requests.length = 0;
    localStorage.setItem('access_token', 'tok');
    localStorage.setItem('username', 'alice');
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        requests.push([url, init]);
        if (url === '/api/transcribe') return ok({ job_id: 'j9', status: 'pending' });
        if (url === '/api/jobs') return ok(queued);
        if (url === '/api/profile') return ok(profile);
        if (url === '/api/engines') return ok({ engines: [{ id: 'openai', label: 'OpenAI', requires_api_key: true, available: true }], max_speakers: 12 });
        if (url === '/auth/me') return ok({ id: 1, username: 'alice', email: 'a@example.com', is_active: true, is_superuser: false, must_change_password: false });
        return ok({ providers: [] });
      }),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('goes straight to the Jobs page, which shows the queued job; the main page stays ready for more', async () => {
    const { container } = render(App);
    await screen.findByText(/Drop your video or audio files here/i);
    const input = container.querySelector('#file-input') as HTMLInputElement;
    const file = new File(['abc'], 'talk.mp3', { type: 'audio/mpeg' });
    await fireEvent.change(input, { target: { files: [file] } });

    await fireEvent.change(await screen.findByLabelText('Retention'), { target: { value: 'delete' } });
    await fireEvent.click(await screen.findByRole('button', { name: /Add to queue/i }));

    await screen.findByRole('heading', { name: /Transcription Jobs/ });
    await screen.findByText(/position 1 in the queue/);
    const upload = requests.find(([u]) => u === '/api/transcribe');
    expect((upload?.[1]?.body as FormData).get('retention_mode')).toBe('delete');
    expect((upload?.[1]?.body as FormData).get('api_key')).toBeFalsy(); // the profile key covers it

    await fireEvent.click(screen.getByRole('button', { name: /Home/ }));
    await waitFor(() => expect(screen.getByText(/Drop your video or audio files here/i)).toBeTruthy());
  });
});
