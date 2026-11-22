<script lang="ts">
  interface Props {
    isLoggedIn?: boolean;
    username?: string;
    onlogout?: () => void;
    onsettings?: () => void;
  }

  let { 
    isLoggedIn = false, 
    username = 'Guest',
    onlogout,
    onsettings
  }: Props = $props();

  let menuOpen = $state(false);

  function toggleMenu() {
    menuOpen = !menuOpen;
  }

  function handleSettings() {
    menuOpen = false;
    if (onsettings) {
      onsettings();
    }
  }

  function handleLogout() {
    menuOpen = false;
    if (onlogout) {
      onlogout();
    }
  }

  // Close menu when clicking outside
  function handleClickOutside(event: MouseEvent) {
    const target = event.target as HTMLElement;
    if (menuOpen && !target.closest('.user-menu')) {
      menuOpen = false;
    }
  }
</script>

<svelte:window onclick={handleClickOutside} />

<div class="user-menu">
  <button class="user-button" onclick={toggleMenu} aria-label="User menu">
    <span class="user-avatar">{isLoggedIn && username.trim() ? username[0].toUpperCase() : '👤'}</span>
    <span class="user-name">{username}</span>
    <span class="chevron" class:open={menuOpen}>▼</span>
  </button>

  {#if menuOpen}
    <div class="dropdown-menu">
      {#if !isLoggedIn}
        <div class="menu-item-info">
          <p>Configure your API keys in Settings</p>
        </div>
      {/if}
      
      <button class="menu-item" onclick={handleSettings}>
        <span class="menu-icon">⚙️</span>
        <span>Settings</span>
      </button>

      {#if isLoggedIn}
        <div class="menu-divider"></div>
        <button class="menu-item" onclick={handleLogout}>
          <span class="menu-icon">🚪</span>
          <span>Logout</span>
        </button>
      {/if}
    </div>
  {/if}
</div>

<style>
  .user-menu {
    position: relative;
    display: inline-block;
  }

  .user-button {
    gap: var(--space-2);
    padding: 0.25rem 0.75rem 0.25rem 0.3rem;
    border-radius: var(--radius-pill);
    background: var(--surface);
  }

  .user-avatar {
    width: 1.75rem;
    height: 1.75rem;
    border-radius: 50%;
    background: var(--accent);
    color: var(--on-accent);
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    font-size: var(--text-sm);
  }

  .user-name {
    font-weight: 600;
    max-width: 12rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .chevron {
    font-size: 0.6rem;
    color: var(--text-muted);
    transition: transform 0.15s ease;
  }

  .chevron.open {
    transform: rotate(180deg);
  }

  .dropdown-menu {
    position: absolute;
    top: calc(100% + var(--space-2));
    right: 0;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow-lg);
    min-width: 200px;
    padding: var(--space-1);
    z-index: 1000;
  }

  .menu-item-info {
    padding: var(--space-3);
    border-bottom: 1px solid var(--border);
  }

  .menu-item-info p {
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .menu-item {
    width: 100%;
    justify-content: flex-start;
    gap: var(--space-3);
    border: 0;
    background: transparent;
    font-weight: 500;
  }

  .menu-divider {
    height: 1px;
    background: var(--border);
    margin: var(--space-1) 0;
  }
</style>
