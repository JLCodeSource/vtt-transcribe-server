import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import FileUpload from '../components/FileUpload.svelte';

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
const profile = {
  username: 'alice', email: 'a@example.com', oauth_provider: null, retention_mode: 'default', retention_days: null,
  effective_retention: { mode: 'hold', days: 7 }, platform_retention: { mode: 'hold', days: 7 }, notify_job_ready: true,
  email_enabled: false, default_options: {}, has_openai_key: true, openai_key_hint: '****abcd',
};
const audio = (name: string, size = 3) => new File(['x'.repeat(size)], name, { type: 'audio/mpeg', lastModified: 1_000 });

describe('uploading several files at once', () => {
  let posted: string[];
  let failOn: string | null;

  beforeEach(() => {
    posted = [];
    failOn = null;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url === '/api/transcribe') {
          const name = ((init?.body as FormData).get('file') as File).name;
          if (name === failOn) return { ok: false, status: 429, statusText: 'Too Many Requests', json: async () => ({ detail: 'You have too many jobs queued.' }) };
          posted.push(name);
          return ok({ job_id: `job-${posted.length}`, status: 'pending' });
        }
        if (url === '/api/profile') return ok(profile);
        if (url === '/api/engines') return ok({ engines: [{ id: 'openai', label: 'OpenAI', requires_api_key: true, available: true }], max_speakers: 12 });
        return ok({});
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  const choose = async (container: HTMLElement, files: File[]) => {
    const input = container.querySelector('#file-input') as HTMLInputElement;
    expect(input.multiple).toBe(true);
    await fireEvent.change(input, { target: { files } });
  };

  it('lets you pick or drop more than one file and lists them all', async () => {
    const { container } = render(FileUpload, { props: { onuploadstart: () => {} } });
    await choose(container, [audio('a.mp3'), audio('b.mp3'), audio('c.wav')]);
    const list = await screen.findByRole('list', { name: 'Selected files' });
    expect(list.querySelectorAll('li')).toHaveLength(3);
    expect(screen.getByRole('button', { name: 'Add 3 files to queue' })).toBeTruthy();
  });

  it('queues every file, in order, then goes to the Jobs page once', async () => {
    const onuploadstart = vi.fn();
    const { container } = render(FileUpload, { props: { onuploadstart } });
    await choose(container, [audio('one.mp3'), audio('two.mp3'), audio('three.mp3')]);
    await fireEvent.click(await screen.findByRole('button', { name: 'Add 3 files to queue' }));
    await waitFor(() => expect(onuploadstart).toHaveBeenCalledTimes(1));
    expect(posted).toEqual(['one.mp3', 'two.mp3', 'three.mp3']);
  });

  it('keeps the valid files and reports the rejected ones', async () => {
    const { container } = render(FileUpload, { props: { onuploadstart: () => {} } });
    await choose(container, [audio('ok.mp3'), new File(['x'], 'notes.txt', { type: 'text/plain' })]);
    expect((await screen.findByRole('list', { name: 'Selected files' })).querySelectorAll('li')).toHaveLength(1);
    expect((await screen.findByRole('alert')).textContent).toContain('notes.txt');
  });

  it('adds to the list on a second pick, ignores duplicates, and lets you remove one', async () => {
    const { container } = render(FileUpload, { props: { onuploadstart: () => {} } });
    await choose(container, [audio('a.mp3')]);
    await choose(container, [audio('a.mp3'), audio('b.mp3')]);
    const list = await screen.findByRole('list', { name: 'Selected files' });
    expect(list.querySelectorAll('li')).toHaveLength(2);
    await fireEvent.click(screen.getByRole('button', { name: 'Remove a.mp3' }));
    expect(list.querySelectorAll('li')).toHaveLength(1);
    await fireEvent.click(screen.getByRole('button', { name: 'Clear all' }));
    await screen.findByText(/Drop your video or audio files here/i);
  });

  it('stops at the first refusal, says what was queued, and keeps the rest listed', async () => {
    failOn = 'two.mp3';
    const onuploadstart = vi.fn();
    const { container } = render(FileUpload, { props: { onuploadstart } });
    await choose(container, [audio('one.mp3'), audio('two.mp3'), audio('three.mp3')]);
    await fireEvent.click(await screen.findByRole('button', { name: 'Add 3 files to queue' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Queued 1 of 3');
    expect(alert.textContent).toContain('two.mp3');
    expect(posted).toEqual(['one.mp3']); // three.mp3 was not attempted after the refusal
    expect(onuploadstart).not.toHaveBeenCalled();
    const list = screen.getByRole('list', { name: 'Selected files' });
    expect([...list.querySelectorAll('h4')].map((h) => h.textContent)).toEqual(['two.mp3', 'three.mp3']);
  });
});
