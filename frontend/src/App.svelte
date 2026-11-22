<script lang="ts">
  import { onMount } from 'svelte';
  import Navigation from './components/Navigation.svelte';
  import UserMenu from './components/UserMenu.svelte';
  import FileUpload from './components/FileUpload.svelte';
  import TranscriptViewer from './components/TranscriptViewer.svelte';
  import SpeakerReview from './components/SpeakerReview.svelte';
  import {
    changePassword,
    deleteDiarization,
    deleteTranslation,
    fetchMe,
    fetchSegments,
    fetchVersions,
    rediarize,
    setActiveDiarization,
    setAuthToken,
    translateJob,
  } from './api';
  import Admin from './components/Admin.svelte';
  import ChangePassword from './components/ChangePassword.svelte';
  import Jobs from './components/Jobs.svelte';
  import Profile from './components/Profile.svelte';
  import Login from './components/Login.svelte';
  import type { JobSummary, SegmentsResponse, TranscriptionJob, TranscriptSegment, VersionsResponse } from './types';

  // Auth state
  let isAuthenticated = $state(false);
  let username = $state('Guest');
  let accessToken = $state<string | null>(null);
  let loginError = $state('');
  let loginLoading = $state(false);
  let isAdmin = $state(false);
  let mustChangePassword = $state(false);

  // Opening a link from the "job finished" email (?job=<id>) lands on that job
  const focusJobId = typeof window !== 'undefined' ? new URLSearchParams(window.location.search).get('job') : null;

  // Page navigation state
  let currentPage = $state(focusJobId ? 'jobs' : 'home');

  // Job state
  let currentJob: TranscriptionJob | null = $state(null);
  let transcript: TranscriptSegment[] = $state([]);
  let segmentsData: SegmentsResponse | null = $state(null);
  let reviewing = $state(false);
  // Every version of the output is kept; these pick what is shown: a language and (the active) diarization.
  let versions: VersionsResponse | null = $state(null);
  let viewLanguage = $state('original');
  let jobError = $state('');

  // User-friendly error messages for OAuth errors
  const oauthErrorMessages: Record<string, string> = {
    oauth_failed: 'Authentication failed. Please try again or use a different login method.',
    no_email: 'We couldn\'t retrieve your email address. Please ensure your email is visible in your provider settings and try again.',
    invalid_provider: 'Invalid authentication provider. Please use one of the supported providers.',
    username_unavailable: 'Unable to create your account. Please contact support for assistance.',
    account_mismatch: 'This email is already registered with a different login method. Please use your original login method.',
  };

  // Restore and validate session from localStorage on mount
  onMount(() => {
    (async () => {
      // Check for OAuth callback with token in URL fragment (more secure than query params)
      const hashParams = new URLSearchParams(window.location.hash.substring(1));
      const tokenFromUrl = hashParams.get('token');
      const usernameFromUrl = hashParams.get('username');
      const oauthError = hashParams.get('error');

      if (oauthError) {
        // Display user-friendly error message
        loginError = oauthErrorMessages[oauthError] || `OAuth login failed: ${oauthError}`;
        // Clean URL fragment
        window.history.replaceState({}, document.title, window.location.pathname);
        return;
      }

      if (tokenFromUrl && usernameFromUrl) {
        // Validate token before storing by making an API call
        try {
          const response = await fetch('/auth/me', {
            headers: {
              Authorization: `Bearer ${tokenFromUrl}`,
            },
          });

          if (!response.ok) {
            loginError = 'OAuth login failed: invalid or expired token';
            // Clean URL fragment
            window.history.replaceState({}, document.title, window.location.pathname);
            return;
          }

          // OAuth login successful - token verified
          accessToken = tokenFromUrl;
          username = usernameFromUrl;
          isAuthenticated = true;

          // Store credentials
          localStorage.setItem('access_token', tokenFromUrl);
          localStorage.setItem('username', usernameFromUrl);

          // Clean URL fragment
          window.history.replaceState({}, document.title, window.location.pathname);
          return;
        } catch {
          loginError = 'OAuth login failed: unable to verify token';
          // Clean URL fragment
          window.history.replaceState({}, document.title, window.location.pathname);
          return;
        }
      }

      // Check for stored credentials
      const storedToken = localStorage.getItem('access_token');
      const storedUsername = localStorage.getItem('username');

      if (!storedToken || !storedUsername) {
        return;
      }

      try {
        const response = await fetch('/auth/me', {
          headers: {
            Authorization: `Bearer ${storedToken}`,
          },
        });

        if (!response.ok) {
          // Token is invalid or expired; clear stored credentials
          localStorage.removeItem('access_token');
          localStorage.removeItem('username');
          return;
        }

        // Token is valid - restore session
        accessToken = storedToken;
        username = storedUsername;
        isAuthenticated = true;
      } catch {
        // On network or other errors, clear potentially stale credentials
        localStorage.removeItem('access_token');
        localStorage.removeItem('username');
      }
    })();
  });

  // Hand the token to the API layer before anything renders that needs it (every /api call requires it).
  $effect.pre(() => {
    setAuthToken(isAuthenticated ? accessToken : null);
  });

  // Whenever we have a signed-in token, learn whether this user is an admin or must reset their password.
  $effect(() => {
    const token = accessToken;
    if (!isAuthenticated || !token) return;
    fetchMe(token)
      .then((me) => {
        isAdmin = me.is_superuser;
        mustChangePassword = me.must_change_password;
      })
      .catch(() => {
        /* a failure here must not lock anyone out; the server enforces the rules */
      });
  });

  async function handleChangePassword(current: string, next: string) {
    if (!accessToken) return;
    await changePassword(accessToken, current, next);
    mustChangePassword = false;
  }

  async function loginWithPassword(usernameValue: string, passwordValue: string): Promise<string> {
    const formData = new URLSearchParams();
    formData.append('username', usernameValue);
    formData.append('password', passwordValue);

    const response = await fetch('/auth/token', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: formData.toString(),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Login failed');
    }

    const data = await response.json();
    return data.access_token as string;
  }

  async function handleLogin(event: CustomEvent<{ username: string; password: string }>) {
    loginLoading = true;
    loginError = '';

    try {
      const token = await loginWithPassword(event.detail.username, event.detail.password);
      accessToken = token;
      username = event.detail.username;
      isAuthenticated = true;

      // Persist to localStorage
      localStorage.setItem('access_token', token);
      localStorage.setItem('username', event.detail.username);
    } catch (error: unknown) {
      loginError = error instanceof Error ? error.message : 'Network error. Please try again.';
    } finally {
      loginLoading = false;
    }
  }

  async function handleRegister(event: CustomEvent<{ username: string; email: string; password: string }>) {
    loginLoading = true;
    loginError = '';

    try {
      const registerResponse = await fetch('/auth/register', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          username: event.detail.username,
          email: event.detail.email,
          password: event.detail.password,
        }),
      });

      if (!registerResponse.ok) {
        const error = await registerResponse.json();
        throw new Error(error.detail || 'Registration failed');
      }

      const token = await loginWithPassword(event.detail.username, event.detail.password);

      accessToken = token;
      username = event.detail.username;
      isAuthenticated = true;

      localStorage.setItem('access_token', token);
      localStorage.setItem('username', event.detail.username);
    } catch (error: unknown) {
      loginError = error instanceof Error ? error.message : 'Network error. Please try again.';
    } finally {
      loginLoading = false;
    }
  }

  function handleLogout() {
    isAuthenticated = false;
    username = 'Guest';
    accessToken = null;
    isAdmin = false;
    mustChangePassword = false;
    currentPage = 'home';
    localStorage.removeItem('access_token');
    localStorage.removeItem('username');
    currentJob = null;
    transcript = [];
  }

  function handleNavigate(page: string) {
    currentPage = page;
  }

  // A submitted file goes straight to the queue: the Jobs page shows where it is and how far along.
  function handleUploadStart(event: CustomEvent<TranscriptionJob>) {
    void event;
    currentPage = 'jobs';
  }

  function applySegments(data: SegmentsResponse) {
    segmentsData = data;
    transcript = data.segments.map((s) => ({
      start: s.start,
      end: s.end,
      text: s.text,
      speaker: s.speaker ?? undefined,
      translation: s.translation ?? undefined,
    }));
  }

  async function refreshView() {
    if (!currentJob) return;
    const id = currentJob.job_id;
    versions = await fetchVersions(id);
    if (viewLanguage !== 'original' && !versions.translations.some((t) => t.language === viewLanguage)) viewLanguage = 'original';
    applySegments(await fetchSegments(id, viewLanguage));
  }

  async function openJob(summary: JobSummary) {
    jobError = '';
    currentJob = { job_id: summary.job_id, filename: summary.filename, status: summary.status };
    transcript = [];
    segmentsData = null;
    versions = null;
    viewLanguage = 'original';
    reviewing = false;
    try {
      await refreshView();
    } catch (e: unknown) {
      jobError = e instanceof Error ? e.message : 'Could not load the transcript';
    }
  }

  async function handleTranslate(language: string) {
    if (!currentJob) return;
    const apiKey = sessionStorage.getItem('openai_api_key') ?? '';
    // Only OpenAI translation needs a key; jobs transcribed with the local engine translate on the server.
    if ((segmentsData?.engine ?? 'openai') === 'openai' && !apiKey) {
      throw new Error('Enter your OpenAI API key (Profile) to translate.');
    }
    const added = await translateJob(currentJob.job_id, language, apiKey || null);
    viewLanguage = added.language && added.language !== 'original' ? added.language : language; // show what was just added
    await refreshView();
  }

  async function handleSelect(choice: { language?: string; diarization?: string }) {
    if (!currentJob) return;
    if (choice.language) viewLanguage = choice.language;
    // The viewed speaker version is also the active one, so the speaker review and clips always edit what is shown.
    if (choice.diarization) await setActiveDiarization(currentJob.job_id, choice.diarization);
    await refreshView();
  }

  async function handleRediarize(numSpeakers: number | null) {
    if (!currentJob) return;
    await rediarize(currentJob.job_id, numSpeakers);
    await refreshView();
  }

  async function handleDeleteVersion(kind: 'translation' | 'diarization', id: string) {
    if (!currentJob) return;
    if (kind === 'translation') await deleteTranslation(currentJob.job_id, id);
    else await deleteDiarization(currentJob.job_id, id);
    await refreshView();
  }

  function handleReset() {
    currentJob = null;
    transcript = [];
    segmentsData = null;
    versions = null;
    reviewing = false;
  }

  function openSettings() {
    currentPage = 'profile';
  }
