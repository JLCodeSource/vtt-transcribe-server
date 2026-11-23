import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import FileUpload from '../components/FileUpload.svelte';

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
const profile = {
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
};
const audio = (name: string, size = 3) =>
  new File(['x'.repeat(size)], name, { type: 'audio/mpeg', lastModified: 1_000 });

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
          if (name === failOn)
            return {
              ok: false,
              status: 429,
              statusText: 'Too Many Requests',
              json: async () => ({ detail: 'You have too many jobs queued.' }),
            };
          posted.push(name);
          return ok({ job_id: `job-${posted.length}`, status: 'pending' });
        }
        if (url === '/api/profile') return ok(profile);
        if (url === '/api/engines')
          return ok({
            engines: [{ id: 'openai', label: 'OpenAI', requires_api_key: true, available: true }],
            max_speakers: 12,
          });
        return ok({});
      })
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

  it('lets the rejected-files notice be dismissed', async () => {
    const { container } = render(FileUpload, { props: { onuploadstart: () => {} } });
    await choose(container, [
      audio('ok.mp3'),
      new File(['x'], 'notes.txt', { type: 'text/plain' }),
    ]);
    await screen.findByRole('alert');
    await fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByRole('alert')).toBeNull();
    expect(
      screen.getByRole('list', { name: 'Selected files' }).querySelectorAll('li')
    ).toHaveLength(1);
  });

  it('keeps the valid files and reports the rejected ones', async () => {
    const { container } = render(FileUpload, { props: { onuploadstart: () => {} } });
    await choose(container, [
      audio('ok.mp3'),
      new File(['x'], 'notes.txt', { type: 'text/plain' }),
    ]);
    expect(
      (await screen.findByRole('list', { name: 'Selected files' })).querySelectorAll('li')
    ).toHaveLength(1);
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
    expect([...list.querySelectorAll('h4')].map((h) => h.textContent)).toEqual([
      'two.mp3',
      'three.mp3',
    ]);
  });
});

describe('a saved default engine this server does not have', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('falls back to an engine that works', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === '/api/profile') return ok({ ...profile, default_options: { engine: 'local' } });
        if (url === '/api/engines') {
          return ok({
            engines: [
              { id: 'openai', label: 'OpenAI', requires_api_key: true, available: true },
              { id: 'local', label: 'Local', requires_api_key: false, available: false },
            ],
            max_speakers: 12,
          });
        }
        return ok({});
      })
    );
    const { container } = render(FileUpload, { props: { onuploadstart: () => {} } });
    const input = container.querySelector('#file-input') as HTMLInputElement;
    Object.defineProperty(input, 'files', { value: [audio('a.mp3')], configurable: true });
    await fireEvent.change(input);
    const select = (await screen.findByLabelText('Engine')) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe('openai'));
  });
});

describe('the upload size limit', () => {
  afterEach(() => vi.unstubAllGlobals());

  const stub = (maxBytes?: number) =>
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === '/api/profile') return ok(profile);
        if (url === '/api/engines') {
          return ok({
            engines: [{ id: 'openai', label: 'OpenAI', requires_api_key: true, available: true }],
            max_speakers: 12,
            max_upload_bytes: maxBytes,
          });
        }
        return ok({});
      })
    );
  const pick = async (container: HTMLElement, file: File) => {
    const input = container.querySelector('#file-input') as HTMLInputElement;
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    await fireEvent.change(input);
  };
  const big = (name: string, bytes: number) => {
    const f = new File(['x'], name, { type: 'audio/mpeg', lastModified: 1 });
    Object.defineProperty(f, 'size', { value: bytes });
    return f;
  };

  it('accepts a multi-gigabyte video by default', async () => {
    stub();
    const { container } = render(FileUpload, { props: { onuploadstart: () => {} } });
    await pick(container, big('film.mp4', 3 * 1024 ** 3));
    expect(await screen.findByRole('list', { name: 'Selected files' })).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('uses the limit the server reports and says what it is', async () => {
    stub(50 * 1024 * 1024);
    const { container } = render(FileUpload, { props: { onuploadstart: () => {} } });
    await waitFor(() =>
      expect(
        (fetch as ReturnType<typeof vi.fn>).mock.calls.some((c) => c[0] === '/api/engines')
      ).toBe(true)
    );
    await new Promise((r) => setTimeout(r, 20));
    await pick(container, big('long.mp4', 80 * 1024 * 1024));
    expect((await screen.findByRole('alert')).textContent).toContain('Maximum: 50 MB');
  });
});
