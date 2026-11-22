<script lang="ts">
  import type { TranscriptSegment } from '../types';
  import { authHeaders } from '../api';

  interface Props {
    segments: TranscriptSegment[];
    jobId: string;
    onreset: () => void;
    /** opens the speaker review (shown only when the job has speakers) */
    onreview?: () => void;
    /** translate the transcript into the given language name; resolves when segments are refreshed */
    ontranslate?: (language: string) => Promise<void>;
    translatedTo?: string | null;
  }

  const { segments, jobId, onreset, onreview, ontranslate, translatedTo = null }: Props = $props();

  let showTranslation = $state(true);
  let targetLanguage = $state('');
  let translating = $state(false);
  let translateError = $state('');
  const hasTranslation = $derived(segments.some((seg) => seg.translation));
  const textOf = (seg: TranscriptSegment) => (hasTranslation && showTranslation && seg.translation) || seg.text;

  async function translate() {
    if (!ontranslate || !targetLanguage.trim()) return;
    translating = true;
    translateError = '';
    try {
      await ontranslate(targetLanguage.trim());
      showTranslation = true;
    } catch (error) {
      translateError = error instanceof Error ? error.message : 'Translation failed';
    } finally {
      translating = false;
    }
  }

  let searchQuery = $state('');
  let selectedSpeaker = $state('all');

  const speakers = $derived.by(() => {
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
      const response = await fetch(`/api/jobs/${jobId}/download?format=${format}${hasTranslation && showTranslation ? '&translated=true' : ''}`, {
        headers: authHeaders(),
      });
      if (!response.ok) {
        throw new Error(`Download failed: ${response.statusText}`);
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `transcript.${format}`;
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

  {#if ontranslate}
    <div class="controls translate">
      <input
        type="text"
        placeholder="Translate to… (e.g. Spanish)"
        aria-label="Translate to language"
        bind:value={targetLanguage}
        class="search-input"
      />
      <button type="button" onclick={translate} disabled={translating || !targetLanguage.trim()}>
        {translating ? 'Translating…' : 'Translate'}
      </button>
      {#if hasTranslation}
        <label class="show-translation">
          <input type="checkbox" bind:checked={showTranslation} />
          Show {translatedTo ?? 'translation'}
        </label>
      {/if}
    </div>
    {#if translateError}<p class="notice error" role="alert">{translateError}</p>{/if}
  {/if}

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
        {#each speakers as speaker}
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
        <p class="segment-text">{textOf(segment)}</p>
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

  .empty-state {
    text-align: center;
    padding: var(--space-6);
    color: var(--text-muted);
  }
</style>