</script>

{#if !isAuthenticated}
  <div class="auth-wrapper">
    <div class="auth-header">
      <h1>🎬 VTT Transcribe</h1>
      <p class="auth-subtitle">AI-Powered Video Transcription</p>
    </div>
    <Login
      onlogin={handleLogin}
      onregister={handleRegister}
      loading={loginLoading}
      error={loginError}
    />
  </div>
{:else if mustChangePassword}
  <ChangePassword forced onsubmit={handleChangePassword} onlogout={handleLogout} />
{:else}
  <Navigation {currentPage} onnavigate={handleNavigate} showAdmin={isAdmin} />

  <div class="app-wrapper">
    <header class="app-header">
      <UserMenu {username} isLoggedIn={true} onlogout={handleLogout} onsettings={openSettings} />
    </header>

    <main class="main-content">
      {#if currentPage === 'home'}
        <div class="page-header">
          <h2>AI-Powered Video Transcription</h2>
          <p>Upload your video files for automatic transcription with speaker diarization</p>
        </div>

        <div class="content-card">
          <FileUpload onuploadstart={handleUploadStart} />
        </div>
      {:else if currentPage === 'jobs'}
        <div class="page-header">
          <h2>Transcription Jobs</h2>
          <p>Your queue, progress and finished transcripts</p>
        </div>
        <div class="content-card">
          {#if currentJob}
            <div class="job-section">
              <button type="button" class="back" onclick={handleReset}>← Back to jobs</button>
              <h3>{currentJob.filename}</h3>
              {#if jobError}<p class="error-text" role="alert">{jobError}</p>{/if}
              {#if reviewing && segmentsData}
                <SpeakerReview
                  jobId={currentJob.job_id}
                  data={segmentsData}
                  onupdate={() => refreshView()}
                  onclose={() => (reviewing = false)}
                />
              {:else if transcript.length > 0}
                <TranscriptViewer
                  segments={transcript}
                  jobId={currentJob.job_id}
                  onreset={handleReset}
                  onreview={segmentsData ? () => (reviewing = true) : undefined}
                  ontranslate={segmentsData ? handleTranslate : undefined}
                  translatedTo={segmentsData?.translated_to ?? null}
                  {versions}
                  language={viewLanguage}
                  diarization={segmentsData?.diarization ?? null}
                  onselect={handleSelect}
                  onrediarize={segmentsData ? handleRediarize : undefined}
                  ondelete={handleDeleteVersion}
                />
              {/if}
            </div>
          {:else}
            <Jobs onopen={openJob} {focusJobId} onhome={() => (currentPage = 'home')} />
          {/if}
        </div>
      {:else if currentPage === 'profile' && accessToken}
        <div class="page-header">
          <h2>Profile & settings</h2>
          <p>Defaults for new jobs, retention, your OpenAI key, email and account</p>
        </div>
        <div class="content-card">
          <Profile token={accessToken} onaccountdeleted={handleLogout} />
        </div>
      {:else if currentPage === 'admin' && isAdmin && accessToken}
        <div class="page-header">
          <h2>Administration</h2>
          <p>Review users, deactivate or delete accounts, and force password resets</p>
        </div>
        <div class="content-card">
          <Admin token={accessToken} currentUsername={username} />
        </div>
      {:else if currentPage === 'about'}
        <div class="page-header">
          <h2>About VTT Transcribe</h2>
          <p>Learn more about this application</p>
        </div>
        <div class="content-card">
          <p>
            VTT Transcribe is an AI-powered video transcription tool that uses OpenAI's Whisper
            model for accurate speech-to-text conversion and sherpa-onnx for speaker diarization.
          </p>
          <h3>Features:</h3>
          <ul>
            <li>High-quality audio transcription</li>
            <li>Speaker diarization</li>
            <li>Multi-language support</li>
            <li>Automatic translation</li>
            <li>VTT subtitle export</li>
          </ul>
        </div>
      {/if}
    </main>

    <footer class="app-footer">
      <p>Powered by OpenAI Whisper &amp; sherpa-onnx · <a href="https://github.com/JLCodeSource/vtt-transcribe-server">Source on GitHub</a></p>
    </footer>
  </div>

{/if}

<style>
  .app-wrapper {
    margin-left: var(--nav-width);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }

  .app-header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    height: 3.75rem;
    padding: 0 var(--space-6);
    display: flex;
    align-items: center;
    justify-content: flex-end;
    position: sticky;
    top: 0;
    z-index: 90;
  }

  .main-content {
    flex: 1;
    width: 100%;
    max-width: var(--content-width);
    margin: 0 auto;
    padding: var(--space-6);
  }

  .page-header {
    margin-bottom: var(--space-5);
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .page-header h2 {
    font-size: var(--text-2xl);
    letter-spacing: -0.015em;
  }

  .page-header p {
    color: var(--text-muted);
  }

  .content-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-sm);
    padding: var(--space-5);
  }

  .content-card ul {
    line-height: 1.8;
    color: var(--text-muted);
  }

  .content-card h3 {
    margin: var(--space-5) 0 var(--space-2);
  }

  .back {
    align-self: flex-start;
  }

  .error-text {
    color: var(--danger);
  }

  .job-section {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
  }

  .app-footer {
    text-align: center;
    color: var(--text-muted);
    padding: var(--space-5);
    font-size: var(--text-sm);
  }

  .auth-wrapper {
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--space-5);
    padding: var(--space-5) var(--space-4);
  }

  .auth-header {
    text-align: center;
  }

  .auth-header h1 {
    font-size: var(--text-2xl);
    margin-bottom: var(--space-1);
  }

  .auth-subtitle {
    color: var(--text-muted);
  }

  @media (max-width: 800px) {
    .app-wrapper {
      margin-left: 0;
    }

    .app-header {
      position: static;
      height: 3rem;
      padding: 0 var(--space-4);
    }

    .main-content {
      padding: var(--space-4);
    }

    .content-card {
      padding: var(--space-4);
    }

    .page-header h2 {
      font-size: var(--text-xl);
    }
  }
</style>
