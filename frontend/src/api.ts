import type {
  AdminUser,
  AdminUserList,
  EnginesResponse,
  JobList,
  JobSummary,
  Me,
  Profile,
  RetentionMode,
  SegmentsResponse,
  VersionsResponse,
} from './types';

/** The signed-in user's bearer token. The server requires it on every /api call. */
let authToken: string | null = null;
export const setAuthToken = (token: string | null) => {
  authToken = token;
};
export const authHeaders = (): Record<string, string> =>
  authToken ? { Authorization: `Bearer ${authToken}` } : {};
/** For URLs that cannot carry headers (an <audio src>, a WebSocket): the token goes in the query string instead. */
export const withToken = (url: string) =>
  authToken
    ? `${url}${url.includes('?') ? '&' : '?'}access_token=${encodeURIComponent(authToken)}`
    : url;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { ...authHeaders(), ...((init?.headers as Record<string, string>) ?? {}) },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(
      typeof body.detail === 'string' ? body.detail : `Request failed: ${response.status}`
    );
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

/** Segments for one (language, diarization) pair; omitted language = the latest translation, omitted diarization = the active one. */
export const fetchSegments = (id: string, language?: string, diarization?: string | null) => {
  const q = new URLSearchParams();
  if (language) q.set('language', language);
  if (diarization) q.set('diarization', diarization);
  const qs = q.toString();
  return request<SegmentsResponse>(`${job(id)}/segments${qs ? `?${qs}` : ''}`);
};

export const fetchVersions = (id: string) => request<VersionsResponse>(`${job(id)}/versions`);

export const setActiveDiarization = (id: string, diarizationId: string) =>
  request<SegmentsResponse>(`${job(id)}/diarizations/active`, json('PUT', { id: diarizationId }));

export const deleteDiarization = (id: string, diarizationId: string) =>
  request<VersionsResponse>(`${job(id)}/diarizations/${encodeURIComponent(diarizationId)}`, {
    method: 'DELETE',
  });

export const deleteTranslation = (id: string, language: string) =>
  request<VersionsResponse>(`${job(id)}/translations/${encodeURIComponent(language)}`, {
    method: 'DELETE',
  });

/** Rename several speakers at once: {SPEAKER_00: "Alice"}. A blank name restores "Speaker N". */
export const renameSpeakers = (id: string, names: Record<string, string>) =>
  request<SegmentsResponse>(`${job(id)}/speakers/names`, json('PUT', { names }));

export const mergeSpeakers = (id: string, sourceIds: string[], targetId: string) =>
  request<SegmentsResponse>(
    `${job(id)}/speakers/merge`,
    json('POST', { source_ids: sourceIds, target_id: targetId })
  );

export const splitSpeaker = (id: string, speakerId: string, segmentIndices: number[]) =>
  request<SegmentsResponse & { new_speaker_id: string }>(
    `${job(id)}/speakers/${encodeURIComponent(speakerId)}/split`,
    json('POST', { segment_indices: segmentIndices })
  );

/** Adds a diarization version (the others are kept) and makes it active. */
export const rediarize = (id: string, numSpeakers: number | null) =>
  request<SegmentsResponse & { new_diarization: string }>(
    `${job(id)}/rediarize`,
    json('POST', { num_speakers: numSpeakers })
  );

/** Adds a language version (the original and the other languages are kept). */
export const translateJob = (id: string, targetLanguage: string, apiKey: string | null) =>
  request<SegmentsResponse & { existing?: boolean }>(
    `${job(id)}/translate`,
    json('POST', { target_language: targetLanguage, api_key: apiKey })
  );

/** Playable URL for a short sample of a speaker (an <audio src>). */
export const clipUrl = (id: string, speakerId: string, start?: number, end?: number) => {
  const q = start !== undefined && end !== undefined ? `?start=${start}&end=${end}` : '';
  return withToken(`${job(id)}/speakers/${encodeURIComponent(speakerId)}/clip${q}`);
};

