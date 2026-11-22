<script lang="ts">
  interface Props {
    forced?: boolean;
    onsubmit: (current: string, next: string) => Promise<void>;
    onlogout?: () => void;
  }

  let { forced = false, onsubmit, onlogout }: Props = $props();

  let current = $state('');
  let next = $state('');
  let confirm = $state('');
  let error = $state('');
  let busy = $state(false);

  async function submit(event: Event) {
    event.preventDefault();
    error = '';
    if (next.length < 8) {
      error = 'The new password must be at least 8 characters.';
      return;
    }
    if (next !== confirm) {
      error = 'The new passwords do not match.';
      return;
    }
    busy = true;
    try {
      await onsubmit(current, next);
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : 'Could not change the password.';
    } finally {
      busy = false;
    }
  }
</script>

<div class="wrap">
  <form class="card form" onsubmit={submit}>
    <h2>{forced ? 'Choose a new password' : 'Change password'}</h2>
    {#if forced}
      <p class="note">An administrator has reset your password. Enter the temporary password you were given, then choose your own.</p>
    {/if}
    {#if error}<p class="notice error" role="alert">{error}</p>{/if}
    <label class="field">
      {forced ? 'Temporary password' : 'Current password'}
      <input type="password" bind:value={current} autocomplete="current-password" required />
    </label>
    <label class="field">
      New password
      <input type="password" bind:value={next} autocomplete="new-password" required minlength="8" />
    </label>
    <label class="field">
      Repeat new password
      <input type="password" bind:value={confirm} autocomplete="new-password" required minlength="8" />
    </label>
    <button type="submit" class="primary lg" disabled={busy}>{busy ? 'Saving…' : 'Change password'}</button>
    {#if onlogout}<button type="button" class="ghost" onclick={onlogout}>Sign out</button>{/if}
  </form>
</div>

<style>
  .wrap {
    display: flex;
    justify-content: center;
    align-items: center;
    min-height: 100vh;
    padding: var(--space-5) var(--space-4);
  }

  .form {
    width: 100%;
    max-width: 26rem;
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    padding: var(--space-6);
    box-shadow: var(--shadow);
  }

  h2 {
    font-size: var(--text-xl);
  }

  .field {
    font-size: var(--text-sm);
    font-weight: 600;
  }

  .note {
    color: var(--text-muted);
    font-size: var(--text-sm);
  }
</style>
