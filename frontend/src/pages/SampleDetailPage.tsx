import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError, type Sample } from "../api";
import { useAuth } from "../auth";
import { FileRow } from "../components/FileRow";
import { SharingPanel } from "../components/SharingPanel";
import { formatBytes, NAVIGATED_AWAY, uploadFile, type Progress } from "../upload";

/** One component instance per sample, so moving between samples never carries an upload
 * (or stale data) from one sample to another. */
export function SampleDetailRoute() {
  const { id = "" } = useParams();
  return <SampleDetailPage key={id} />;
}

export function SampleDetailPage() {
  const { id = "" } = useParams();
  const { me } = useAuth();
  const navigate = useNavigate();
  const [sample, setSample] = useState<Sample | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<(Progress & { name: string }) | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const cancel = useRef<AbortController | null>(null);

  // Leaving the page stops sending; choosing the same file later resumes where it stopped.
  useEffect(() => () => cancel.current?.abort(NAVIGATED_AWAY), []);

  const load = useCallback(() => {
    api
      .getSample(id)
      .then(setSample)
      .catch((e) => setError(e instanceof ApiError && e.status === 404 ? "Sample not found." : e.message));
  }, [id]);

  useEffect(load, [load]);

  if (error && !sample) return <p className="error">{error}</p>;
  if (!sample) return <p className="muted">Loading…</p>;

  const isOwner = sample.access === "owner";
  const canManageSharing = isOwner || me?.role === "admin";

  const upload = async (e: FormEvent) => {
    e.preventDefault();
    const file = fileInput.current?.files?.[0];
    if (!file) return;
    setError(null);
    const controller = new AbortController();
    cancel.current = controller;
    setProgress({ name: file.name, sent: 0, total: file.size });
    try {
      await uploadFile(sample.id, file, (p) => setProgress({ name: file.name, ...p }), controller.signal);
      if (fileInput.current) fileInput.current.value = "";
      load();
    } catch (err) {
      if (controller.signal.reason === NAVIGATED_AWAY) return;
      if (controller.signal.aborted) setError("Upload cancelled.");
      else if (err instanceof ApiError) setError(err.message);
      else setError("Upload interrupted. Choose the same file again to resume.");
    } finally {
      cancel.current = null;
      setProgress(null);
    }
  };

  const deleteFile = async (fileId: string, filename: string) => {
    if (!window.confirm(`Delete ${filename}? This cannot be undone.`)) return;
    try {
      await api.deleteFile(fileId);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Delete failed");
    }
  };

  const deleteSample = async () => {
    if (!window.confirm(`Delete sample ${sample.name} and all its files?`)) return;
    try {
      await api.deleteSample(sample.id);
      navigate("/samples");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Delete failed");
    }
  };

  return (
    <>
      <h1>{sample.name}</h1>
      <p className="muted">
        Owner: {sample.owner_username} · your access:{" "}
        <span className={`badge access-${sample.access}`}>{sample.access}</span>
      </p>
      {sample.access === "none" && (
        <p className="notice">
          As an admin you can manage who has access to this sample, but you can't download its
          files unless you grant yourself access (which is recorded in the audit log).
        </p>
      )}

      <section className="card">
        <h2>Files</h2>
        {sample.files.length === 0 ? (
          <p className="muted">No files yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>File</th>
                <th>Size</th>
                <th>QC</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sample.files.map((f) => (
                <FileRow
                  key={f.id}
                  file={f}
                  access={sample.access}
                  onDelete={isOwner ? () => deleteFile(f.id, f.filename) : undefined}
                />
              ))}
            </tbody>
          </table>
        )}
        {isOwner && (
          <form className="inline" onSubmit={upload}>
            <input
              type="file"
              ref={fileInput}
              aria-label="File to upload"
              required
              disabled={progress !== null}
            />
            <button type="submit" disabled={progress !== null}>
              Upload
            </button>
          </form>
        )}
        {progress && (
          <div className="upload-progress" role="status">
            <progress value={progress.sent} max={progress.total || 1} />
            <span className="small">
              {progress.name}: {formatBytes(progress.sent)} of {formatBytes(progress.total)} (
              {progress.total ? Math.floor((100 * progress.sent) / progress.total) : 100}%)
              {progress.sent === progress.total && " · verifying checksum…"}
            </span>
            <button className="danger" type="button" onClick={() => cancel.current?.abort()}>
              Cancel
            </button>
          </div>
        )}
        <p className="muted small">
          Any file type, up to 10 GB by default. Large files are sent in chunks; if an upload is interrupted,
          choose the same file again to resume. New files start as <b>pending</b> and can be
          downloaded once the QC pipeline marks them <b>passed</b>.
        </p>
        {error && <p className="error">{error}</p>}
      </section>

      {canManageSharing && <SharingPanel sampleId={sample.id} />}

      {isOwner && (
        <section className="card danger-zone">
          <button className="danger" onClick={deleteSample}>
            Delete sample
          </button>
        </section>
      )}
    </>
  );
}
