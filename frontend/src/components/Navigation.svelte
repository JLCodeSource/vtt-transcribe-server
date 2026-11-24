<script lang="ts">
  interface Props {
    currentPage?: string;
    onnavigate?: (page: string) => void;
    showAdmin?: boolean;
  }

  let { currentPage = 'home', onnavigate, showAdmin = false }: Props = $props();

  let open = $state(false);

  const baseItems = [
    { id: 'home', label: 'Home', icon: '🏠' },
    { id: 'jobs', label: 'Jobs', icon: '📋' },
    { id: 'profile', label: 'Profile', icon: '👤' },
    { id: 'about', label: 'About', icon: 'ℹ️' },
  ];
  const menuItems = $derived(
    showAdmin ? [...baseItems, { id: 'admin', label: 'Admin', icon: '🛡️' }] : baseItems
  );

  function handleNavigate(page: string) {
    open = false;
    if (onnavigate) {
      onnavigate(page);
    }
  }
</script>

<nav class="navigation" class:open aria-label="Main">
  <div class="nav-header">
    <h2>🎬 VTT</h2>
  </div>

  <ul class="nav-menu" id="nav-menu">
    {#each menuItems as item (item.id)}
      <li>
        <button
          class="nav-item"
          class:active={currentPage === item.id}
          aria-current={currentPage === item.id ? 'page' : undefined}
          onclick={() => handleNavigate(item.id)}
        >
          <span class="nav-icon" aria-hidden="true">{item.icon}</span>
          <span class="nav-label">{item.label}</span>
        </button>
      </li>
    {/each}
  </ul>

  <button
    type="button"
    class="nav-toggle ghost"
    aria-label="Menu"
    aria-expanded={open}
    aria-controls="nav-menu"
    onclick={() => (open = !open)}
  >
    {open ? '✕' : '☰'}
  </button>
</nav>

<style>
  .navigation {
    width: var(--nav-width);
    background: var(--surface);
    border-right: 1px solid var(--border);
    height: 100vh;
    position: fixed;
    left: 0;
    top: 0;
    display: flex;
    flex-direction: column;
    z-index: 100;
  }

  .nav-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: var(--space-4) var(--space-5);
    height: 3.75rem;
  }

  .nav-header h2 {
    font-size: var(--text-xl);
    color: var(--accent-text);
    letter-spacing: -0.01em;
  }

  .nav-toggle {
    display: none;
    position: absolute;
    top: 0.5rem;
    right: var(--space-3);
    font-size: var(--text-lg);
  }

  .nav-menu {
    list-style: none;
    padding: var(--space-2) var(--space-3);
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .nav-item {
    width: 100%;
    justify-content: flex-start;
    gap: var(--space-3);
    min-height: 2.5rem;
    padding: 0.5rem var(--space-3);
    border: 0;
    background: transparent;
    color: var(--text-muted);
    font-size: var(--text-base);
    font-weight: 500;
  }

  .nav-item:hover:not(:disabled) {
    background: var(--surface-3);
    color: var(--text);
  }

  .nav-item.active,
  .nav-item.active:hover:not(:disabled) {
    background: var(--accent-soft);
    color: var(--accent-text);
    font-weight: 650;
  }

  .nav-icon {
    width: 1.5rem;
    text-align: center;
  }

  @media (max-width: 800px) {
    .navigation {
      position: sticky;
      width: 100%;
      height: auto;
      border-right: 0;
      border-bottom: 1px solid var(--border);
    }

    .nav-header {
      height: 3.25rem;
      padding-inline: var(--space-4);
    }

    .nav-toggle {
      display: inline-flex;
    }

    .nav-menu {
      display: none;
      padding-bottom: var(--space-3);
    }

    .navigation.open .nav-menu {
      display: flex;
    }
  }
</style>
