<script lang="ts">
  import { onMount } from 'svelte';
  import { authHeaders, fetchEngines, fetchProfile } from '../api';
  import { languageChoices, loadLanguages, TRANSLATION_LANGUAGES } from '../languages';
  import type { UploadOptions, TranscriptionJob, EnginesResponse } from '../types';

  interface Props {
    onuploadstart: (event: CustomEvent<TranscriptionJob>) => void;
  }

  const { onuploadstart }: Props = $props();

  let languages: string[] = $state(TRANSLATION_LANGUAGES);
  loadLanguages().then((l) => (languages = l));

  let isDragging = $state(false);
  // Several files can be chosen or dropped at once; each becomes its own job and waits its turn in the queue.
  let selectedFiles: File[] = $state([]);
  const options: UploadOptions = $state({
    diarization: true,
    engine: 'openai',
    numSpeakers: 'auto',
    translateTo: '',
    language: 'auto',
    model: 'whisper-1',
    apiKey: '',
    retentionMode: '',
    retentionDays: '7',
  });
  // A key saved in the profile means the field is optional (the server falls back to it).
  let profileHasKey = $state(false);
  let engines: EnginesResponse['engines'] = $state([
    { id: 'openai', label: 'OpenAI Whisper (API)', requires_api_key: true, available: true },
    { id: 'local', label: 'Local Whisper (no key)', requires_api_key: false, available: false },
  ]);
  // A saved default (or the initial choice) can name an engine this server does not have: use one that works instead.
  $effect(() => {
    const chosen = engines.find((e) => e.id === options.engine);
    if (chosen && !chosen.available) {
      const fallback = engines.find((e) => e.available);
      if (fallback) options.engine = fallback.id as UploadOptions['engine'];
    }
  });
  const needsKey = $derived((engines.find((e) => e.id === options.engine)?.requires_api_key ?? true) && !profileHasKey);
  let isUploading = $state(false);

  onMount(() => {
    fetchProfile()
      .then((p) => {
        profileHasKey = p.has_openai_key;
        if (p.default_options.engine) options.engine = p.default_options.engine;
        if (p.default_options.diarize !== undefined) options.diarization = p.default_options.diarize;
        if (p.default_options.num_speakers) options.numSpeakers = String(p.default_options.num_speakers);
        if (p.default_options.translate_to) options.translateTo = p.default_options.translate_to;
      })
      .catch(() => {
        /* the profile is an optional convenience */
      });
    fetchEngines()
      .then((r) => (engines = r.engines))
      .catch(() => {
        /* keep the defaults: the server is the source of truth only when reachable */
      });
  });
  let validationError = $state('');

  // Load API key from sessionStorage on mount
  $effect(() => {
    if (typeof window !== 'undefined') {
      const savedApiKey = sessionStorage.getItem('openai_api_key');
      
      if (savedApiKey) {
        options.apiKey = savedApiKey;
      }
    }
  });

  const SUPPORTED_VIDEO_TYPES = ['.mp4', '.avi', '.mov', '.webm', '.mpeg'];
  const SUPPORTED_AUDIO_TYPES = ['.mp3', '.wav', '.ogg', '.m4a', '.mpga'];
  const MAX_FILE_SIZE = 100 * 1024 * 1024; // 100MB

  function validateFile(file: File): string | null {
    const ext = '.' + file.name.split('.').pop()?.toLowerCase();
    const allSupported = [...SUPPORTED_VIDEO_TYPES, ...SUPPORTED_AUDIO_TYPES];
    
    if (!allSupported.includes(ext)) {
      return `Unsupported file type: ${ext}. Supported types: ${allSupported.join(', ')}`;
    }
    
    if (file.size > MAX_FILE_SIZE) {
      return `File too large: ${(file.size / 1024 / 1024).toFixed(2)}MB. Maximum: 100MB`;
    }
    
    return null;
  }

  function handleDragOver(event: DragEvent) {
    event.preventDefault();
    isDragging = true;
  }

  function handleDragLeave() {
    isDragging = false;
  }

  const sameFile = (a: File, b: File) => a.name === b.name && a.size === b.size && a.lastModified === b.lastModified;

  /** Add valid files to the list (duplicates ignored) and report the ones that were rejected. */
  function addFiles(incoming: FileList | File[] | null | undefined) {
    if (!incoming || incoming.length === 0) return;
    const next = [...selectedFiles];
    const problems: string[] = [];
    for (const file of Array.from(incoming)) {
      const error = validateFile(file);
      if (error) {
        problems.push(`${file.name}: ${error}`);
      } else if (!next.some((f) => sameFile(f, file))) {
        next.push(file);
      }
    }
    selectedFiles = next;
    validationError = problems.join('\n');
  }

  function handleDrop(event: DragEvent) {
    event.preventDefault();
    isDragging = false;
    addFiles(event.dataTransfer?.files);
  }

  function handleFileSelect(event: Event) {
    const target = event.target as HTMLInputElement;
    addFiles(target.files);
    target.value = ''; // so choosing the same file again still fires a change
  }

  function removeFile(file: File) {
    selectedFiles = selectedFiles.filter((f) => f !== file);
    validationError = '';
  }

  /** Send one file as its own job; resolves with the queued job. */
  async function submitFile(file: File): Promise<TranscriptionJob> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('engine', options.engine ?? 'openai');
    if (options.apiKey) {
      formData.append('api_key', options.apiKey);
      // kept for this tab only, so Translate / re-run can reuse it; never persisted
      sessionStorage.setItem('openai_api_key', options.apiKey);
    }
    formData.append('diarize', String(options.diarization));
    if (options.diarization && options.numSpeakers && options.numSpeakers !== 'auto') {
      formData.append('num_speakers', options.numSpeakers);
    }
    if (options.retentionMode) {
      formData.append('retention_mode', options.retentionMode);
      if (options.retentionMode === 'hold') formData.append('retention_days', options.retentionDays || '7');
    }
    if (options.translateTo?.trim()) {
      formData.append('translate_to', options.translateTo.trim());
    }
    if (options.language && options.language !== 'auto') {
      formData.append('language', options.language);
    }

    const response = await fetch('/api/transcribe', {
      method: 'POST',
      headers: authHeaders(),
      body: formData,
    });

    if (!response.ok) {
      const errorData = (await response.json().catch(() => ({ detail: response.statusText }))) as { detail?: string };
      throw new Error(errorData.detail || `Upload failed: ${response.statusText}`);
    }
    return (await response.json()) as TranscriptionJob;
  }

  async function handleUpload() {
    if (selectedFiles.length === 0) return;

    // Validate required fields
    if (needsKey && !options.apiKey) {
      alert('OpenAI API key is required');
      return;
    }

    isUploading = true;
    validationError = '';
    const queued: TranscriptionJob[] = [];
    let failure = '';

    // One after another, in the order listed, so they queue in that order. Stop at the first refusal (for example the
    // per-user queue limit) and keep the rest listed so nothing is lost.
    for (const file of [...selectedFiles]) {
      try {
        queued.push(await submitFile(file));
        selectedFiles = selectedFiles.filter((f) => f !== file);
      } catch (error) {
        failure = `${file.name}: ${error instanceof Error ? error.message : 'Unknown error'}`;
        break;
      }
    }
    isUploading = false;

    if (failure) {
      validationError =
        `Queued ${queued.length} of ${queued.length + selectedFiles.length}. Could not queue ${failure}` +
        (selectedFiles.length > 1 ? `\nThe ${selectedFiles.length} files that were not queued are still listed.` : '');
    } else if (queued.length > 0) {
      // The app moves on to the Jobs page, where the jobs wait their turn in the queue
      onuploadstart(new CustomEvent<TranscriptionJob>('uploadstart', { detail: queued[0] }));
    }
  }

  const AUDIO_EXT = ['.mp3', '.wav', '.ogg', '.m4a', '.mpga'];
  const kindIcon = (file: File) => (AUDIO_EXT.some((e) => file.name.toLowerCase().endsWith(e)) ? '🎧' : '🎬');
  const sizeLabel = (bytes: number) =>
    bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

  function clearFiles() {
    selectedFiles = [];
    validationError = '';
  }
