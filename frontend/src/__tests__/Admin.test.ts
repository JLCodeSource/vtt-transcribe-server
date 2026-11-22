import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import Admin from '../components/Admin.svelte';
import ChangePassword from '../components/ChangePassword.svelte';
import Navigation from '../components/Navigation.svelte';

const user = (over: Record<string, unknown> = {}) => ({
  id: 2,
  username: 'alice',
  email: 'alice@example.com',
  is_active: true,
  is_superuser: false,
  must_change_password: false,
  oauth_provider: null,
  created_at: '2026-11-20T10:00:00Z',
  job_count: 3,
  ...over,
});

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
const calls = () => (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls as Array<[string, RequestInit?]>;

describe('Admin', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
    vi.stubGlobal('confirm', vi.fn(() => true));
  });

  it('lists users with the bearer token and hides actions on your own row', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      ok({ total: 2, users: [user(), user({ id: 1, username: 'admin', email: 'admin@example.com', is_superuser: true })] }),
    );
    render(Admin, { token: 'tok', currentUsername: 'admin' });
    await screen.findByText('alice@example.com');
    expect(calls()[0][0]).toContain('/admin/users');
    expect((calls()[0][1]?.headers as Record<string, string>).Authorization).toBe('Bearer tok');
    expect(screen.getAllByText('Deactivate')).toHaveLength(1); // not for "admin" (you)
    expect(screen.getByText('you')).toBeTruthy();
  });

  it('deactivates a user and reloads', async () => {
    const f = fetch as ReturnType<typeof vi.fn>;
    f.mockResolvedValueOnce(ok({ total: 1, users: [user()] }))
      .mockResolvedValueOnce(ok(user({ is_active: false })))
      .mockResolvedValueOnce(ok({ total: 1, users: [user({ is_active: false })] }));
    render(Admin, { token: 'tok', currentUsername: 'admin' });
    await fireEvent.click(await screen.findByText('Deactivate'));
    await screen.findByText('Activate');
    expect(calls()[1][0]).toBe('/admin/users/2/deactivate');
    expect(calls()[1][1]?.method).toBe('POST');
  });

  it('shows a temporary password once after a reset', async () => {
    const f = fetch as ReturnType<typeof vi.fn>;
    f.mockResolvedValueOnce(ok({ total: 1, users: [user()] }))
      .mockResolvedValueOnce(ok({ user_id: 2, temporary_password: 'tmp-secret-123' }))
      .mockResolvedValueOnce(ok({ total: 1, users: [user({ must_change_password: true })] }));
    render(Admin, { token: 'tok', currentUsername: 'admin' });
    await fireEvent.click(await screen.findByText('Reset password'));
    await screen.findByText('tmp-secret-123');
    expect(screen.getByText('must reset')).toBeTruthy();
    await fireEvent.click(screen.getByText('Dismiss'));
    expect(screen.queryByText('tmp-secret-123')).toBeNull();
  });

  it('deletes only after confirmation', async () => {
    const f = fetch as ReturnType<typeof vi.fn>;
    f.mockResolvedValue(ok({ total: 1, users: [user()] }));
    (confirm as unknown as ReturnType<typeof vi.fn>).mockReturnValueOnce(false);
    render(Admin, { token: 'tok', currentUsername: 'admin' });
    await fireEvent.click(await screen.findByText('Delete'));
    expect(calls().some(([, init]) => init?.method === 'DELETE')).toBe(false);

    f.mockResolvedValueOnce({ ok: true, status: 204, json: async () => ({}) });
    await fireEvent.click(screen.getByText('Delete'));
    await waitFor(() => expect(calls().some(([u, init]) => u === '/admin/users/2' && init?.method === 'DELETE')).toBe(true));
  });

  it('shows the server error when an action is refused', async () => {
    const f = fetch as ReturnType<typeof vi.fn>;
    f.mockResolvedValueOnce(ok({ total: 1, users: [user()] })).mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => ({ detail: 'Cannot delete the last active administrator' }),
    });
    (confirm as unknown as ReturnType<typeof vi.fn>).mockReturnValue(true);
    render(Admin, { token: 'tok', currentUsername: 'admin' });
    await fireEvent.click(await screen.findByText('Delete'));
    await screen.findByText('Cannot delete the last active administrator');
  });
});

describe('ChangePassword', () => {
  it('rejects short or mismatched passwords before calling the server', async () => {
    const onsubmit = vi.fn(async () => {});
    render(ChangePassword, { forced: true, onsubmit });
    const [current, next, again] = screen.getAllByLabelText(/password/i) as HTMLInputElement[];
    await fireEvent.input(current, { target: { value: 'temporary1' } });
    await fireEvent.input(next, { target: { value: 'longenough1' } });
    await fireEvent.input(again, { target: { value: 'different12' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Change password' }));
    await screen.findByText('The new passwords do not match.');
    expect(onsubmit).not.toHaveBeenCalled();
  });

  it('submits matching passwords', async () => {
    const onsubmit = vi.fn(async () => {});
    render(ChangePassword, { forced: true, onsubmit });
    const [current, next, again] = screen.getAllByLabelText(/password/i) as HTMLInputElement[];
    await fireEvent.input(current, { target: { value: 'temporary1' } });
    await fireEvent.input(next, { target: { value: 'longenough1' } });
    await fireEvent.input(again, { target: { value: 'longenough1' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Change password' }));
    await waitFor(() => expect(onsubmit).toHaveBeenCalledWith('temporary1', 'longenough1'));
  });
});

describe('Navigation admin entry', () => {
  it('only appears for admins', () => {
    const { unmount } = render(Navigation, { showAdmin: false });
    expect(screen.queryByText('Admin')).toBeNull();
    unmount();
    render(Navigation, { showAdmin: true });
    expect(screen.getByText('Admin')).toBeTruthy();
  });
});
