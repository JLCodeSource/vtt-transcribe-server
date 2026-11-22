import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import TranscriptViewer from '../components/TranscriptViewer.svelte';
import type { TranscriptSegment, VersionsResponse } from '../types';

const segments: TranscriptSegment[] = [
  { start: 0, end: 5, text: 'Hello there', speaker: 'Speaker 1', translation: 'Hola' },
  { start: 5, end: 9, text: 'Hi', speaker: 'Speaker 2', translation: 'Hola, que tal' },
];

const versions = (over: Partial<VersionsResponse> = {}): VersionsResponse => ({
  job_id: 'j1',
  original: { language: 'en', segments: 2 },
  translations: [
    { language: 'Spanish', created_at: '2026-01-01T10:00:00Z' },
    { language: 'French', created_at: '2026-01-02T10:00:00Z' },
  ],
  diarizations: [
    { id: 'd1', label: 'Auto (3 found)', num_speakers: null, speakers: 3, active: false, created_at: '2026-01-01T09:00:00Z' },
    { id: 'd2', label: '2 speakers', num_speakers: 2, speakers: 2, active: true, created_at: '2026-01-01T11:00:00Z' },
  ],
  active_diarization: 'd2',
  ...over,
});

const mount = (props: Record<string, unknown> = {}) =>
  render(TranscriptViewer, {
    props: {
      segments,
      jobId: 'j1',
      onreset: () => {},
      versions: versions(),
      language: 'Spanish',
      diarization: 'd2',
      ...props,
    },
  });

