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
  <form class="card" onsubmit={submit}>
    <h2>{forced ? 'Choose a new password' : 'Change password'}</h2>
    {#if forced}
      <p class="note">An administrator has reset your password. Enter the temporary password you were given, then choose your own.</p>
    {/if}
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    <label>
      {forced ? 'Temporary password' : 'Current password'}
      <input type="password" bind:value={current} autocomplete="current-password" required />
    </label>
    <label>
      New password
      <input type="password" bind:value={next} autocomplete="new-password" required minlength="8" />
    </label>
    <label>
      Repeat new password
      <input type="password" bind:value={confirm} autocomplete="new-password" required minlength="8" />
    </label>
    <button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Change password'}</button>
    {#if onlogout}<button type="button" class="link" onclick={onlogout}>Sign out</button>{/if}
  </form>
</div>

<style>
  .wrap { display: flex; justify-content: center; padding: 3rem 1rem; }
  .card { background: white; border-radius: 12px; padding: 2rem; width: 100%; max-width: 420px; box-shadow: 0 4px 16px rgba(0,0,0,.1); display: flex; flex-direction: column; gap: 1rem; }
  h2 { margin: 0; }
  label { display: flex; flex-direction: column; gap: .35rem; font-size: .9rem; color: #374151; }
  input { padding: .6rem .75rem; border: 1px solid #d1d5db; border-radius: 8px; font-size: 1rem; }
  button[type='submit'] { padding: .7rem; border: 0; border-radius: 8px; background: #6366f1; color: white; font-weight: 600; cursor: pointer; }
  button[disabled] { opacity: .6; cursor: default; }
  .link { background: none; border: 0; color: #6366f1; cursor: pointer; }
  .error { background: #fee2e2; color: #991b1b; padding: .6rem .75rem; border-radius: 8px; margin: 0; }
  .note { color: #4b5563; margin: 0; font-size: .9rem; }
</style>
