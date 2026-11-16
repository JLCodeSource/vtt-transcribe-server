import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import SpeakerReview from '../components/SpeakerReview.svelte';
import type { SegmentsResponse } from '../types';

const data = (over: Partial<SegmentsResponse> = {}): SegmentsResponse => ({
  job_id: 'j1',
  translated_to: null,
  speakers: [
    { id: 'SPEAKER_00', name: null, display_name: 'Speaker 1', seconds: 12, segment_count: 2, sample_text: 'Hello there', samples: [{ start: 0, end: 5 }] },
    { id: 'SPEAKER_01', name: null, display_name: 'Speaker 2', seconds: 7, segment_count: 1, sample_text: 'Hi', samples: [{ start: 5, end: 12 }] },
  ],
  segments: [
    { index: 0, start: 0, end: 5, text: 'Hello there', translation: null, speaker_id: 'SPEAKER_00', speaker: 'Speaker 1' },
    { index: 1, start: 5, end: 12, text: 'Hi', translation: null, speaker_id: 'SPEAKER_01', speaker: 'Speaker 2' },
    { index: 2, start: 12, end: 14, text: 'Fine', translation: null, speaker_id: 'SPEAKER_00', speaker: 'Speaker 1' },
  ],
  ...over,
});

const ok = (body: unknown) => ({ ok: true, json: async () => body }) as Response;

describe('SpeakerReview', () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('lists every speaker with a quote and sample button', () => {
    render(SpeakerReview, { props: { jobId: 'j1', data: data(), onupdate: () => {}, onclose: () => {} } });
    expect(screen.getByText('“Hello there”')).toBeTruthy();
    expect(screen.getByLabelText('Play sample 1 of Speaker 2')).toBeTruthy();
    expect(screen.getByText(/Found 2 speakers/)).toBeTruthy();
  });

  it('saves names via PUT and reports the refreshed transcript', async () => {
    const next = data();
    next.speakers[0].name = 'Alice';
    fetchMock.mockResolvedValue(ok(next));
    const onupdate = vi.fn();
    render(SpeakerReview, { props: { jobId: 'j1', data: data(), onupdate, onclose: () => {} } });
    await fireEvent.input(screen.getByLabelText('Name for Speaker 1'), { target: { value: 'Alice' } });
    await fireEvent.click(screen.getByText('Save names'));
    await waitFor(() => expect(onupdate).toHaveBeenCalledWith(next));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/jobs/j1/speakers/names');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(init.body).names.SPEAKER_00).toBe('Alice');
  });

  it('merges one speaker into another', async () => {
    fetchMock.mockResolvedValue(ok(data()));
    render(SpeakerReview, { props: { jobId: 'j1', data: data(), onupdate: () => {}, onclose: () => {} } });
    await fireEvent.change(screen.getByLabelText('Merge Speaker 2 into'), { target: { value: 'SPEAKER_00' } });
    await fireEvent.click(screen.getAllByText('Merge').find((b) => !(b as HTMLButtonElement).disabled)!);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/jobs/j1/speakers/merge');
    expect(JSON.parse(init.body)).toEqual({ source_ids: ['SPEAKER_01'], target_id: 'SPEAKER_00' });
  });

  it('splits the ticked lines off a speaker', async () => {
    fetchMock.mockResolvedValue(ok(data()));
    render(SpeakerReview, { props: { jobId: 'j1', data: data(), onupdate: () => {}, onclose: () => {} } });
    await fireEvent.click(screen.getAllByText('Split…')[0]);
    await fireEvent.click(screen.getByLabelText(/Fine/));
    await fireEvent.click(screen.getByText('Split off 1 line'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/jobs/j1/speakers/SPEAKER_00/split');
    expect(JSON.parse(init.body)).toEqual({ segment_indices: [2] });
  });

  it('shows the server error instead of failing silently', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 400, json: async () => ({ detail: 'Unknown speaker: X' }) } as Response);
    render(SpeakerReview, { props: { jobId: 'j1', data: data(), onupdate: () => {}, onclose: () => {} } });
    await fireEvent.click(screen.getByText('Save names'));
    expect(await screen.findByText('Unknown speaker: X')).toBeTruthy();
  });
});
