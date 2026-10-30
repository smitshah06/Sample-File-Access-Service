import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type Role, type User } from "../../api";
import { useAuth } from "../../auth";

export function UsersPage() {
  const { me } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [form, setForm] = useState({ username: "", password: "", role: "user" as Role });
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.listUsers().then(setUsers).catch((e) => setError(String(e.message)));
  }, []);

  useEffect(load, [load]);

  const run = async (action: () => Promise<unknown>) => {
    setError(null);
    try {
      await action();
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed");
    }
  };

  const create = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      await api.createUser(form.username.trim(), form.password, form.role);
      setForm({ username: "", password: "", role: "user" });
    });
  };

  return (
    <>
      <h1>Users</h1>
      <section className="card">
        <h2>Create user</h2>
        <form className="inline" onSubmit={create}>
          <input
            aria-label="Username"
            placeholder="username"
            value={form.username}
            onChange={(e) => setForm({ ...form, username: e.target.value })}
            required
          />
          <input
            aria-label="Initial password"
            type="password"
            placeholder="initial password (12+ characters)"
            minLength={12}
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            autoComplete="new-password"
            required
          />
          <select
            aria-label="Role"
            value={form.role}
            onChange={(e) => setForm({ ...form, role: e.target.value as Role })}
          >
            <option value="user">user</option>
            <option value="admin">admin</option>
          </select>
          <button type="submit">Create</button>
        </form>
        {error && <p className="error">{error}</p>}
      </section>

      <section className="card">
        <table>
          <thead>
            <tr>
              <th>Username</th>
              <th>Role</th>
              <th>Status</th>
              <th>Created</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const self = u.id === me?.id;
              return (
                <tr key={u.id} className={u.is_active ? "" : "inactive"}>
                  <td>
                    {u.username}
                    {self && <span className="muted"> (you)</span>}
                  </td>
                  <td>
                    <select
                      aria-label={`Role for ${u.username}`}
                      value={u.role}
                      disabled={self}
                      onChange={(e) => run(() => api.updateUser(u.id, { role: e.target.value as Role }))}
                    >
                      <option value="user">user</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  <td>{u.is_active ? "active" : "deactivated"}</td>
                  <td>{new Date(u.created_at).toLocaleDateString()}</td>
                  <td className="actions">
                    {!self && (
                      <button
                        className={u.is_active ? "danger" : ""}
                        onClick={() => run(() => api.updateUser(u.id, { is_active: !u.is_active }))}
                      >
                        {u.is_active ? "Deactivate" : "Reactivate"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="muted small">
          Changing a role or deactivating a user signs them out everywhere immediately.
        </p>
      </section>
    </>
  );
}
