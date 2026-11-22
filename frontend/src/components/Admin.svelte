<script lang="ts">
  import { onMount } from 'svelte';
  import {
    adminDeleteUser,
    adminListUsers,
    adminResetPassword,
    adminSetActive,
    adminSetAdmin,
  } from '../api';
  import type { AdminUser } from '../types';

  interface Props {
    token: string;
    currentUsername: string;
  }

  let { token, currentUsername }: Props = $props();

  let users = $state<AdminUser[]>([]);
  let total = $state(0);
  let query = $state('');
  let error = $state('');
  let loading = $state(false);
  let temp = $state<{ username: string; password: string } | null>(null);

  async function load() {
    loading = true;
    error = '';
    try {
      const page = await adminListUsers(token, query);
      users = page.users;
      total = page.total;
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : 'Could not load users.';
    } finally {
      loading = false;
    }
  }

  async function act(fn: () => Promise<unknown>) {
    error = '';
    try {
      await fn();
      await load();
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : 'Action failed.';
    }
  }

  const toggleActive = (u: AdminUser) => act(() => adminSetActive(token, u.id, !u.is_active));
  const toggleAdmin = (u: AdminUser) => act(() => adminSetAdmin(token, u.id, !u.is_superuser));

  async function resetPassword(u: AdminUser) {
    if (!confirm(`Reset the password for ${u.username}? They must choose a new one at next sign-in.`)) return;
    error = '';
    try {
      const r = await adminResetPassword(token, u.id);
      temp = { username: u.username, password: r.temporary_password };
      await load();
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : 'Action failed.';
    }
  }

  async function remove(u: AdminUser) {
    if (!confirm(`Permanently delete ${u.username} and all their jobs and API keys? This cannot be undone.`)) return;
    await act(() => adminDeleteUser(token, u.id));
  }

  onMount(load);
</script>

<section class="admin">
  <form
    class="toolbar"
    onsubmit={(e) => {
      e.preventDefault();
      load();
    }}
  >
    <input type="search" placeholder="Search username or email" bind:value={query} aria-label="Search users" />
    <button type="submit">Search</button>
    <span class="count">{total} user{total === 1 ? '' : 's'}</span>
  </form>

  {#if error}<p class="error" role="alert">{error}</p>{/if}

  {#if temp}
    <div class="temp" role="status">
      Temporary password for <strong>{temp.username}</strong>: <code>{temp.password}</code>
      <span>(shown once; share it securely)</span>
      <button type="button" onclick={() => (temp = null)}>Dismiss</button>
    </div>
  {/if}

  <table>
    <thead>
      <tr><th>User</th><th>Email</th><th>Status</th><th>Jobs</th><th>Joined</th><th>Actions</th></tr>
    </thead>
    <tbody>
      {#each users as u (u.id)}
        <tr data-user={u.username}>
          <td>{u.username}{#if u.is_superuser} <span class="badge admin">admin</span>{/if}</td>
          <td>{u.email}</td>
          <td>
            {#if u.is_active}<span class="badge ok">active</span>{:else}<span class="badge off">deactivated</span>{/if}
            {#if u.must_change_password} <span class="badge warn">must reset</span>{/if}
            {#if u.oauth_provider} <span class="badge">{u.oauth_provider}</span>{/if}
          </td>
          <td>{u.job_count}</td>
          <td>{u.created_at.slice(0, 10)}</td>
          <td class="actions">
            {#if u.username !== currentUsername}
              <button type="button" onclick={() => toggleActive(u)}>{u.is_active ? 'Deactivate' : 'Activate'}</button>
              {#if !u.oauth_provider}<button type="button" onclick={() => resetPassword(u)}>Reset password</button>{/if}
              <button type="button" onclick={() => toggleAdmin(u)}>{u.is_superuser ? 'Remove admin' : 'Make admin'}</button>
              <button type="button" class="danger" onclick={() => remove(u)}>Delete</button>
            {:else}
              <em>you</em>
            {/if}
          </td>
        </tr>
      {:else}
        <tr><td colspan="6">{loading ? 'Loading…' : 'No users found.'}</td></tr>
      {/each}
    </tbody>
  </table>
</section>

<style>
  .toolbar { display: flex; gap: .5rem; align-items: center; margin-bottom: 1rem; }
  .toolbar input { flex: 1; max-width: 320px; padding: .5rem .75rem; border: 1px solid #d1d5db; border-radius: 8px; }
  .count { color: #6b7280; margin-left: auto; }
  table { width: 100%; border-collapse: collapse; font-size: .9rem; }
  th, td { text-align: left; padding: .55rem .5rem; border-bottom: 1px solid #e5e7eb; vertical-align: top; }
  button { padding: .3rem .6rem; border: 1px solid #d1d5db; border-radius: 6px; background: white; cursor: pointer; margin: 0 .25rem .25rem 0; }
  button.danger { color: #b91c1c; border-color: #fca5a5; }
  .actions { white-space: normal; }
  .badge { font-size: .72rem; padding: .1rem .4rem; border-radius: 999px; background: #e5e7eb; color: #374151; }
  .badge.admin { background: #ede9fe; color: #5b21b6; }
  .badge.ok { background: #dcfce7; color: #166534; }
  .badge.off { background: #fee2e2; color: #991b1b; }
  .badge.warn { background: #fef3c7; color: #92400e; }
  .error { background: #fee2e2; color: #991b1b; padding: .6rem .75rem; border-radius: 8px; }
  .temp { background: #fef3c7; padding: .6rem .75rem; border-radius: 8px; margin-bottom: 1rem; display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; }
  code { background: white; padding: .1rem .4rem; border-radius: 4px; }
</style>
