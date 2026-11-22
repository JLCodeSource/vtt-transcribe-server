<script lang="ts">
  import { onMount } from 'svelte';
  import { authHeaders, fetchEngines, fetchProfile } from '../api';
  import type { UploadOptions, TranscriptionJob, EnginesResponse } from '../types';

  interface Props {
    onuploadstart: (event: CustomEvent<TranscriptionJob>) => void;
  }

  const { onuploadstart }: Props = $props();

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
  const needsKey = $derived((engines.find((e) => e.id === options.engine)?.requires_api_key ?? true) && !profileHasKey);
  let isUploading = $state(false);

  onMount(() => {
    fetchProfile()
      .then((p) => {
        profileHasKey = p.has_openai_key;
        if (p.default_options.engine) options.engine = p.default_options.engine;
        if (p.default_options.diarize !== undefined) options.diarization = p.default_options.diarize;
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

  function clearFiles() {
    selectedFiles = [];
    validationError = '';
  }
</script>

<div class="upload-container">
  <div class="dropzone"
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
        <svg class="upload-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
          />
        </svg>
        <h3>Drop your video or audio files here</h3>
        <p>or</p>
        <label for="file-input" class="file-label">
          <button
            type="button"
            class="primary"
            onclick={() => document.getElementById('file-input')?.click()}
          >
            Choose Files
          </button>
        </label>
        <p class="hint">Supports MP4, AVI, MOV, MP3, WAV, and more. Pick as many files as you like; each is queued separately.</p>
      </div>
    {:else}
      <ul class="file-list" aria-label="Selected files">
        {#each selectedFiles as file (file.name + file.size + file.lastModified)}
          <li class="file-selected">
            <svg class="file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor">
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
              />
            </svg>
            <div class="file-info">
              <h4>{file.name}</h4>
              <p>{(file.size / 1024 / 1024).toFixed(2)} MB</p>
            </div>
            <button type="button" class="clear-btn" onclick={() => removeFile(file)} disabled={isUploading} aria-label={`Remove ${file.name}`}>
              ✕
            </button>
          </li>
        {/each}
      </ul>
      <div class="file-actions">
        <button type="button" class="secondary" onclick={() => document.getElementById('file-input')?.click()} disabled={isUploading}>
          Add more files
        </button>
        <button type="button" class="secondary" onclick={clearFiles} disabled={isUploading}>Clear all</button>
      </div>
    {/if}
    <input
      id="file-input"
      type="file"
      multiple
      accept="video/*,audio/*"
      onchange={handleFileSelect}
      style="display: none;"
    />
  </div>

  {#if selectedFiles.length > 0}
    <div class="options">
      <h3>Transcription Options</h3>

      <label class="input-label">
        <span>Engine</span>
        <select bind:value={options.engine} disabled={isUploading} aria-label="Engine">
          {#each engines as e (e.id)}
            <option value={e.id} disabled={!e.available}>{e.label}{e.available ? '' : ' — not installed'}</option>
          {/each}
        </select>
      </label>

      {#if needsKey}
        <label class="input-label">
          <span>OpenAI API Key <span class="required">*</span></span>
          <input
            type="password"
            bind:value={options.apiKey}
            placeholder="sk-..."
            disabled={isUploading}
            required
          />
          <span class="hint-text">Required for transcription with the OpenAI engine</span>
        </label>
      {/if}

      <label class="checkbox-label">
        <input type="checkbox" bind:checked={options.diarization} disabled={isUploading} />
        <span>Enable Speaker Diarization</span>
        <span class="hint-text">Identify different speakers (runs on the server, no token needed)</span>
      </label>

      {#if options.diarization}
        <label class="input-label">
          <span>Number of speakers</span>
          <select bind:value={options.numSpeakers} disabled={isUploading} aria-label="Number of speakers">
            <option value="auto">Auto-detect</option>
            {#each [1, 2, 3, 4, 5, 6, 7, 8] as n (n)}<option value={String(n)}>{n}</option>{/each}
          </select>
        </label>
      {/if}

      <label class="input-label">
        <span>Translate to (optional)</span>
        <input
          type="text"
          bind:value={options.translateTo}
          placeholder="e.g. Spanish"
          disabled={isUploading}
          aria-label="Translate to"
        />
        <span class="hint-text">Needs an OpenAI API key (unless you use the local engine)</span>
      </label>

      <label class="input-label">
        <span>Keep the result</span>
        <select bind:value={options.retentionMode} disabled={isUploading} aria-label="Retention">
          <option value="">Use my default</option>
          <option value="hold">Keep for a number of days</option>
          <option value="keep">Keep until I delete it</option>
          <option value="delete">Delete after I download it</option>
        </select>
      </label>
      {#if options.retentionMode === 'hold'}
        <label class="input-label">
          <span>Days to keep</span>
          <input type="number" min="1" max="3650" bind:value={options.retentionDays} disabled={isUploading} aria-label="Days to keep" />
        </label>
      {/if}

      <label class="input-label">
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

      <label class="input-label">
        <span>Model</span>
        <select bind:value={options.model} disabled={isUploading}>
          <option value="whisper-1">Whisper-1 (Default)</option>
        </select>
      </label>

      <button type="button" class="primary upload-btn" onclick={handleUpload} disabled={isUploading}>
        {isUploading ? 'Uploading...' : selectedFiles.length > 1 ? `Add ${selectedFiles.length} files to queue` : 'Add to queue'}
      </button>
    </div>
  {/if}

  {#if validationError}
    <div class="error-message" role="alert" style="white-space: pre-line;">
      {validationError}
    </div>
  {/if}
</div>

<style>
  .upload-container {
    display: flex;
    flex-direction: column;
    gap: 2rem;
  }

  .dropzone {
    border: 3px dashed var(--border-color);
    border-radius: 12px;
    padding: 3rem;
    text-align: center;
    transition: all 0.3s;
    background: var(--background);
  }

  .dropzone.dragging {
    border-color: var(--primary-color);
    background: rgba(102, 126, 234, 0.05);
    transform: scale(1.02);
  }

  .dropzone.has-file {
    border-color: var(--success-color);
    background: rgba(16, 185, 129, 0.05);
  }

  .dropzone-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 1rem;
  }

  .upload-icon {
    width: 80px;
    height: 80px;
    color: var(--primary-color);
    margin-bottom: 1rem;
  }

  .dropzone-content h3 {
    margin: 0;
    font-size: 1.5rem;
    color: var(--text-color);
  }

  .dropzone-content p {
    margin: 0;
    color: var(--text-light);
  }

  .hint {
    font-size: 0.875rem;
    margin-top: 0.5rem !important;
  }

  .file-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    max-height: 22rem;
    overflow-y: auto;
  }

  .file-actions {
    display: flex;
    gap: 0.75rem;
    justify-content: center;
    margin-top: 1rem;
  }

  .file-selected {
    display: flex;
    align-items: center;
    gap: 1.5rem;
    padding: 1rem;
  }

  .file-icon {
    width: 60px;
    height: 60px;
    color: var(--success-color);
    flex-shrink: 0;
  }

  .file-info {
    flex: 1;
    text-align: left;
  }

  .file-info h4 {
    margin: 0 0 0.25rem 0;
    font-size: 1.125rem;
    word-break: break-all;
  }

  .file-info p {
    margin: 0;
    color: var(--text-light);
    font-size: 0.875rem;
  }

  .clear-btn {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: var(--error-color);
    color: white;
    font-size: 1.25rem;
    padding: 0;
    flex-shrink: 0;
  }

  .options {
    display: flex;
    flex-direction: column;
    gap: 1.5rem;
    padding: 2rem;
    background: var(--background);
    border-radius: 12px;
  }

  .options h3 {
    margin: 0;
    font-size: 1.25rem;
  }

  .checkbox-label {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    cursor: pointer;
  }

  .checkbox-label input[type='checkbox'] {
    width: 20px;
    height: 20px;
    cursor: pointer;
  }

  .checkbox-label > span:first-of-type {
    font-weight: 500;
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .hint-text {
    font-size: 0.875rem;
    color: var(--text-light);
  }

  .input-label {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .input-label > span:first-child {
    font-weight: 500;
  }

  .input-label select {
    width: 100%;
  }

  .upload-btn {
    margin-top: 1rem;
    width: 100%;
    padding: 1rem;
    font-size: 1.125rem;
  }

  .error-message {
    padding: 1rem;
    background: #fee;
    border: 2px solid var(--error-color);
    border-radius: 8px;
    color: var(--error-color);
    font-weight: 500;
  }

  .required {
    color: var(--error-color);
  }

  .input-label input[type='password'] {
    width: 100%;
    padding: 0.75rem;
    border: 2px solid var(--border-color);
    border-radius: 8px;
    font-size: 1rem;
  }

  .input-label input[type='password']:focus {
    outline: none;
    border-color: var(--primary-color);
  }
</style>