// ---- jobs queue ----
export const fetchJobs = () => request<JobList>('/api/jobs');
export const cancelJob = (id: string) =>
  request<JobSummary>(`${job(id)}/cancel`, { method: 'POST' });
export const setJobRetention = (id: string, mode: RetentionMode, days: number | null) =>
  request<JobSummary>(`${job(id)}/retention`, json('PUT', { mode, days }));
export async function deleteJob(id: string): Promise<void> {
  await requestNoBody(job(id), { method: 'DELETE', headers: authHeaders() });
}
/** Download URL (token in the query string so a plain link works). */
export type ExportChoice = { language?: string; diarization?: string | null; bilingual?: boolean };

/** Query string for an export of one chosen (language, diarization) combination. */
export const exportQuery = (format: 'txt' | 'vtt' | 'srt' | 'json', choice: ExportChoice = {}) => {
  const q = new URLSearchParams({ format });
  if (choice.language) q.set('language', choice.language);
  if (choice.diarization) q.set('diarization', choice.diarization);
  if (choice.bilingual) q.set('bilingual', 'true');
  return q.toString();
};

export const downloadUrl = (
  id: string,
  format: 'txt' | 'vtt' | 'srt' | 'json',
  choice: ExportChoice = {}
) => withToken(`${job(id)}/download?${exportQuery(format, choice)}`);

// ---- profile ----
export const fetchProfile = () => request<Profile>('/api/profile');
export const updateProfile = (patch: Record<string, unknown>) =>
  request<Profile>('/api/profile', json('PATCH', patch));
export const setOpenAIKey = (apiKey: string) =>
  request<Profile>('/api/profile/openai-key', json('PUT', { api_key: apiKey }));
export const removeOpenAIKey = () =>
  request<Profile>('/api/profile/openai-key', { method: 'DELETE' });
export async function deleteAccount(password: string | null): Promise<void> {
  await requestNoBody('/api/profile', {
    ...json('DELETE', { password }),
    headers: { ...authHeaders(), 'Content-Type': 'application/json' },
  });
}

// ---- account + admin (explicit token, so they work before setAuthToken has run) ----
const authed = (token: string, init: RequestInit = {}): RequestInit => ({
  ...init,
  headers: { ...(init.headers ?? {}), Authorization: `Bearer ${token}` },
});

async function requestNoBody(url: string, init: RequestInit): Promise<void> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(
      typeof body.detail === 'string' ? body.detail : `Request failed: ${response.status}`
    );
  }
}

export const fetchMe = (token: string) => request<Me>('/auth/me', authed(token));

export const changePassword = (token: string, currentPassword: string, newPassword: string) =>
  requestNoBody(
    '/auth/change-password',
    authed(token, json('POST', { current_password: currentPassword, new_password: newPassword }))
  );

const adminUser = (id: number) => `/admin/users/${id}`;

export const adminListUsers = (token: string, q = '', limit = 50, offset = 0) => {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (q.trim()) params.set('q', q.trim());
  return request<AdminUserList>(`/admin/users?${params}`, authed(token));
};
export const adminSetActive = (token: string, id: number, active: boolean) =>
  request<AdminUser>(
    `${adminUser(id)}/${active ? 'activate' : 'deactivate'}`,
    authed(token, { method: 'POST' })
  );
export const adminSetAdmin = (token: string, id: number, admin: boolean) =>
  request<AdminUser>(
    `${adminUser(id)}/${admin ? 'make-admin' : 'remove-admin'}`,
    authed(token, { method: 'POST' })
  );
export const adminResetPassword = (token: string, id: number) =>
  request<{ user_id: number; temporary_password: string }>(
    `${adminUser(id)}/reset-password`,
    authed(token, { method: 'POST' })
  );
export const adminDeleteUser = (token: string, id: number) =>
  requestNoBody(adminUser(id), authed(token, { method: 'DELETE' }));
