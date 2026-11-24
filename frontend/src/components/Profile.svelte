<script lang="ts">
  import { onMount } from 'svelte';
  import {
    changePassword,
    deleteAccount,
    fetchProfile,
    removeOpenAIKey,
    setOpenAIKey,
    updateProfile,
  } from '../api';
  import { languageChoices, loadLanguages, TRANSLATION_LANGUAGES } from '../languages';
  import type { Profile } from '../types';

  interface Props {
    token: string;
    onaccountdeleted?: () => void;
  }

  const { token, onaccountdeleted }: Props = $props();

  let languages: string[] = $state(TRANSLATION_LANGUAGES);
  loadLanguages().then((l) => (languages = l));

  type Section = 'retention' | 'defaults' | 'key' | 'email' | 'password' | 'account' | 'page';

  let profile: Profile | null = $state(null);
  // Feedback is shown inside the section that was saved, so it is clear what it refers to.
  let status: { section: Section; kind: 'success' | 'error'; text: string } | null = $state(null);

  let mode = $state('default');
  let days = $state('7');
  let notify = $state(true);
  let engine = $state('');
  let diarize = $state(false);
  let speakers = $state('auto');
  let translateTo = $state('');
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
    speakers = p.default_options.num_speakers ? String(p.default_options.num_speakers) : 'auto';
    translateTo = p.default_options.translate_to ?? '';
  }

  async function run(section: Section, fn: () => Promise<void>, done: string) {
    status = null;
    try {
      await fn();
      if (done) status = { section, kind: 'success', text: done };
    } catch (e: unknown) {
      status = {
        section,
        kind: 'error',
        text: e instanceof Error ? e.message : 'Something went wrong',
      };
    }
  }

  onMount(() => run('page', async () => load(await fetchProfile()), ''));

  const saveRetention = () =>
    run(
      'retention',
      async () =>
        load(
          await updateProfile({
            retention_mode: mode,
            retention_days: mode === 'hold' ? Number(days) : null,
          })
        ),
      'Retention saved'
    );
  const saveNotify = () =>
    run(
      'email',
      async () => load(await updateProfile({ notify_job_ready: notify })),
      'Email preference saved'
    );
  const saveOptions = () =>
    run(
      'defaults',
      async () =>
        load(
          await updateProfile({
            default_options: {
              ...(engine ? { engine } : {}),
              diarize,
              ...(diarize && speakers !== 'auto' ? { num_speakers: Number(speakers) } : {}),
              ...(translateTo.trim() ? { translate_to: translateTo.trim() } : {}),
            },
          })
        ),
      'Defaults saved'
    );
  const saveKey = () =>
    run(
      'key',
      async () => {
        load(await setOpenAIKey(keyInput));
        keyInput = '';
      },
      'API key saved'
    );
  const removeKey = () => run('key', async () => load(await removeOpenAIKey()), 'API key removed');
  const savePassword = () =>
    run(
      'password',
      async () => {
        await changePassword(token, currentPassword, newPassword);
        currentPassword = newPassword = '';
      },
      'Password changed'
    );
  const removeAccount = () =>
    run(
      'account',
      async () => {
        await deleteAccount(deletePassword || null);
        onaccountdeleted?.();
      },
      ''
    );
</script>

