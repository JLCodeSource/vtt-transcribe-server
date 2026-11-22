import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import Jobs from '../components/Jobs.svelte';
import Profile from '../components/Profile.svelte';

const job = (over: Record<string, unknown> = {}) => ({
  job_id: 'j1',
  filename: 'meeting.mp3',
  status: 'pending',
  step: 'queued',
  progress: 0,
  queue_position: 1,
  retention_mode: 'hold',
  retention_days: 7,
  expires_at: null,
  ...over,
});
const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
const list = (jobs: unknown[]) => ok({ jobs, queue_length: jobs.length, running: null });
const calls = () => (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls as Array<[string, RequestInit?]>;

describe('Jobs page', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
    vi.stubGlobal('confirm', vi.fn(() => true));
  });
  afterEach(() => vi.unstubAllGlobals());

  it('shows the queue position of a waiting job', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(list([job({ queue_position: 3 })]));
    render(Jobs, { onopen: () => {} });
    await screen.findByText('meeting.mp3');
    expect(screen.getByText(/position 3 in the queue/)).toBeTruthy();
    expect(screen.getByText('queued')).toBeTruthy();
  });

  it('shows the pipeline step and a percentage for the running job', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      list([job({ status: 'processing', step: 'transcribing', progress: 0.42, queue_position: null, message: 'Transcribing audio' })]),
    );
    render(Jobs, { onopen: () => {} });
    await screen.findByText(/Transcribing — Transcribing audio/);
    expect(screen.getByText('42%')).toBeTruthy();
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('42');
  });

  it('cancels a queued job and refreshes', async () => {
    const f = fetch as ReturnType<typeof vi.fn>;
    f.mockResolvedValueOnce(list([job()]))
      .mockResolvedValueOnce(ok(job({ status: 'cancelled' })))
      .mockResolvedValueOnce(list([job({ status: 'cancelled', step: 'cancelled', queue_position: null })]));
    render(Jobs, { onopen: () => {} });
    await fireEvent.click(await screen.findByText('Cancel'));
    await waitFor(() => expect(screen.getAllByText('cancelled').length).toBeGreaterThan(0));
    expect(calls()[1][0]).toBe('/api/jobs/j1/cancel');
    expect(calls()[1][1]?.method).toBe('POST');
  });

  it('opens a finished job and shows when it expires', async () => {
    const onopen = vi.fn();
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      list([job({ status: 'completed', step: 'done', progress: 1, queue_position: null, expires_at: '2030-01-08T12:00:00+00:00' })]),
    );
    render(Jobs, { onopen });
    await fireEvent.click(await screen.findByText('Open'));
    expect(onopen).toHaveBeenCalledOnce();
    expect(screen.getByText(/Expires/)).toBeTruthy();
    expect(screen.getByText('TXT').getAttribute('href')).toContain('/api/jobs/j1/download?format=txt');
  });

  it('changes the retention of a job', async () => {
    const f = fetch as ReturnType<typeof vi.fn>;
    const done = job({ status: 'completed', step: 'done', queue_position: null });
    f.mockResolvedValueOnce(list([done])).mockResolvedValueOnce(ok(done)).mockResolvedValueOnce(list([done]));
    render(Jobs, { onopen: () => {} });
    await fireEvent.click(await screen.findByText('Retention'));
    await fireEvent.change(screen.getByLabelText('Retention policy'), { target: { value: 'keep' } });
    await fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(calls()[1][0]).toBe('/api/jobs/j1/retention'));
    expect(JSON.parse(calls()[1][1]?.body as string)).toEqual({ mode: 'keep', days: null });
  });

  it('opens the job from an email link once it is finished', async () => {
    const onopen = vi.fn();
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(list([job({ status: 'completed', step: 'done', queue_position: null })]));
    render(Jobs, { onopen, focusJobId: 'j1' });
    await waitFor(() => expect(onopen).toHaveBeenCalledOnce());
  });

  it('has an empty state', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(list([]));
    render(Jobs, { onopen: () => {} });
    await screen.findByText(/No jobs yet/);
  });
});

