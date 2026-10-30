import { useEffect, useState } from "react";
import { api, type SampleSummary } from "../../api";
import { SampleTable } from "../SamplesPage";

export function AdminSamplesPage() {
  const [samples, setSamples] = useState<SampleSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listAllSamples().then(setSamples).catch((e) => setError(String(e.message)));
  }, []);

  return (
    <>
      <h1>All samples</h1>
      <p className="muted">Open a sample to grant or revoke access for any user.</p>
      <section className="card">
        {error && <p className="error">{error}</p>}
        {samples === null ? <p className="muted">Loading…</p> : <SampleTable samples={samples} />}
      </section>
    </>
  );
}
