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
    <button type="submit" class="primary">Search</button>
    <span class="count">{total} user{total === 1 ? '' : 's'}</span>
  </form>

  {#if error}<p class="notice error" role="alert">{error}</p>{/if}

  {#if temp}
    <div class="notice warning temp" role="status">
      Temporary password for <strong>{temp.username}</strong>: <code>{temp.password}</code>
      <span>(shown once; share it securely)</span>
      <button type="button" class="sm" onclick={() => (temp = null)}>Dismiss</button>
    </div>
  {/if}

  <div class="table-wrap">
  <table>
    <thead>
      <tr><th>User</th><th>Email</th><th>Status</th><th>Jobs</th><th>Joined</th><th>Actions</th></tr>
    </thead>
    <tbody>
      {#each users as u (u.id)}
        <tr data-user={u.username}>
          <td>{u.username}{#if u.is_superuser} <span class="chip info">admin</span>{/if}</td>
          <td>{u.email}</td>
          <td>
            {#if u.is_active}<span class="chip success">active</span>{:else}<span class="chip danger">deactivated</span>{/if}
            {#if u.must_change_password} <span class="chip pending">must reset</span>{/if}
            {#if u.oauth_provider} <span class="chip">{u.oauth_provider}</span>{/if}
          </td>
          <td>{u.job_count}</td>
          <td>{u.created_at.slice(0, 10)}</td>
          <td><div class="actions">
            {#if u.username !== currentUsername}
              <button type="button" class="sm" onclick={() => toggleActive(u)}>{u.is_active ? 'Deactivate' : 'Activate'}</button>
              {#if !u.oauth_provider}<button type="button" class="sm" onclick={() => resetPassword(u)}>Reset password</button>{/if}
              <button type="button" class="sm" onclick={() => toggleAdmin(u)}>{u.is_superuser ? 'Remove admin' : 'Make admin'}</button>
              <button type="button" class="sm danger" onclick={() => remove(u)}>Delete</button>
            {:else}
              <span class="muted">you</span>
            {/if}
          </div></td>
        </tr>
      {:else}
        <tr><td colspan="6">{loading ? 'Loading…' : 'No users found.'}</td></tr>
      {/each}
    </tbody>
  </table>
  </div>
</section>

<style>
  .admin {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }

  .toolbar {
    display: flex;
    gap: var(--space-2);
    align-items: center;
    flex-wrap: wrap;
  }

  .toolbar input {
    flex: 1;
    min-width: 12rem;
    max-width: 22rem;
  }

  .count {
    color: var(--text-muted);
    margin-left: auto;
    font-size: var(--text-sm);
  }

  .table-wrap {
    overflow-x: auto;
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--text-sm);
    min-width: 40rem;
  }

  th {
    background: var(--surface-2);
    color: var(--text-muted);
    font-size: var(--text-xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  th,
  td {
    text-align: left;
    padding: var(--space-3);
    border-bottom: 1px solid var(--border);
    vertical-align: middle;
  }

  tbody tr:last-child td {
    border-bottom: 0;
  }

  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-1);
  }

  .temp {
    flex-wrap: wrap;
    align-items: center;
  }
</style>