describe('versions in the transcript viewer', () => {
  beforeEach(() => vi.stubGlobal('confirm', vi.fn(() => true)));
  afterEach(() => vi.unstubAllGlobals());

  it('lists the original and every stored translation in the language switcher', () => {
    mount();
    const select = screen.getByLabelText('Language') as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent?.trim())).toEqual(['Original (en)', 'Spanish', 'French']);
    expect(select.value).toBe('Spanish');
    expect(screen.getByText(/2 translations stored: Spanish, French/)).toBeTruthy();
  });

  it('switching language asks the app for that version', async () => {
    const onselect = vi.fn();
    mount({ onselect });
    await fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'French' } });
    expect(onselect).toHaveBeenCalledWith({ language: 'French' });
  });

  it('shows the translation, or the original, or both side by side', async () => {
    const { container, rerender } = mount();
    expect(screen.getByText('Hola')).toBeTruthy();
    expect(screen.queryByText('Hello there')).toBeNull();
    await fireEvent.click(screen.getByLabelText('Side by side'));
    expect(screen.getByText('Hello there')).toBeTruthy();
    expect(screen.getByText('Hola')).toBeTruthy();
    expect(container.querySelectorAll('.side-by-side')).toHaveLength(2);
    await rerender({ language: 'original', segments: segments.map((s) => ({ ...s, translation: undefined })) });
    expect(screen.queryByLabelText('Side by side')).toBeNull();
    expect(screen.getByText('Hello there')).toBeTruthy();
  });

  it('lists the diarization versions with their size and date, and switches between them', async () => {
    const onselect = vi.fn();
    mount({ onselect });
    const select = screen.getByLabelText('Speaker version') as HTMLSelectElement;
    expect(select.value).toBe('d2');
    expect([...select.options].map((o) => o.textContent)).toEqual([
      expect.stringContaining('Auto (3 found) · '),
      expect.stringContaining('2 speakers · '),
    ]);
    await fireEvent.change(select, { target: { value: 'd1' } });
    expect(onselect).toHaveBeenCalledWith({ diarization: 'd1' });
  });

  it('shows requested versus found as the server words it, without adding a count', () => {
    mount({
      versions: versions({
        diarizations: [
          { id: 'd1', label: '3 requested, 2 found', num_speakers: 3, speakers: 2, active: true, created_at: '2026-01-01T09:00:00Z' },
        ],
      }),
      diarization: 'd1',
    });
    const text = (screen.getByLabelText('Speaker version') as HTMLSelectElement).options[0].textContent ?? '';
    expect(text).toContain('3 requested, 2 found');
    expect(text).not.toContain('2 speakers');
  });

  it('re-runs with a chosen number of speakers, which adds a version', async () => {
    const onrediarize = vi.fn(async () => {});
    mount({ onrediarize });
    await fireEvent.change(screen.getByLabelText('Number of speakers for the re-run'), { target: { value: '3' } });
    await fireEvent.click(screen.getByText('Re-run'));
    expect(onrediarize).toHaveBeenCalledWith(3);
    await fireEvent.change(screen.getByLabelText('Number of speakers for the re-run'), { target: { value: 'auto' } });
    await fireEvent.click(screen.getByText('Re-run'));
    expect(onrediarize).toHaveBeenLastCalledWith(null);
    expect(screen.getByText(/Adds a version; the existing ones are kept/)).toBeTruthy();
  });

  it('asks before deleting a translation or a speaker version, and keeps the original', async () => {
    const ondelete = vi.fn(async () => {});
    mount({ ondelete });
    await fireEvent.click(screen.getByText('Delete this translation'));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('The original text and the other versions are kept'));
    expect(ondelete).toHaveBeenCalledWith('translation', 'Spanish');
    await fireEvent.click(screen.getByText('Delete this version'));
    expect(ondelete).toHaveBeenLastCalledWith('diarization', 'd2');
  });

  it('does not delete when the confirmation is declined', async () => {
    vi.stubGlobal('confirm', vi.fn(() => false));
    const ondelete = vi.fn();
    mount({ ondelete });
    await fireEvent.click(screen.getByText('Delete this translation'));
    expect(ondelete).not.toHaveBeenCalled();
  });

  it('offers no delete for the original and none for the only speaker version', () => {
    mount({ language: 'original', versions: versions({ diarizations: [versions().diarizations[1]] }) });
    expect(screen.queryByText('Delete this translation')).toBeNull();
    expect(screen.queryByText('Delete this version')).toBeNull();
  });

  it('a job without speaker identification says so and can still run it', () => {
    mount({ versions: versions({ diarizations: [], active_diarization: null }), diarization: null, onrediarize: async () => {} });
    expect(screen.getByText('No speaker identification yet.')).toBeTruthy();
    expect(screen.getByText('Re-run')).toBeTruthy();
  });

  describe('exports pick the language and diarization explicitly', () => {
    let urls: string[];
    beforeEach(() => {
      urls = [];
      vi.stubGlobal('fetch', vi.fn(async (url: string) => {
          if (url.includes('/download')) urls.push(url);
          return url.includes('/download') ? { ok: true, blob: async () => new Blob(['x']) } : { ok: true, json: async () => ({}) };
        }));
      vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:x', revokeObjectURL: () => {} }));
    });

    it('sends language and diarization', async () => {
      mount();
      await fireEvent.click(screen.getByText('Download SRT'));
      await waitFor(() => expect(urls).toHaveLength(1));
      const q = new URLSearchParams(urls[0].split('?')[1]);
      expect([q.get('format'), q.get('language'), q.get('diarization'), q.get('bilingual')]).toEqual(['srt', 'Spanish', 'd2', null]);
    });

    it('can export both languages together', async () => {
      mount();
      await fireEvent.click(screen.getByLabelText(/Downloads contain both the original and Spanish/));
      await fireEvent.click(screen.getByText('Download TXT'));
      await waitFor(() => expect(urls).toHaveLength(1));
      expect(new URLSearchParams(urls[0].split('?')[1]).get('bilingual')).toBe('true');
    });

    it('exports the original when that is what is shown', async () => {
      mount({ language: 'original' });
      await fireEvent.click(screen.getByText('Download VTT'));
      await waitFor(() => expect(urls).toHaveLength(1));
      expect(new URLSearchParams(urls[0].split('?')[1]).get('language')).toBe('original');
    });
  });

  it('adding a language goes through the translate callback and keeps the others listed', async () => {
    const ontranslate = vi.fn(async () => {});
    mount({ ontranslate });
    await fireEvent.change(screen.getByLabelText('Translate to language'), { target: { value: 'German' } });
    await fireEvent.click(screen.getByText('Add translation'));
    expect(ontranslate).toHaveBeenCalledWith('German');
  });
});
