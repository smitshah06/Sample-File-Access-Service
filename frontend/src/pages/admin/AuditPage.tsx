import { useEffect, useMemo, useState } from "react";
import { api, type AuditEvent, type User } from "../../api";

const EVENTS = [
  "",
  "login_succeeded",
  "login_failed",
  "account_locked",
  "user_created",
  "user_updated",
  "sample_registered",
  "sample_deleted",
  "file_uploaded",
  "file_deleted",
  "access_granted",
  "access_revoked",
  "qc_status_changed",
  "qc_transition_rejected",
  "download_url_issued",
  "download_served",
  "download_denied",
  "download_link_rejected",
];

export function AuditPage() {
  const [event, setEvent] = useState("");
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listUsers().then(setUsers).catch(() => setUsers([]));
  }, []);

  useEffect(() => {
    api
      .listAudit(event || undefined)
      .then(setEvents)
      .catch((e) => setError(String(e.message)));
  }, [event]);

  // The log stores IDs only; resolve user IDs to names for display.
  const names = useMemo(() => new Map(users.map((u) => [u.id, u.username])), [users]);
  const who = (id: string | null) => (id ? (names.get(id) ?? id.slice(0, 8)) : "system");

  return (
    <>
      <h1>Audit log</h1>
      <section className="card">
        <label className="inline">
          Event
          <select value={event} onChange={(e) => setEvent(e.target.value)}>
            {EVENTS.map((ev) => (
              <option key={ev} value={ev}>
                {ev || "all events"}
              </option>
            ))}
          </select>
        </label>
        {error && <p className="error">{error}</p>}
        <table className="audit">
          <thead>
            <tr>
              <th>Time</th>
              <th>Event</th>
              <th>Actor</th>
              <th>Sample / file</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id}>
                <td>{new Date(e.ts).toLocaleString()}</td>
                <td>{e.event}</td>
                <td>{who(e.actor_id)}</td>
                <td className="mono">
                  {e.sample_id?.slice(0, 8)}
                  {e.file_id && ` / ${e.file_id.slice(0, 8)}`}
                </td>
                <td className="mono">
                  {Object.entries(e.details)
                    .filter(([, v]) => v !== null)
                    .map(([k, v]) =>
                      `${k}=${k.endsWith("_id") && typeof v === "string" ? who(v) : String(v)}`,
                    )
                    .join(" ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
