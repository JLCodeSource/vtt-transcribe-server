import type { EnginesResponse, SegmentsResponse } from './types';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

const job = (id: string) => `/api/jobs/${encodeURIComponent(id)}`;

export const fetchEngines = () => request<EnginesResponse>('/api/engines');

export const fetchSegments = (id: string) => request<SegmentsResponse>(`${job(id)}/segments`);

/** Rename several speakers at once: {SPEAKER_00: "Alice"}. A blank name restores "Speaker N". */
export const renameSpeakers = (id: string, names: Record<string, string>) =>
  request<SegmentsResponse>(`${job(id)}/speakers/names`, json('PUT', { names }));

export const mergeSpeakers = (id: string, sourceIds: string[], targetId: string) =>
  request<SegmentsResponse>(`${job(id)}/speakers/merge`, json('POST', { source_ids: sourceIds, target_id: targetId }));

export const splitSpeaker = (id: string, speakerId: string, segmentIndices: number[]) =>
  request<SegmentsResponse & { new_speaker_id: string }>(
    `${job(id)}/speakers/${encodeURIComponent(speakerId)}/split`,
    json('POST', { segment_indices: segmentIndices })
  );

export const rediarize = (id: string, numSpeakers: number | null) =>
  request<SegmentsResponse>(`${job(id)}/rediarize`, json('POST', { num_speakers: numSpeakers }));

export const translateJob = (id: string, targetLanguage: string, apiKey: string) =>
  request<SegmentsResponse>(`${job(id)}/translate`, json('POST', { target_language: targetLanguage, api_key: apiKey }));

/** Playable URL for a short sample of a speaker (an <audio src>). */
export const clipUrl = (id: string, speakerId: string, start?: number, end?: number) => {
  const q = start !== undefined && end !== undefined ? `?start=${start}&end=${end}` : '';
  return `${job(id)}/speakers/${encodeURIComponent(speakerId)}/clip${q}`;
};
