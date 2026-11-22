<script lang="ts">
  import { onMount } from 'svelte';

  interface Props {
    loading?: boolean;
    error?: string;
    onlogin?: (event: CustomEvent<{ username: string; password: string }>) => void;
    onregister?: (event: CustomEvent<{ username: string; email: string; password: string }>) => void;
  }

  let { loading = false, error = '', onlogin, onregister }: Props = $props();

  let username = $state('');
  let email = $state('');
  let password = $state('');
  let oauthProviders = $state<string[]>([]);
  let isRegisterMode = $state(false);

  onMount(async () => {
    try {
      const response = await fetch('/oauth/providers');
      if (response.ok) {
        const data = await response.json();
        oauthProviders = data.providers || [];
      }
    } catch {
      // OAuth providers not available
    }
  });

  function handleSubmit(event: Event) {
    event.preventDefault();

    if (isRegisterMode && onregister) {
      const customEvent = new CustomEvent('register', {
        detail: { username, email, password },
      });
      onregister(customEvent);
      return;
    }

    if (onlogin) {
      const customEvent = new CustomEvent('login', {
        detail: { username, password },
      });
      onlogin(customEvent);
    }
  }

  function toggleMode() {
    isRegisterMode = !isRegisterMode;
  }

  function handleOAuthLogin(provider: string) {
    window.location.href = `/oauth/login/${provider}`;
  }

  function getProviderIcon(provider: string): string {
    const icons: Record<string, string> = {
      google: '🔍',
      github: '🐙',
      microsoft: '🪟',
    };
    return icons[provider] || '🔐';
  }

  function getProviderName(provider: string): string {
    return provider.charAt(0).toUpperCase() + provider.slice(1);
  }
</script>

<div class="login-container">
  <div class="login-card">
    <h2>{isRegisterMode ? 'Create Account' : 'Sign In'}</h2>
    <p class="subtitle">Access your transcription workspace</p>

    {#if error}
      <div class="notice error error-message" role="alert">
        {error}
      </div>
    {/if}

    {#if oauthProviders.length > 0}
      <div class="oauth-section">
        <div class="oauth-buttons">
          {#each oauthProviders as provider}
            <button type="button" onclick={() => handleOAuthLogin(provider)} class="oauth-button">
              <span class="oauth-icon">{getProviderIcon(provider)}</span>
              Continue with {getProviderName(provider)}
            </button>
          {/each}
        </div>
        <div class="divider">
          <span>or</span>
        </div>
      </div>
    {/if}

    <form onsubmit={handleSubmit}>
      <div class="form-group">
        <label for="username">Username</label>
        <input id="username" type="text" bind:value={username} disabled={loading} required />
      </div>

      {#if isRegisterMode}
        <div class="form-group">
          <label for="email">Email</label>
          <input id="email" type="email" bind:value={email} disabled={loading} required />
        </div>
      {/if}

      <div class="form-group">
        <label for="password">Password</label>
        <input id="password" type="password" bind:value={password} disabled={loading} required />
      </div>

      <button type="submit" disabled={loading} class="primary lg submit-button">
        {#if loading}
          {isRegisterMode ? 'Creating Account...' : 'Signing In...'}
        {:else}
          {isRegisterMode ? 'Create Account' : 'Sign In'}
        {/if}
      </button>
    </form>

    {#if onregister}
    <div class="register-link">
      <span>{isRegisterMode ? 'Already have an account?' : "Don't have an account?"}</span>
      <button type="button" onclick={toggleMode} class="link link-button">
        {isRegisterMode ? 'Sign In' : 'Register'}
      </button>
    </div>
  {/if}
  </div>
</div>

<style>
  .login-container {
    display: flex;
    justify-content: center;
    width: 100%;
  }

  .login-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow);
    padding: var(--space-6);
    max-width: 24rem;
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }

  h2 {
    font-size: var(--text-xl);
    text-align: center;
  }

  .subtitle {
    color: var(--text-muted);
    text-align: center;
    font-size: var(--text-sm);
    margin-top: calc(var(--space-3) * -1);
  }

  form {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }

  .form-group {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  label {
    font-weight: 600;
    font-size: var(--text-sm);
  }

  .submit-button {
    width: 100%;
  }

  .oauth-buttons {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }

  .oauth-button {
    width: 100%;
    min-height: 2.5rem;
  }

  .divider {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    margin-top: var(--space-4);
    color: var(--text-muted);
    font-size: var(--text-sm);
  }

  .divider::before,
  .divider::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border);
  }

  .register-link {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: var(--space-1);
    font-size: var(--text-sm);
    color: var(--text-muted);
  }
</style>
