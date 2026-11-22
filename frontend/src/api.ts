import type { AdminUser, AdminUserList, EnginesResponse, Me, SegmentsResponse } from './types';

/** The signed-in user's bearer token. The server requires it on every /api call. */
let authToken: string | null = null;
export const setAuthToken = (token: string | null) => {
  authToken = token;
};
export const authHeaders = (): Record<string, string> => (authToken ? { Authorization: `Bearer ${authToken}` } : {});
/** For URLs that cannot carry headers (an <audio src>, a WebSocket): the token goes in the query string instead. */
export const withToken = (url: string) =>
  authToken ? `${url}${url.includes('?') ? '&' : '?'}access_token=${encodeURIComponent(authToken)}` : url;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, headers: { ...authHeaders(), ...((init?.headers as Record<string, string>) ?? {}) } });
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

export const translateJob = (id: string, targetLanguage: string, apiKey: string | null) =>
  request<SegmentsResponse>(`${job(id)}/translate`, json('POST', { target_language: targetLanguage, api_key: apiKey }));

/** Playable URL for a short sample of a speaker (an <audio src>). */
export const clipUrl = (id: string, speakerId: string, start?: number, end?: number) => {
  const q = start !== undefined && end !== undefined ? `?start=${start}&end=${end}` : '';
  return withToken(`${job(id)}/speakers/${encodeURIComponent(speakerId)}/clip${q}`);
};

// ---- account + admin (explicit token, so they work before setAuthToken has run) ----
const authed = (token: string, init: RequestInit = {}): RequestInit => ({
  ...init,
  headers: { ...(init.headers ?? {}), Authorization: `Bearer ${token}` },
});

async function requestNoBody(url: string, init: RequestInit): Promise<void> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed: ${response.status}`);
  }
}

export const fetchMe = (token: string) => request<Me>('/auth/me', authed(token));

export const changePassword = (token: string, currentPassword: string, newPassword: string) =>
  requestNoBody('/auth/change-password', authed(token, json('POST', { current_password: currentPassword, new_password: newPassword })));

const adminUser = (id: number) => `/admin/users/${id}`;

export const adminListUsers = (token: string, q = '', limit = 50, offset = 0) => {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (q.trim()) params.set('q', q.trim());
  return request<AdminUserList>(`/admin/users?${params}`, authed(token));
};
export const adminSetActive = (token: string, id: number, active: boolean) =>
  request<AdminUser>(`${adminUser(id)}/${active ? 'activate' : 'deactivate'}`, authed(token, { method: 'POST' }));
export const adminSetAdmin = (token: string, id: number, admin: boolean) =>
  request<AdminUser>(`${adminUser(id)}/${admin ? 'make-admin' : 'remove-admin'}`, authed(token, { method: 'POST' }));
export const adminResetPassword = (token: string, id: number) =>
  request<{ user_id: number; temporary_password: string }>(`${adminUser(id)}/reset-password`, authed(token, { method: 'POST' }));
export const adminDeleteUser = (token: string, id: number) =>
  requestNoBody(adminUser(id), authed(token, { method: 'DELETE' }));
