<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { cancelJob, deleteJob, downloadUrl, fetchJobs, setJobRetention } from '../api';
  import type { JobSummary, RetentionMode } from '../types';

  interface Props {
    /** open a finished job for review / download */
    onopen: (job: JobSummary) => void;
    /** a job to open as soon as it is known and finished (deep link from the notification email) */
    focusJobId?: string | null;
    /** poll interval while something is queued or running, ms */
    pollMs?: number;
  }

  const { onopen, focusJobId = null, pollMs = 2000 }: Props = $props();

  let jobs: JobSummary[] = $state([]);
  let queueLength = $state(0);
  let loaded = $state(false);
  let error = $state('');
  let editing: string | null = $state(null);
  let editMode: RetentionMode = $state('hold');
  let editDays = $state('7');
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;
  let opened = false;

  const STEP_LABELS: Record<string, string> = {
    queued: 'Queued',
    starting: 'Starting',
    detecting: 'Detecting language',
    transcribing: 'Transcribing',
    diarizing: 'Identifying speakers',
    translating: 'Translating',
    done: 'Done',
    failed: 'Failed',
    cancelled: 'Cancelled',
  };

  const active = (j: JobSummary) => j.status === 'pending' || j.status === 'processing';
  const percent = (j: JobSummary) => Math.round((j.progress ?? 0) * 100);

  function stepLabel(j: JobSummary): string {
    if (j.status === 'pending') return j.queue_position ? `Waiting — position ${j.queue_position} in the queue` : 'Waiting in the queue';
    return STEP_LABELS[j.step ?? ''] ?? j.step ?? j.status;
  }

  function expiry(j: JobSummary): string {
    if (!active(j) && j.retention_mode === 'keep') return 'Kept until you delete it';
    if (j.expires_at) return `Expires ${new Date(j.expires_at).toLocaleString()}`;
    if (j.retention_mode === 'delete') return 'Deleted after you download it';
    if (j.retention_mode === 'hold') return `Kept ${j.retention_days ?? ''} days after it finishes`;
    return '';
  }

  async function refresh() {
    try {
      const list = await fetchJobs();
      jobs = list.jobs;
      queueLength = list.queue_length;
      error = '';
      loaded = true;
      if (focusJobId && !opened) {
        const target = jobs.find((j) => j.job_id === focusJobId);
        if (target && target.status === 'completed') {
          opened = true;
          onopen(target);
        }
      }
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : 'Could not load jobs';
    }
    if (!stopped) timer = setTimeout(refresh, jobs.some(active) ? pollMs : pollMs * 5);
  }

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      error = '';
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : 'Action failed';
    }
    clearTimeout(timer);
    await refresh();
  }

  const cancel = (j: JobSummary) => act(() => cancelJob(j.job_id));
  const remove = (j: JobSummary) => {
    if (!confirm(`Delete ${j.filename} and its results?`)) return Promise.resolve();
    return act(() => deleteJob(j.job_id));
  };

  function startEdit(j: JobSummary) {
    editing = j.job_id;
    editMode = (j.retention_mode ?? 'hold') as RetentionMode;
    editDays = String(j.retention_days ?? 7);
  }

  async function saveEdit(j: JobSummary) {
    await act(() => setJobRetention(j.job_id, editMode, editMode === 'hold' ? Number(editDays) : null));
    editing = null;
  }

  onMount(refresh);
  onDestroy(() => {
    stopped = true;
    clearTimeout(timer);
  });
</script>

<div class="jobs">
  {#if error}<p class="error" role="alert">{error}</p>{/if}

  {#if !loaded}
    <p class="muted">Loading…</p>
  {:else if jobs.length === 0}
    <p class="muted">No jobs yet. Add a file on the Home page and it will show up here.</p>
  {:else}
    <p class="muted summary">{queueLength} waiting in the queue. Jobs run one at a time, in the order they were added.</p>
    <ul class="job-list">
      {#each jobs as j (j.job_id)}
        <li class="job" data-testid="job-row" data-status={j.status}>
          <div class="job-head">
            <strong class="name">{j.filename}</strong>
            <span class="badge {j.status}">{j.status === 'pending' ? 'queued' : j.status}</span>
          </div>

          {#if active(j)}
            <div class="step">{stepLabel(j)}{j.message && j.status === 'processing' ? ` — ${j.message}` : ''}</div>
            {#if j.status === 'processing'}
              <div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={percent(j)} aria-label="Progress">
                <div class="fill" style="width: {percent(j)}%"></div>
              </div>
              <div class="pct">{percent(j)}%</div>
            {/if}
          {:else if j.status === 'failed'}
            <div class="step failed">{j.error ?? 'The job failed'}</div>
          {/if}

          <div class="meta">{expiry(j)}</div>

          {#if editing === j.job_id}
            <div class="edit">
              <select bind:value={editMode} aria-label="Retention policy">
                <option value="hold">Keep for days</option>
                <option value="keep">Keep until I delete it</option>
                <option value="delete">Delete after download</option>
              </select>
              {#if editMode === 'hold'}
                <input type="number" min="1" max="3650" bind:value={editDays} aria-label="Days to keep" />
              {/if}
              <button type="button" onclick={() => saveEdit(j)}>Save</button>
              <button type="button" class="link" onclick={() => (editing = null)}>Cancel</button>
            </div>
          {/if}

          <div class="actions">
            {#if j.status === 'completed'}
              <button type="button" class="primary" onclick={() => onopen(j)}>Open</button>
              <a href={downloadUrl(j.job_id, 'txt')} download>TXT</a>
              <a href={downloadUrl(j.job_id, 'srt')} download>SRT</a>
              <a href={downloadUrl(j.job_id, 'vtt')} download>VTT</a>
              <a href={downloadUrl(j.job_id, 'json')} download>JSON</a>
            {/if}
            {#if active(j)}
              <button type="button" onclick={() => cancel(j)}>Cancel</button>
            {:else}
              <button type="button" class="link" onclick={() => startEdit(j)}>Retention</button>
              <button type="button" class="danger" onclick={() => remove(j)}>Delete</button>
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .jobs { display: flex; flex-direction: column; gap: 1rem; }
  .job-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.75rem; }
  .job { border: 1px solid #e5e7eb; border-radius: 10px; padding: 1rem; display: flex; flex-direction: column; gap: 0.5rem; }
  .job-head { display: flex; justify-content: space-between; gap: 1rem; align-items: center; }
  .name { overflow-wrap: anywhere; }
  .badge { font-size: 0.75rem; padding: 0.15rem 0.6rem; border-radius: 999px; background: #e5e7eb; color: #374151; text-transform: capitalize; }
  .badge.processing { background: #dbeafe; color: #1e40af; }
  .badge.completed { background: #d1fae5; color: #065f46; }
  .badge.failed { background: #fee2e2; color: #991b1b; }
  .bar { height: 10px; background: #e5e7eb; border-radius: 999px; overflow: hidden; }
  .fill { height: 100%; background: linear-gradient(90deg, #667eea, #764ba2); transition: width 0.4s; }
  .pct, .meta, .muted { color: #6b7280; font-size: 0.875rem; }
  .step { color: #374151; }
  .step.failed, .error { color: #b91c1c; }
  .actions, .edit { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
  .actions a { font-size: 0.875rem; }
  .link { background: none; border: none; color: #4f46e5; cursor: pointer; padding: 0; text-decoration: underline; }
  .danger { color: #b91c1c; }
</style>