</script>

<div class="upload-container">
  <div
    class="dropzone"
    class:dragging={isDragging}
    class:has-file={selectedFiles.length > 0}
    ondragover={handleDragOver}
    ondragleave={handleDragLeave}
    ondrop={handleDrop}
    role="region"
    aria-label="File upload dropzone"
  >
    {#if selectedFiles.length === 0}
      <div class="dropzone-content">
        <svg class="upload-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="1.8"
            d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
          />
        </svg>
        <h3>Drop your video or audio files here</h3>
        <p class="muted">or</p>
        <button type="button" class="primary lg" onclick={() => document.getElementById('file-input')?.click()}>
          Choose Files
        </button>
        <p class="help">Supports MP4, AVI, MOV, MP3, WAV, and more. Pick as many files as you like; each is queued separately.</p>
      </div>
    {:else}
      <ul class="file-list" aria-label="Selected files">
        {#each selectedFiles as file (file.name + file.size + file.lastModified)}
          <li class="file-selected">
            <span class="file-icon" aria-hidden="true">{kindIcon(file)}</span>
            <div class="file-info">
              <h4>{file.name}</h4>
              <span class="help">{sizeLabel(file.size)}</span>
            </div>
            <button
              type="button"
              class="icon"
              onclick={() => removeFile(file)}
              disabled={isUploading}
              aria-label={`Remove ${file.name}`}
              title="Remove"
            >
              ✕
            </button>
          </li>
        {/each}
      </ul>
      <div class="file-actions">
        <button type="button" class="sm" onclick={() => document.getElementById('file-input')?.click()} disabled={isUploading}>
          Add more files
        </button>
        <button type="button" class="sm ghost" onclick={clearFiles} disabled={isUploading}>Clear all</button>
      </div>
    {/if}
    <input id="file-input" type="file" multiple accept="video/*,audio/*" onchange={handleFileSelect} hidden />
  </div>

  {#if validationError}
    <div class="notice error" role="alert">
      <span class="message">{validationError}</span>
      <button type="button" class="icon" aria-label="Dismiss" onclick={() => (validationError = '')}>✕</button>
    </div>
  {/if}

  {#if selectedFiles.length > 0}
    <div class="options">
      <h3>Transcription Options</h3>

      <div class="form-grid">
        <label class="field">
          <span>Engine</span>
          <select bind:value={options.engine} disabled={isUploading} aria-label="Engine">
            {#each engines as e (e.id)}
              <option value={e.id} disabled={!e.available}>{e.label}{e.available ? '' : ' — not installed'}</option>
            {/each}
          </select>
        </label>

        {#if needsKey}
          <label class="field">
            <span>OpenAI API Key <span class="required">*</span></span>
            <input type="password" bind:value={options.apiKey} placeholder="sk-..." disabled={isUploading} required />
            <span class="help">Required for transcription with the OpenAI engine. Save one on the Profile page to skip this.</span>
          </label>
        {/if}

        <label class="field">
          <span>Language</span>
          <select bind:value={options.language} disabled={isUploading}>
            <option value="auto">Auto-detect</option>
            <option value="en">English</option>
            <option value="es">Spanish</option>
            <option value="fr">French</option>
            <option value="de">German</option>
            <option value="it">Italian</option>
            <option value="pt">Portuguese</option>
            <option value="nl">Dutch</option>
            <option value="pl">Polish</option>
            <option value="ru">Russian</option>
            <option value="ja">Japanese</option>
            <option value="zh">Chinese</option>
          </select>
        </label>

        <label class="field">
          <span>Model</span>
          <select bind:value={options.model} disabled={isUploading}>
            <option value="whisper-1">Whisper-1 (Default)</option>
          </select>
        </label>

        <label class="field">
          <span>Translate to (optional)</span>
          <select bind:value={options.translateTo} disabled={isUploading} aria-label="Translate to">
            <option value="">No translation</option>
            {#each languageChoices(languages, options.translateTo ?? '') as l (l)}<option value={l}>{l}</option>{/each}
          </select>
          <span class="help">Needs an OpenAI API key (unless you use the local engine)</span>
        </label>

        <label class="field">
          <span>Keep the result</span>
          <select bind:value={options.retentionMode} disabled={isUploading} aria-label="Retention">
            <option value="">Use my default</option>
            <option value="hold">Keep for a number of days</option>
            <option value="keep">Keep until I delete it</option>
            <option value="delete">Delete after I download it</option>
          </select>
        </label>

        {#if options.retentionMode === 'hold'}
          <label class="field">
            <span>Days to keep</span>
            <input type="number" min="1" max="3650" bind:value={options.retentionDays} disabled={isUploading} aria-label="Days to keep" />
          </label>
        {/if}
      </div>

      <div class="diarize">
        <label class="check">
          <input type="checkbox" bind:checked={options.diarization} disabled={isUploading} />
          <span>
            <strong>Enable Speaker Diarization</strong>
            <span class="help block">Identify different speakers (runs on the server, no token needed)</span>
          </span>
        </label>

        {#if options.diarization}
          <label class="field speakers">
            <span>Number of speakers</span>
            <select bind:value={options.numSpeakers} disabled={isUploading} aria-label="Number of speakers">
              <option value="auto">Auto-detect</option>
              {#each [1, 2, 3, 4, 5, 6, 7, 8] as n (n)}<option value={String(n)}>{n}</option>{/each}
            </select>
          </label>
        {/if}
      </div>

      <button type="button" class="primary lg upload-btn" onclick={handleUpload} disabled={isUploading}>
        {isUploading ? 'Uploading...' : selectedFiles.length > 1 ? `Add ${selectedFiles.length} files to queue` : 'Add to queue'}
      </button>
    </div>
  {/if}
</div>

<style>
  .upload-container {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
  }

  .dropzone {
    border: 2px dashed var(--border-strong);
    border-radius: var(--radius-lg);
    padding: var(--space-6);
    background: var(--surface-2);
    transition:
      border-color 0.15s,
      background-color 0.15s;
  }

  .dropzone.dragging {
    border-color: var(--accent);
    background: var(--accent-soft);
  }

  .dropzone.has-file {
    border-style: solid;
    border-color: var(--border);
    padding: var(--space-3);
  }

  .dropzone-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-3);
    text-align: center;
  }

  .upload-icon {
    width: 3rem;
    height: 3rem;
    color: var(--accent);
  }

  .file-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
  }

  .file-selected {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius);
  }

  .file-selected:hover {
    background: var(--surface-3);
  }

  .file-selected + .file-selected {
    border-top: 1px solid var(--border);
  }

  .file-icon {
    font-size: var(--text-lg);
  }

  .file-info {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: baseline;
    gap: var(--space-3);
  }

  .file-info h4 {
    font-size: var(--text-sm);
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .file-actions {
    display: flex;
    gap: var(--space-2);
    padding: var(--space-3) var(--space-3) var(--space-1);
  }

  .options {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }

  .options h3 {
    font-size: var(--text-lg);
  }

  .required {
    color: var(--danger);
  }

  .diarize {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    justify-content: space-between;
    gap: var(--space-4);
    padding: var(--space-4);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface-2);
  }

  .speakers {
    min-width: 12rem;
  }

  .help.block {
    display: block;
  }

  .upload-btn {
    align-self: stretch;
  }

  .message {
    white-space: pre-line;
  }

  @media (min-width: 640px) {
    .upload-btn {
      align-self: flex-start;
    }
  }
</style>
