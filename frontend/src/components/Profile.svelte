<script lang="ts">
  import { onMount } from 'svelte';
  import { changePassword, deleteAccount, fetchProfile, removeOpenAIKey, setOpenAIKey, updateProfile } from '../api';
  import type { Profile } from '../types';

  interface Props {
    token: string;
    onaccountdeleted?: () => void;
  }

  const { token, onaccountdeleted }: Props = $props();

  let profile: Profile | null = $state(null);
  let message = $state('');
  let error = $state('');

  let mode = $state('default');
  let days = $state('7');
  let notify = $state(true);
  let engine = $state('');
  let diarize = $state(false);
  let keyInput = $state('');
  let currentPassword = $state('');
  let newPassword = $state('');
  let deletePassword = $state('');
  let confirmDelete = $state(false);

  function load(p: Profile) {
    profile = p;
    mode = p.retention_mode;
    days = String(p.retention_days ?? p.platform_retention.days ?? 7);
    notify = p.notify_job_ready;
    engine = p.default_options.engine ?? '';
    diarize = p.default_options.diarize ?? false;
  }

  async function run(fn: () => Promise<void>, done: string) {
    error = '';
    message = '';
    try {
      await fn();
      message = done;
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : 'Something went wrong';
    }
  }

  onMount(() => run(async () => load(await fetchProfile()), ''));

  const saveRetention = () =>
    run(async () => load(await updateProfile({ retention_mode: mode, retention_days: mode === 'hold' ? Number(days) : null })), 'Retention saved');
  const saveNotify = () => run(async () => load(await updateProfile({ notify_job_ready: notify })), 'Email preference saved');
  const saveOptions = () =>
    run(async () => load(await updateProfile({ default_options: { ...(engine ? { engine } : {}), diarize } })), 'Defaults saved');
  const saveKey = () =>
    run(async () => {
      load(await setOpenAIKey(keyInput));
      keyInput = '';
    }, 'API key saved');
  const removeKey = () => run(async () => load(await removeOpenAIKey()), 'API key removed');
  const savePassword = () =>
    run(async () => {
      await changePassword(token, currentPassword, newPassword);
      currentPassword = newPassword = '';
    }, 'Password changed');
  const removeAccount = () =>
    run(async () => {
      await deleteAccount(deletePassword || null);
      onaccountdeleted?.();
    }, '');
</script>

<div class="profile">
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if message}<p class="ok" role="status">{message}</p>{/if}

  {#if profile}
    <p class="muted">Signed in as <strong>{profile.username}</strong> ({profile.email})</p>

    <section>
      <h3>Keep my results</h3>
      <p class="muted">
        Default for new jobs; you can change it for each file when you add it, and later on the job. Platform default:
        {profile.platform_retention.mode === 'hold' ? `${profile.platform_retention.days} days` : profile.platform_retention.mode}.
      </p>
      <label>
        <span>Policy</span>
        <select bind:value={mode} aria-label="Default retention">
          <option value="default">Platform default</option>
          <option value="hold">Keep for a number of days</option>
          <option value="keep">Keep until I delete it</option>
          <option value="delete">Delete after I download it</option>
        </select>
      </label>
      {#if mode === 'hold'}
        <label><span>Days</span><input type="number" min="1" max="3650" bind:value={days} aria-label="Retention days" /></label>
      {/if}
      <button type="button" onclick={saveRetention}>Save retention</button>
    </section>

    <section>
      <h3>Defaults for new jobs</h3>
      <label>
        <span>Engine</span>
        <select bind:value={engine} aria-label="Default engine">
          <option value="">No preference</option>
          <option value="openai">OpenAI Whisper (API)</option>
          <option value="local">Local Whisper (no key)</option>
        </select>
      </label>
      <label class="check"><input type="checkbox" bind:checked={diarize} /> <span>Identify speakers</span></label>
      <button type="button" onclick={saveOptions}>Save defaults</button>
    </section>

    <section>
      <h3>OpenAI API key</h3>
      {#if profile.has_openai_key}
        <p>Stored key: <code>{profile.openai_key_hint}</code></p>
      {:else}
        <p class="muted">No key stored. Without one you need to enter a key for each OpenAI job.</p>
      {/if}
      <label>
        <span>{profile.has_openai_key ? 'Replace key' : 'Key'}</span>
        <input type="password" autocomplete="off" placeholder="sk-..." bind:value={keyInput} aria-label="OpenAI API key" />
      </label>
      <div class="row">
        <button type="button" disabled={keyInput.trim().length < 8} onclick={saveKey}>Save key</button>
        {#if profile.has_openai_key}<button type="button" class="danger" onclick={removeKey}>Remove key</button>{/if}
      </div>
      <p class="muted">Stored encrypted on the server and used only when a job or translation has no key of its own.</p>
    </section>

    <section>
      <h3>Email</h3>
      <label class="check">
        <input type="checkbox" bind:checked={notify} onchange={saveNotify} />
        <span>Email me when a job finishes or fails</span>
      </label>
      {#if !profile.email_enabled}<p class="muted">This server has no email configured, so nothing is sent for now.</p>{/if}
    </section>

    {#if !profile.oauth_provider}
      <section>
        <h3>Password</h3>
        <label><span>Current password</span><input type="password" autocomplete="current-password" bind:value={currentPassword} /></label>
        <label><span>New password</span><input type="password" autocomplete="new-password" bind:value={newPassword} /></label>
        <button type="button" disabled={newPassword.length < 8 || !currentPassword} onclick={savePassword}>Change password</button>
      </section>
    {/if}

    <section class="danger-zone">
      <h3>Delete account</h3>
      <p class="muted">Deletes your account, all jobs, results, stored audio and your saved API key. This cannot be undone.</p>
      {#if !confirmDelete}
        <button type="button" class="danger" onclick={() => (confirmDelete = true)}>Delete my account…</button>
      {:else}
        {#if !profile.oauth_provider}
          <label><span>Confirm with your password</span><input type="password" bind:value={deletePassword} aria-label="Password to confirm" /></label>
        {/if}
        <div class="row">
          <button type="button" class="danger" onclick={removeAccount}>Permanently delete</button>
          <button type="button" onclick={() => (confirmDelete = false)}>Keep my account</button>
        </div>
      {/if}
    </section>
  {:else if !error}
    <p class="muted">Loading…</p>
  {/if}
</div>

<style>
  .profile { display: flex; flex-direction: column; gap: 1.5rem; max-width: 640px; margin: 0 auto; }
  section { border: 1px solid #e5e7eb; border-radius: 10px; padding: 1rem 1.25rem; display: flex; flex-direction: column; gap: 0.6rem; }
  h3 { margin: 0; }
  label { display: flex; flex-direction: column; gap: 0.25rem; }
  label.check { flex-direction: row; align-items: center; gap: 0.5rem; }
  .row { display: flex; gap: 0.5rem; }
  .muted { color: #6b7280; font-size: 0.875rem; margin: 0; }
  .error { color: #b91c1c; }
  .ok { color: #047857; }
  .danger { color: #b91c1c; }
  .danger-zone { border-color: #fecaca; }
</style>
