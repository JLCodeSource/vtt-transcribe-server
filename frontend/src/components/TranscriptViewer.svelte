<script lang="ts">
  import type { DiarizationVersion, TranscriptSegment, VersionsResponse } from '../types';
  import { authHeaders, exportQuery } from '../api';
  import { languageChoices, loadLanguages, TRANSLATION_LANGUAGES } from '../languages';

  interface Props {
    segments: TranscriptSegment[];
    jobId: string;
    onreset: () => void;
    /** opens the speaker review (shown only when the job has speakers) */
    onreview?: () => void;
    /** translate the transcript into the given language name; ADDS a language version and resolves when refreshed */
    ontranslate?: (language: string) => Promise<void>;
    translatedTo?: string | null;
    /** which versions exist; when given, the viewer shows the language / diarization pickers */
    versions?: VersionsResponse | null;
    /** the language shown: "original" or a stored translation */
    language?: string;
    /** the diarization version shown (an id) */
    diarization?: string | null;
    onselect?: (choice: { language?: string; diarization?: string }) => Promise<void> | void;
    /** run speaker identification again (n speakers, or null = automatic); adds a version */
    onrediarize?: (numSpeakers: number | null) => Promise<void>;
    ondelete?: (kind: 'translation' | 'diarization', id: string) => Promise<void>;
  }

  const {
    segments,
    jobId,
    onreset,
    onreview,
    ontranslate,
    translatedTo = null,
    versions = null,
    language = 'original',
    diarization = null,
    onselect,
    onrediarize,
    ondelete,
  }: Props = $props();

  let languages: string[] = $state(TRANSLATION_LANGUAGES);
  loadLanguages().then((l) => (languages = l));

  // Without version data (older callers) the viewer keeps its original behaviour: one translation, a show/hide checkbox.
  let showTranslation = $state(true);
  let sideBySide = $state(false);
  let bilingual = $state(false);
  let targetLanguage = $state('');
  let rerunCount = $state('auto');
  let translating = $state(false);
  let busy = $state(false);
  let translateError = $state('');
  const hasTranslation = $derived(segments.some((seg) => seg.translation));
  const showingTranslation = $derived(
    versions ? language !== 'original' && hasTranslation : hasTranslation && showTranslation
  );
  const textOf = (seg: TranscriptSegment) =>
    (showingTranslation && !sideBySide && seg.translation) || seg.text;

  async function guarded(action: () => Promise<void> | void) {
    busy = true;
    translateError = '';
    try {
      await action();
    } catch (error) {
      translateError = error instanceof Error ? error.message : 'Something went wrong';
    } finally {
      busy = false;
    }
  }

  async function translate() {
    if (!ontranslate || !targetLanguage.trim()) return;
    translating = true;
    translateError = '';
    try {
      await ontranslate(targetLanguage.trim());
      showTranslation = true;
      targetLanguage = '';
    } catch (error) {
      translateError = error instanceof Error ? error.message : 'Translation failed';
    } finally {
      translating = false;
    }
  }

  const pickLanguage = (event: Event) =>
    guarded(() => onselect?.({ language: (event.target as HTMLSelectElement).value }));
  const pickDiarization = (event: Event) =>
    guarded(() => onselect?.({ diarization: (event.target as HTMLSelectElement).value }));
  const rerun = () =>
    guarded(() => onrediarize?.(rerunCount === 'auto' ? null : Number(rerunCount)));

  function remove(kind: 'translation' | 'diarization', id: string, label: string) {
    const what =
      kind === 'translation' ? `the ${label} translation` : `the speaker version “${label}”`;
    if (!confirm(`Delete ${what}? The original text and the other versions are kept.`)) return;
    void guarded(() => ondelete?.(kind, id));
  }

  const when = (iso?: string) => (iso ? new Date(iso).toLocaleString() : '');
  // "2 speakers · 11/22/2026, 9:30 AM": the count is only added when the name does not already say it
  function versionLabel(d: DiarizationVersion): string {
    const count = /speaker|found/i.test(d.label)
      ? ''
      : ` · ${d.speakers} ${d.speakers === 1 ? 'speaker' : 'speakers'}`;
    return `${d.label}${count}${d.created_at ? ` · ${when(d.created_at)}` : ''}`;
  }
  const activeVersion = $derived(versions?.diarizations.find((d) => d.id === diarization));

  let searchQuery = $state('');
  let selectedSpeaker = $state('all');

  const speakers = $derived.by(() => {
    // eslint-disable-next-line svelte/prefer-svelte-reactivity -- local accumulator inside $derived.by, not state
    const speakerSet = new Set<string>();
    segments.forEach((seg) => {
      if (seg.speaker) {
        speakerSet.add(seg.speaker);
      }
    });
    return Array.from(speakerSet).sort();
  });

  const filteredSegments = $derived.by(() => {
    return segments.filter((seg) => {
      const matchesSearch =
        !searchQuery || seg.text.toLowerCase().includes(searchQuery.toLowerCase());
      const matchesSpeaker = selectedSpeaker === 'all' || seg.speaker === selectedSpeaker;
      return matchesSearch && matchesSpeaker;
    });
  });

  function formatTime(seconds: number): string {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }

  function formatTimeRange(start: number, end: number): string {
    return `${formatTime(start)} - ${formatTime(end)}`;
  }

  async function downloadTranscript(format: 'txt' | 'vtt' | 'srt' | 'json') {
    try {
      // With versions the choice is explicit (language, diarization, bilingual); otherwise the old "translated" flag.
      const query = versions
        ? exportQuery(format, {
            language,
            diarization,
            bilingual: bilingual && language !== 'original',
          })
        : `format=${format}${hasTranslation && showTranslation ? '&translated=true' : ''}`;
      const response = await fetch(`/api/jobs/${jobId}/download?${query}`, {
        headers: authHeaders(),
      });
      if (!response.ok) {
        throw new Error(`Download failed: ${response.statusText}`);
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const suffix = versions && language !== 'original' ? `.${language.toLowerCase()}` : '';
      a.download = `transcript${suffix}.${format}`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (error) {
      alert(`Download failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  }
</script>

<div class="transcript-container">
  <div class="transcript-header">
    <h2>📄 Transcript</h2>
    <div class="header-actions">
      <button type="button" class="sm" onclick={() => downloadTranscript('txt')}>
        Download TXT
      </button>
      <button type="button" class="sm" onclick={() => downloadTranscript('vtt')}>
        Download VTT
      </button>
      <button type="button" class="sm" onclick={() => downloadTranscript('srt')}>
        Download SRT
      </button>
      <button type="button" class="sm" onclick={() => downloadTranscript('json')}>
        Download JSON
      </button>
      {#if onreview && speakers.length > 0}
        <button type="button" class="sm" onclick={onreview}>Review speakers</button>
      {/if}
      <button type="button" class="sm primary" onclick={onreset}>New Transcription</button>
    </div>
  </div>

  {#if versions}
    <section class="versions" aria-label="Versions">
      <div class="version-row">
        <label class="field">
          <span>Language</span>
          <select aria-label="Language" value={language} onchange={pickLanguage} disabled={busy}>
            <option value="original">
              Original{versions.original.language ? ` (${versions.original.language})` : ''}
            </option>
            {#each versions.translations as t (t.language)}
              <option value={t.language}>{t.language}</option>
            {/each}
          </select>
        </label>
        {#if language !== 'original'}
          <button
            type="button"
            class="sm danger"
            onclick={() => remove('translation', language, language)}
            disabled={busy}
          >
            Delete this translation
          </button>
          <label class="check">
            <input type="checkbox" bind:checked={sideBySide} /> <span>Side by side</span>
          </label>
        {/if}
      </div>
      <p class="help">
        {versions.translations.length === 0
          ? 'Only the original text so far. Add a language below; the original is always kept.'
          : `${versions.translations.length} translation${versions.translations.length === 1 ? '' : 's'} stored: ${versions.translations.map((t) => t.language).join(', ')}.`}
      </p>

      <div class="version-row">
        <label class="field">
          <span>Speakers</span>
          {#if versions.diarizations.length > 0}
            <select
              aria-label="Speaker version"
              value={diarization ?? ''}
              onchange={pickDiarization}
              disabled={busy}
            >
              {#each versions.diarizations as d (d.id)}
                <option value={d.id}>{versionLabel(d)}</option>
              {/each}
            </select>
          {:else}
            <span class="help">No speaker identification yet.</span>
          {/if}
        </label>
        {#if versions.diarizations.length > 1 && activeVersion}
          <button
            type="button"
            class="sm danger"
            onclick={() => remove('diarization', activeVersion.id, activeVersion.label)}
            disabled={busy}
          >
            Delete this version
          </button>
        {/if}
      </div>

      {#if onrediarize}
        <div class="version-row">
          <label class="field">
            <span>Re-run speaker identification</span>
            <select
              aria-label="Number of speakers for the re-run"
              bind:value={rerunCount}
              disabled={busy}
            >
              <option value="auto">Auto-detect</option>
              {#each [1, 2, 3, 4, 5, 6, 7, 8] as n (n)}<option value={String(n)}>
                  {n}
                  {n === 1 ? 'speaker' : 'speakers'}
                </option>{/each}
            </select>
          </label>
          <button type="button" onclick={rerun} disabled={busy}>
            {busy ? 'Working…' : 'Re-run'}
          </button>
          <span class="help">Adds a version; the existing ones are kept.</span>
        </div>
      {/if}

      {#if language !== 'original'}
        <label class="check">
          <input type="checkbox" bind:checked={bilingual} />
          <span>Downloads contain both the original and {language}</span>
        </label>
      {/if}
    </section>
  {/if}

  {#if ontranslate}
    <div class="controls translate">
      <select aria-label="Translate to language" bind:value={targetLanguage} class="search-input">
        <option value="">{versions ? 'Add language…' : 'Translate to…'}</option>
        {#each languageChoices(languages, targetLanguage) as l (l)}<option value={l}>
            {l}
          </option>{/each}
      </select>
      <button type="button" onclick={translate} disabled={translating || !targetLanguage.trim()}>
        {translating ? 'Translating…' : versions ? 'Add translation' : 'Translate'}
      </button>
      {#if hasTranslation && !versions}
        <label class="show-translation">
          <input type="checkbox" bind:checked={showTranslation} />
          Show {translatedTo ?? 'translation'}
        </label>
      {/if}
    </div>
  {/if}
  {#if translateError}<p class="notice error" role="alert">{translateError}</p>{/if}

  <div class="controls">
    <input
      type="text"
      placeholder="Search transcript..."
      bind:value={searchQuery}
      class="search-input"
    />

    {#if speakers.length > 0}
      <select bind:value={selectedSpeaker} class="speaker-filter">
        <option value="all">All Speakers</option>
        {#each speakers as speaker (speaker)}
          <option value={speaker}>{speaker}</option>
        {/each}
      </select>
    {/if}
  </div>

  <div class="segment-count">
    Showing {filteredSegments.length} of {segments.length} segments
  </div>

  <div class="segments">
    {#each filteredSegments as segment (segment.start + '-' + segment.end)}
      <div class="segment" class:has-speaker={segment.speaker}>
        <div class="segment-header">
          <span class="timestamp">{formatTimeRange(segment.start, segment.end)}</span>
          {#if segment.speaker}
            <span class="speaker-badge">{segment.speaker}</span>
          {/if}
        </div>
        {#if sideBySide && showingTranslation}
          <div class="side-by-side">
            <p class="segment-text">{segment.text}</p>
            <p class="segment-text translated">{segment.translation ?? ''}</p>
          </div>
        {:else}
          <p class="segment-text">{textOf(segment)}</p>
        {/if}
      </div>
    {/each}
  </div>

  {#if filteredSegments.length === 0}
    <div class="empty-state">
      <p>No segments match your search criteria.</p>
    </div>
  {/if}
</div>

<style>
  .transcript-container {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }

  .transcript-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--space-3);
  }

  .transcript-header h2 {
    font-size: var(--text-xl);
  }

  .header-actions {
    display: flex;
    gap: var(--space-2);
    flex-wrap: wrap;
  }

  .controls {
    display: flex;
    gap: var(--space-3);
    flex-wrap: wrap;
    align-items: center;
  }

  .search-input {
    flex: 1;
    min-width: 12rem;
    width: auto;
  }

  .speaker-filter {
    min-width: 9rem;
    width: auto;
  }

  .show-translation {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-size: var(--text-sm);
  }

  .segment-count {
    font-size: var(--text-sm);
    color: var(--text-muted);
  }

  .segments {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    max-height: 600px;
    overflow-y: auto;
    padding: var(--space-3);
    background: var(--surface-2);
    border-radius: var(--radius);
  }

  .segment {
    padding: var(--space-3) var(--space-4);
    background: var(--surface);
    border-radius: var(--radius);
    border: 1px solid var(--border);
  }

  .segment.has-speaker {
    border-left: 4px solid var(--accent);
  }

  .segment-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: var(--space-1);
    gap: var(--space-3);
  }

  .timestamp {
    font-family: var(--font-mono);
    font-size: var(--text-sm);
    color: var(--text-muted);
  }

  .speaker-badge {
    padding: 0.1rem 0.6rem;
    background: var(--accent-soft);
    color: var(--accent-text);
    border-radius: var(--radius-pill);
    font-size: var(--text-xs);
    font-weight: 650;
  }

  .segment-text {
    line-height: 1.6;
  }

  .side-by-side {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--space-4);
  }

  .translated {
    color: var(--accent-text);
  }

  .versions {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-4);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface-2);
  }

  .version-row {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    gap: var(--space-3);
  }

  .version-row .field {
    min-width: 12rem;
  }

  @media (max-width: 640px) {
    .side-by-side {
      grid-template-columns: 1fr;
    }
  }

  .empty-state {
    text-align: center;
    padding: var(--space-6);
    color: var(--text-muted);
  }
</style>
