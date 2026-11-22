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
      <button type="button" class="secondary" onclick={() => downloadTranscript('txt')}>
        Download TXT
      </button>
      <button type="button" class="secondary" onclick={() => downloadTranscript('vtt')}>
        Download VTT
      </button>
      <button type="button" class="secondary" onclick={() => downloadTranscript('srt')}>
        Download SRT
      </button>
      <button type="button" class="secondary" onclick={() => downloadTranscript('json')}>
        Download JSON
      </button>
      {#if onreview && speakers.length > 0}
        <button type="button" class="secondary" onclick={onreview}>Review speakers</button>
      {/if}
      <button type="button" class="secondary" onclick={onreset}>New Transcription</button>
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
      <button type="button" class="secondary" onclick={translate} disabled={translating || !targetLanguage.trim()}>
        {translating ? 'Translating…' : 'Translate'}
      </button>
      {#if hasTranslation}
        <label class="show-translation">
          <input type="checkbox" bind:checked={showTranslation} />
          Show {translatedTo ?? 'translation'}
        </label>
      {/if}
    </div>
    {#if translateError}<p class="error-message" role="alert">{translateError}</p>{/if}
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
    gap: 1.5rem;
  }

  .transcript-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 1rem;
  }

  .transcript-header h2 {
    margin: 0;
    font-size: 1.75rem;
  }

  .header-actions {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
  }

  .header-actions button {
    padding: 0.5rem 1rem;
    font-size: 0.875rem;
  }

  .controls {
    display: flex;
    gap: 1rem;
    flex-wrap: wrap;
  }

  .search-input {
    flex: 1;
    min-width: 200px;
  }

  .speaker-filter {
    min-width: 150px;
  }

  .segment-count {
    font-size: 0.875rem;
    color: var(--text-light);
  }

  .segments {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    max-height: 600px;
    overflow-y: auto;
    padding: 1rem;
    background: var(--background);
    border-radius: 12px;
  }

  .segment {
    padding: 1rem;
    background: white;
    border-radius: 8px;
    border: 2px solid var(--border-color);
    transition: all 0.2s;
  }

  .segment:hover {
    border-color: var(--primary-color);
    transform: translateX(4px);
  }

  .segment.has-speaker {
    border-left: 4px solid var(--primary-color);
  }

  .segment-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.5rem;
    gap: 1rem;
  }

  .timestamp {
    font-family: 'Courier New', monospace;
    font-size: 0.875rem;
    color: var(--text-light);
    font-weight: 600;
  }

  .speaker-badge {
    padding: 0.25rem 0.75rem;
    background: linear-gradient(135deg, var(--primary-color), var(--secondary-color));
    color: white;
    border-radius: 12px;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
  }

  .segment-text {
    margin: 0;
    line-height: 1.6;
    color: var(--text-color);
  }

  .empty-state {
    text-align: center;
    padding: 3rem;
    color: var(--text-light);
  }

  .empty-state p {
    margin: 0;
    font-size: 1.125rem;
  }
</style>