const profile = (over: Record<string, unknown> = {}) => ({
  username: 'alice',
  email: 'alice@example.com',
  oauth_provider: null,
  retention_mode: 'default',
  retention_days: null,
  effective_retention: { mode: 'hold', days: 7 },
  platform_retention: { mode: 'hold', days: 7 },
  notify_job_ready: true,
  email_enabled: true,
  default_options: {},
  has_openai_key: false,
  openai_key_hint: null,
  ...over,
});

describe('Profile page', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('saves a retention default', async () => {
    const f = fetch as ReturnType<typeof vi.fn>;
    f.mockResolvedValueOnce(ok(profile())).mockResolvedValueOnce(ok(profile({ retention_mode: 'keep' })));
    render(Profile, { token: 't' });
    await fireEvent.change(await screen.findByLabelText('Default retention'), { target: { value: 'keep' } });
    await fireEvent.click(screen.getByText('Save retention'));
    await screen.findByText('Retention saved');
    expect(calls()[1][1]?.method).toBe('PATCH');
    expect(JSON.parse(calls()[1][1]?.body as string)).toEqual({ retention_mode: 'keep', retention_days: null });
  });

  it('shows only the masked tail of a stored key and never the key', async () => {
    const f = fetch as ReturnType<typeof vi.fn>;
    f.mockResolvedValueOnce(ok(profile({ has_openai_key: true, openai_key_hint: '****cdef' })));
    const { container } = render(Profile, { token: 't' });
    await screen.findByText('****cdef');
    expect(container.textContent).not.toContain('sk-');
    expect(screen.getByText('Remove key')).toBeTruthy();
  });

  it('stores a new key, then clears the field', async () => {
    const f = fetch as ReturnType<typeof vi.fn>;
    f.mockResolvedValueOnce(ok(profile())).mockResolvedValueOnce(ok(profile({ has_openai_key: true, openai_key_hint: '****9999' })));
    render(Profile, { token: 't' });
    const input = (await screen.findByLabelText('OpenAI API key')) as HTMLInputElement;
    await fireEvent.input(input, { target: { value: 'sk-secret-key-9999' } });
    await fireEvent.click(screen.getByText('Save key'));
    await screen.findByText('****9999');
    expect(calls()[1][0]).toBe('/api/profile/openai-key');
    expect(input.value).toBe('');
  });

  it('opts out of the job-ready email', async () => {
    const f = fetch as ReturnType<typeof vi.fn>;
    f.mockResolvedValueOnce(ok(profile())).mockResolvedValueOnce(ok(profile({ notify_job_ready: false })));
    render(Profile, { token: 't' });
    await fireEvent.click(await screen.findByLabelText(/Email me when a job finishes/));
    await waitFor(() => expect(calls()[1][1]?.method).toBe('PATCH'));
    expect(JSON.parse(calls()[1][1]?.body as string)).toEqual({ notify_job_ready: false });
  });

  it('deletes the account only after confirming with the password', async () => {
    const f = fetch as ReturnType<typeof vi.fn>;
    f.mockResolvedValueOnce(ok(profile())).mockResolvedValueOnce({ ok: true, status: 204, json: async () => ({}) });
    const gone = vi.fn();
    render(Profile, { token: 't', onaccountdeleted: gone });
    await fireEvent.click(await screen.findByText('Delete my account…'));
    await fireEvent.input(screen.getByLabelText('Password to confirm'), { target: { value: 'pw-123456' } });
    await fireEvent.click(screen.getByText('Permanently delete'));
    await waitFor(() => expect(gone).toHaveBeenCalledOnce());
    expect(calls()[1][1]?.method).toBe('DELETE');
    expect(JSON.parse(calls()[1][1]?.body as string)).toEqual({ password: 'pw-123456' });
  });
});
