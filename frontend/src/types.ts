export interface TranscriptionJob {
  job_id: string;
  filename: string;
  status: JobStatus;
  progress?: number;
  detected_language?: string;
  translated_to?: string;
}

export type JobStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled';
export type RetentionMode = 'keep' | 'delete' | 'hold';

/** One row of GET /api/jobs (the Jobs page). */
export interface JobSummary {
  job_id: string;
  filename: string;
  status: JobStatus;
  /** queued, detecting, transcribing, diarizing, translating, done, failed, cancelled */
  step: string | null;
  /** overall 0..1 */
  progress: number | null;
  message?: string | null;
  error?: string | null;
  /** 1 = next to run; null unless waiting */
  queue_position: number | null;
  retention_mode?: RetentionMode | null;
  retention_days?: number | null;
  expires_at: string | null;
  created_at?: string;
  completed_at?: string | null;
  downloaded_at?: string | null;
  engine?: string;
  user_id?: number;
}

export interface JobList {
  jobs: JobSummary[];
  queue_length: number;
  running: string | null;
}

export interface Profile {
  username: string;
  email: string;
  oauth_provider: string | null;
  /** keep | delete | hold | default (platform default) */
  retention_mode: RetentionMode | 'default';
  retention_days: number | null;
  effective_retention: { mode: RetentionMode; days: number | null };
  platform_retention: { mode: RetentionMode; days: number | null };
  notify_job_ready: boolean;
  email_enabled: boolean;
  default_options: { engine?: 'openai' | 'local'; diarize?: boolean; translate_to?: string };
  has_openai_key: boolean;
  /** ****abcd; the key itself is never returned */
  openai_key_hint: string | null;
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
  /** keep | delete | hold; '' = your profile default */
  retentionMode?: RetentionMode | '';
  retentionDays?: string;
}

export interface WebSocketMessage {
  job_id: string;
  status: JobStatus;
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
