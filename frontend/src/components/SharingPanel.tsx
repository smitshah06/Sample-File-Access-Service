import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type Grant } from "../api";

export function SharingPanel({ sampleId }: { sampleId: string }) {
  const [grants, setGrants] = useState<Grant[]>([]);
  const [username, setUsername] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.listGrants(sampleId).then(setGrants).catch((e) => setError(String(e.message)));
  }, [sampleId]);

  useEffect(load, [load]);

  const grant = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await api.grant(sampleId, username.trim());
      setUsername("");
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not grant access");
    }
  };

  const revoke = async (g: Grant) => {
    if (!window.confirm(`Revoke ${g.username}'s access? Links they already have stop working.`)) {
      return;
    }
    setError(null);
    try {
      await api.revoke(sampleId, g.user_id);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not revoke access");
    }
  };

  return (
    <section className="card">
      <h2>Sharing</h2>
      {grants.length === 0 ? (
        <p className="muted">Only the owner has access.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>User</th>
              <th>Granted</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {grants.map((g) => (
              <tr key={g.user_id}>
                <td>{g.username}</td>
                <td>{new Date(g.granted_at).toLocaleString()}</td>
                <td className="actions">
                  <button className="danger" onClick={() => revoke(g)}>
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form className="inline" onSubmit={grant}>
        <input
          aria-label="Username to grant"
          placeholder="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
        />
        <button type="submit">Grant access</button>
      </form>
      {error && <p className="error">{error}</p>}
    </section>
  );
}
