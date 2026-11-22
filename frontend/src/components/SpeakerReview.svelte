<script lang="ts">
  import { clipUrl, mergeSpeakers, rediarize, renameSpeakers, splitSpeaker } from '../api';
  import type { SegmentsResponse } from '../types';

  interface Props {
    jobId: string;
    data: SegmentsResponse;
    /** called with the refreshed transcript after every change */
    onupdate: (data: SegmentsResponse) => void;
    onclose: () => void;
  }

  const { jobId, data, onupdate, onclose }: Props = $props();

  // svelte-ignore state_referenced_locally
  let names = $state<Record<string, string>>(Object.fromEntries(data.speakers.map((s) => [s.id, s.name ?? ''])));
  let mergeInto = $state<Record<string, string>>({});
  let splitting = $state<string | null>(null);
  let picked = $state<number[]>([]);
  let note = $state('');
  let busy = $state(false);
  let speakerCount = $state('');
  let playing: HTMLAudioElement | null = null;

  const mm = (s: number) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

  function play(speakerId: string, start?: number, end?: number) {
    playing?.pause();
    playing = new Audio(clipUrl(jobId, speakerId, start, end));
    void playing.play().catch(() => (note = 'Could not play the sample.'));
  }

  async function run(action: () => Promise<SegmentsResponse>, message: string) {
    busy = true;
    note = '';
    try {
      const next = await action();
      names = Object.fromEntries(next.speakers.map((s) => [s.id, s.name ?? '']));
      onupdate(next);
      note = message;
    } catch (error) {
      note = error instanceof Error ? error.message : 'Something went wrong.';
    } finally {
      busy = false;
    }
  }

  const save = () => run(() => renameSpeakers(jobId, names), 'Names saved. Downloads now read “Name: text”.');

  function merge(sourceId: string) {
    const target = mergeInto[sourceId];
    if (!target) return;
    mergeInto[sourceId] = '';
    void run(() => mergeSpeakers(jobId, [sourceId], target), 'Merged.');
  }

  function startSplit(speakerId: string) {
    splitting = splitting === speakerId ? null : speakerId;
    picked = [];
  }

  function toggle(index: number) {
    picked = picked.includes(index) ? picked.filter((i) => i !== index) : [...picked, index];
  }

  async function confirmSplit(speakerId: string) {
    const indices = picked;
    splitting = null;
    picked = [];
    await run(() => splitSpeaker(jobId, speakerId, indices), 'Split. Listen to both and merge back if it was wrong.');
  }

  const redo = () =>
    run(() => rediarize(jobId, speakerCount ? Number(speakerCount) : null), 'Added a new speaker version with the re-run. The other versions are kept; switch between them in the transcript view.');
</script>

<section class="review" aria-label="Review speakers">
  <h2>Who is who?</h2>
  <p class="hint">
    Found {data.speakers.length}
    {data.speakers.length === 1 ? 'speaker' : 'speakers'}. Listen to a sample, name each speaker, merge voices that are
    the same person, or split off lines that belong to someone else. These edits change only the speaker version you are looking at.
  </p>
  {#if note}<p class="note" role="status">{note}</p>{/if}

  <ul>
    {#each data.speakers as sp (sp.id)}
      <li data-speaker={sp.id}>
        <div class="row">
          <input
            type="text"
            aria-label="Name for {sp.display_name}"
            placeholder={sp.display_name}
            maxlength="100"
            bind:value={names[sp.id]}
            disabled={busy}
          />
          <span class="meta">{mm(sp.seconds)} speaking · {sp.segment_count} segments</span>
        </div>
        {#if sp.sample_text}<p class="quote">“{sp.sample_text}”</p>{/if}
        <div class="row">
          {#each sp.samples as s, i (i)}
            <button type="button" class="sm" onclick={() => play(sp.id, s.start, s.end)} aria-label="Play sample {i + 1} of {sp.display_name}">
              ▶ {mm(s.start)}
            </button>
          {/each}
          <button type="button" class="sm" onclick={() => startSplit(sp.id)} disabled={busy || sp.segment_count < 2}>
            {splitting === sp.id ? 'Cancel split' : 'Split…'}
          </button>
          {#if data.speakers.length > 1}
            <select aria-label="Merge {sp.display_name} into" bind:value={mergeInto[sp.id]} disabled={busy}>
              <option value="">Merge into…</option>
              {#each data.speakers.filter((o) => o.id !== sp.id) as o (o.id)}
                <option value={o.id}>{o.display_name}</option>
              {/each}
            </select>
            <button type="button" class="sm" disabled={busy || !mergeInto[sp.id]} onclick={() => merge(sp.id)}>Merge</button>
          {/if}
        </div>
        {#if splitting === sp.id}
          <div class="split">
            <p class="hint">Tick the lines that belong to a different person.</p>
            {#each data.segments.filter((g) => g.speaker_id === sp.id) as g (g.index)}
              <label class="line">
                <input type="checkbox" checked={picked.includes(g.index)} onchange={() => toggle(g.index)} />
                <span class="meta">{mm(g.start)}</span> {g.text}
              </label>
            {/each}
            <button type="button" class="primary" disabled={busy || picked.length === 0} onclick={() => confirmSplit(sp.id)}>
              Split off {picked.length} {picked.length === 1 ? 'line' : 'lines'}
            </button>
          </div>
        {/if}
      </li>
    {/each}
  </ul>

  <div class="row redo">
    <label>
      Wrong number of speakers? Re-run (adds a version)
      <select bind:value={speakerCount} aria-label="Number of speakers" disabled={busy}>
        <option value="">Auto-detect</option>
        {#each [1, 2, 3, 4, 5, 6, 7, 8] as n (n)}<option value={String(n)}>{n}</option>{/each}
      </select>
    </label>
    <button type="button" onclick={redo} disabled={busy}>Re-run detection</button>
  </div>

  <div class="row">
    <button type="button" class="primary" onclick={save} disabled={busy}>Save names</button>
    <button type="button" onclick={onclose}>Done</button>
  </div>
</section>

<style>
  .review {
    display: grid;
    gap: var(--space-3);
  }

  h2 {
    font-size: var(--text-xl);
  }

  .hint {
    color: var(--text-muted);
    font-size: var(--text-sm);
  }

  .note {
    color: var(--success);
    font-size: var(--text-sm);
    font-weight: 600;
  }

  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: var(--space-3);
  }

  li {
    padding: var(--space-4);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
    display: grid;
    gap: var(--space-3);
  }

  .row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
    align-items: center;
  }

  .row input[type='text'] {
    flex: 1 1 12rem;
    width: auto;
  }

  .row select {
    width: auto;
    min-height: 1.875rem;
    padding-block: 0.25rem;
    font-size: var(--text-sm);
  }

  .meta {
    color: var(--text-muted);
    font-size: var(--text-sm);
  }

  .quote {
    font-style: italic;
    color: var(--text-muted);
  }

  .split {
    display: grid;
    gap: var(--space-2);
    max-height: 14rem;
    overflow-y: auto;
    padding: var(--space-3);
    background: var(--surface-2);
    border-radius: var(--radius);
  }

  .line {
    display: flex;
    gap: var(--space-2);
    align-items: baseline;
    font-size: var(--text-sm);
  }
</style>
