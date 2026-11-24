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
    /** empty state: take the user to the upload page */
    onhome?: () => void;
  }

  const { onopen, focusJobId = null, pollMs = 2000, onhome }: Props = $props();

  let rawJobs: JobSummary[] = $state([]);
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

  // Running first, then the queue in the order it will run, then finished jobs, newest first.
  const rank = (j: JobSummary) => (j.status === 'processing' ? 0 : j.status === 'pending' ? 1 : 2);
  const finishedAt = (j: JobSummary) => Date.parse(j.completed_at ?? j.created_at ?? '') || 0;
  const jobs = $derived(
    [...rawJobs].sort((a, b) => {
      if (rank(a) !== rank(b)) return rank(a) - rank(b);
      if (rank(a) === 1) return (a.queue_position ?? 1e9) - (b.queue_position ?? 1e9);
      return finishedAt(b) - finishedAt(a);
    })
  );

  const active = (j: JobSummary) => j.status === 'pending' || j.status === 'processing';
  const percent = (j: JobSummary) => Math.round((j.progress ?? 0) * 100);

  function stepLabel(j: JobSummary): string {
    if (j.status === 'pending')
      return j.queue_position
        ? `Waiting — position ${j.queue_position} in the queue`
        : 'Waiting in the queue';
    return STEP_LABELS[j.step ?? ''] ?? j.step ?? j.status;
  }

  /** The progress message, unless it only repeats the step label ("Detecting language — Detecting language"). */
  function detail(j: JobSummary): string {
    const message = (j.message ?? '').trim();
    if (j.status !== 'processing' || !message) return '';
    return message.toLowerCase() === stepLabel(j).toLowerCase() ? '' : message;
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
      rawJobs = list.jobs;
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
    await act(() =>
      setJobRetention(j.job_id, editMode, editMode === 'hold' ? Number(editDays) : null)
    );
    editing = null;
  }

  onMount(refresh);
  onDestroy(() => {
    stopped = true;
    clearTimeout(timer);
  });
</script>

<div class="jobs">
  {#if error}<p class="notice error" role="alert">{error}</p>{/if}

  {#if !loaded}
    <p class="muted">Loading…</p>
  {:else if jobs.length === 0}
    <div class="empty">
      <h3>Nothing here yet</h3>
      <p>No jobs yet. Add a file on the Home page and it will show up here.</p>
      {#if onhome}<button type="button" class="primary" onclick={onhome}>Add files</button>{/if}
    </div>
  {:else}
    <p class="muted summary">
      {queueLength} waiting in the queue. Jobs run one at a time, in the order they were added.
    </p>
    <ul class="job-list">
      {#each jobs as j (j.job_id)}
        <li class="job" data-testid="job-row" data-status={j.status}>
          <div class="job-head">
            <strong class="name">{j.filename}</strong>
            <span class="chip {j.status}">{j.status === 'pending' ? 'queued' : j.status}</span>
          </div>

          {#if active(j)}
            <div class="step">{stepLabel(j)}{detail(j) ? ` — ${detail(j)}` : ''}</div>
            {#if j.status === 'processing'}
              <div class="bar-row">
                <div
                  class="progress"
                  role="progressbar"
                  aria-valuemin="0"
                  aria-valuemax="100"
                  aria-valuenow={percent(j)}
                  aria-label="Progress"
                >
                  <span style="width: {percent(j)}%"></span>
                </div>
                <span class="pct">{percent(j)}%</span>
              </div>
            {/if}
          {:else if j.status === 'failed'}
            <div class="step failed">{j.error ?? 'The job failed'}</div>
          {/if}

          {#if expiry(j)}<div class="meta">{expiry(j)}</div>{/if}

          {#if editing === j.job_id}
            <div class="edit">
              <select bind:value={editMode} aria-label="Retention policy">
                <option value="hold">Keep for days</option>
                <option value="keep">Keep until I delete it</option>
                <option value="delete">Delete after download</option>
              </select>
              {#if editMode === 'hold'}
                <input
                  type="number"
                  min="1"
                  max="3650"
                  bind:value={editDays}
                  aria-label="Days to keep"
                />
              {/if}
              <button type="button" class="primary sm" onclick={() => saveEdit(j)}>Save</button>
              <button type="button" class="ghost sm" onclick={() => (editing = null)}>
                Cancel
              </button>
            </div>
          {/if}

          <div class="actions">
            {#if j.status === 'completed'}
              <button type="button" class="primary sm" onclick={() => onopen(j)}>Open</button>
              <span class="btn-group" role="group" aria-label="Download">
                <a class="btn sm" href={downloadUrl(j.job_id, 'txt')} download>TXT</a>
                <a class="btn sm" href={downloadUrl(j.job_id, 'srt')} download>SRT</a>
                <a class="btn sm" href={downloadUrl(j.job_id, 'vtt')} download>VTT</a>
                <a class="btn sm" href={downloadUrl(j.job_id, 'json')} download>JSON</a>
              </span>
            {/if}
            <span class="spacer"></span>
            {#if active(j)}
              <button type="button" class="sm" onclick={() => cancel(j)}>Cancel</button>
            {:else}
              <button type="button" class="sm" onclick={() => startEdit(j)}>Retention</button>
              <button type="button" class="sm danger" onclick={() => remove(j)}>Delete</button>
            {/if}
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .jobs {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }

  .summary {
    font-size: var(--text-sm);
  }

  .job-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }

  .job {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
    padding: var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }

  .job[data-status='processing'] {
    border-color: var(--accent);
  }

  .job-head {
    display: flex;
    justify-content: space-between;
    gap: var(--space-3);
    align-items: center;
  }

  .name {
    overflow-wrap: anywhere;
  }

  .step {
    font-size: var(--text-sm);
  }

  .step.failed {
    color: var(--danger);
  }

  .bar-row {
    display: flex;
    align-items: center;
    gap: var(--space-3);
  }

  .bar-row .progress {
    flex: 1;
  }

  .pct {
    font-size: var(--text-sm);
    font-variant-numeric: tabular-nums;
    min-width: 2.75rem;
    text-align: right;
  }

  .meta {
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .actions,
  .edit {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
    align-items: center;
    margin-top: var(--space-1);
  }

  .edit select,
  .edit input {
    width: auto;
    min-height: 2rem;
  }

  .spacer {
    flex: 1;
  }
</style>
