import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, type SampleSummary } from "../api";

export function SampleTable({ samples }: { samples: SampleSummary[] }) {
  if (samples.length === 0) return <p className="muted">No samples yet.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>Name</th>
          <th>Owner</th>
          <th>Files</th>
          <th>Your access</th>
          <th>Created</th>
        </tr>
      </thead>
      <tbody>
        {samples.map((s) => (
          <tr key={s.id}>
            <td>
              <Link to={`/samples/${s.id}`}>{s.name}</Link>
            </td>
            <td>{s.owner_username}</td>
            <td>{s.file_count}</td>
            <td>
              <span className={`badge access-${s.access}`}>{s.access}</span>
            </td>
            <td>{new Date(s.created_at).toLocaleDateString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function SamplesPage() {
  const navigate = useNavigate();
  const [samples, setSamples] = useState<SampleSummary[] | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listSamples().then(setSamples).catch((e) => setError(String(e.message)));
  }, []);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const sample = await api.createSample(name.trim());
      navigate(`/samples/${sample.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create sample");
    }
  };

  return (
    <>
      <h1>My samples</h1>
      <section className="card">
        <form className="inline" onSubmit={create}>
          <input
            aria-label="New sample name"
            placeholder="New sample name, e.g. TCGA-BRCA-0001"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={200}
            required
          />
          <button type="submit">Register sample</button>
        </form>
        {error && <p className="error">{error}</p>}
      </section>
      <section className="card">
        {samples === null ? <p className="muted">Loading…</p> : <SampleTable samples={samples} />}
      </section>
    </>
  );
}