{#snippet feedback(section: Section)}
  {#if status && status.section === section}
    <p class="notice {status.kind}" role={status.kind === 'error' ? 'alert' : 'status'}>
      {status.text}
    </p>
  {/if}
{/snippet}

<div class="profile">
  {@render feedback('page')}

  {#if profile}
    <p class="muted">Signed in as <strong>{profile.username}</strong> ({profile.email})</p>

    <section class="section">
      <h3>Defaults for new jobs</h3>
      <p class="help">
        These fill in the upload form; each one can still be changed for an individual file when you
        add it.
      </p>
      <div class="form-grid">
        <label class="field">
          <span>Engine</span>
          <select bind:value={engine} aria-label="Default engine">
            <option value="">No preference</option>
            <option value="openai">OpenAI Whisper (API)</option>
            <option value="local">Local Whisper (no key)</option>
          </select>
        </label>
        <label class="field">
          <span>Translate to</span>
          <select bind:value={translateTo} aria-label="Default translation language">
            <option value="">No translation</option>
            {#each languageChoices(languages, translateTo) as l (l)}<option value={l}>
                {l}
              </option>{/each}
          </select>
        </label>
      </div>
      <label class="check">
        <input type="checkbox" bind:checked={diarize} /> <span>Identify speakers</span>
      </label>
      {#if diarize}
        <label class="field narrow">
          <span>Number of speakers</span>
          <select bind:value={speakers} aria-label="Default number of speakers">
            <option value="auto">Auto-detect</option>
            {#each [1, 2, 3, 4, 5, 6, 7, 8] as n (n)}<option value={String(n)}>{n}</option>{/each}
          </select>
        </label>
      {/if}
      <div class="section-actions">
        <button type="button" class="primary" onclick={saveOptions}>Save defaults</button>
      </div>
      {@render feedback('defaults')}
    </section>

    <section class="section">
      <h3>Keep my results</h3>
      <p class="help">
        Default for new jobs; you can change it for each file when you add it, and later on the job.
        Platform default:
        {profile.platform_retention.mode === 'hold'
          ? `${profile.platform_retention.days} days`
          : profile.platform_retention.mode}.
      </p>
      <div class="form-grid">
        <label class="field">
          <span>Policy</span>
          <select bind:value={mode} aria-label="Default retention">
            <option value="default">Platform default</option>
            <option value="hold">Keep for a number of days</option>
            <option value="keep">Keep until I delete it</option>
            <option value="delete">Delete after I download it</option>
          </select>
        </label>
        {#if mode === 'hold'}
          <label class="field">
            <span>Days</span>
            <input type="number" min="1" max="3650" bind:value={days} aria-label="Retention days" />
          </label>
        {/if}
      </div>
      <div class="section-actions">
        <button type="button" class="primary" onclick={saveRetention}>Save retention</button>
      </div>
      {@render feedback('retention')}
    </section>

    <section class="section">
      <h3>OpenAI API key</h3>
      {#if profile.has_openai_key}
        <p>Stored key: <code>{profile.openai_key_hint}</code></p>
      {:else}
        <p class="help">No key stored. Without one you need to enter a key for each OpenAI job.</p>
      {/if}
      <label class="field">
        <span>{profile.has_openai_key ? 'Replace key' : 'Key'}</span>
        <input
          type="password"
          autocomplete="off"
          placeholder="sk-..."
          bind:value={keyInput}
          aria-label="OpenAI API key"
        />
      </label>
      <p class="help">
        Stored encrypted on the server and used only when a job or translation has no key of its
        own.
      </p>
      <div class="section-actions">
        <button
          type="button"
          class="primary"
          disabled={keyInput.trim().length < 8}
          onclick={saveKey}
        >
          Save key
        </button>
        {#if profile.has_openai_key}<button type="button" class="danger" onclick={removeKey}>
            Remove key
          </button>{/if}
      </div>
      {@render feedback('key')}
    </section>

    <section class="section">
      <h3>Email</h3>
      <label class="check">
        <input type="checkbox" bind:checked={notify} onchange={saveNotify} />
        <span>Email me when a job finishes or fails</span>
      </label>
      {#if !profile.email_enabled}<p class="help">
          This server has no email configured, so nothing is sent for now.
        </p>{/if}
      {@render feedback('email')}
    </section>

    {#if !profile.oauth_provider}
      <section class="section">
        <h3>Password</h3>
        <div class="form-grid">
          <label class="field">
            <span>Current password</span><input
              type="password"
              autocomplete="current-password"
              bind:value={currentPassword}
            />
          </label>
          <label class="field">
            <span>New password</span><input
              type="password"
              autocomplete="new-password"
              bind:value={newPassword}
            />
          </label>
        </div>
        <p class="help">At least 8 characters.</p>
        <div class="section-actions">
          <button
            type="button"
            class="primary"
            disabled={newPassword.length < 8 || !currentPassword}
            onclick={savePassword}
          >
            Change password
          </button>
        </div>
        {@render feedback('password')}
      </section>
    {/if}

    <section class="section danger-zone">
      <h3>Delete account</h3>
      <p class="help">
        Deletes your account, all jobs, results, stored audio and your saved API key. This cannot be
        undone.
      </p>
      {#if !confirmDelete}
        <div class="section-actions">
          <button type="button" class="danger" onclick={() => (confirmDelete = true)}>
            Delete my account…
          </button>
        </div>
      {:else}
        {#if !profile.oauth_provider}
          <label class="field narrow">
            <span>Confirm with your password</span>
            <input type="password" bind:value={deletePassword} aria-label="Password to confirm" />
          </label>
        {/if}
        <div class="section-actions">
          <button type="button" class="danger solid" onclick={removeAccount}>
            Permanently delete
          </button>
          <button type="button" onclick={() => (confirmDelete = false)}>Keep my account</button>
        </div>
      {/if}
      {@render feedback('account')}
    </section>
  {:else if !status}
    <p class="muted">Loading…</p>
  {/if}
</div>

<style>
  .profile {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    max-width: 42rem;
  }

  .narrow {
    max-width: 16rem;
  }

  .notice {
    margin: 0;
  }
</style>
