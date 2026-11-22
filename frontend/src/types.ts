export interface TranscriptionJob {
  job_id: string;
  filename: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  progress?: number;
  detected_language?: string;
  translated_to?: string;
}

export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
  speaker?: string;
  translation?: string;
}

export interface UploadOptions {
  diarization: boolean;
  /** `openai` (needs apiKey) or `local` (key-free; available once core ships it) */
  engine?: 'openai' | 'local';
  /** known speaker count; undefined/'auto' = detect */
  numSpeakers?: string;
  language?: string;
  model?: string;
  apiKey?: string;
  translateTo?: string;
}

export interface WebSocketMessage {
  job_id: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  filename: string;
  result?: string;
  error?: string;
  progress?: number;
  detected_language?: string;
  translated_to?: string;
}

/** One speaker as returned by GET /api/jobs/{id}/speakers. */
export interface SpeakerInfo {
  id: string;
  name: string | null;
  display_name: string;
  seconds: number;
  segment_count: number;
  sample_text: string;
  samples: Array<{ start: number; end: number }>;
}

/** One segment as returned by GET /api/jobs/{id}/segments. */
export interface SegmentView {
  index: number;
  start: number;
  end: number;
  text: string;
  translation: string | null;
  speaker_id: string | null;
  /** display name ("Alice", or "Speaker 1" until renamed) */
  speaker: string | null;
}

export interface SegmentsResponse {
  job_id: string;
  translated_to: string | null;
  /** the engine the job ran with; local jobs translate on the server and need no API key */
  engine?: string;
  speakers: SpeakerInfo[];
  segments: SegmentView[];
}

export interface EnginesResponse {
  engines: Array<{ id: string; label: string; requires_api_key: boolean; available: boolean }>;
  diarizers: string[];
  max_speakers: number;
}

export interface Me {
  id: number;
  username: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  must_change_password: boolean;
}

export interface AdminUser extends Me {
  oauth_provider: string | null;
  created_at: string;
  job_count: number;
}

export interface AdminUserList {
  total: number;
  users: AdminUser[];
}
