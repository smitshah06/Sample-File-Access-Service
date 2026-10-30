import { useState } from "react";
import { api, ApiError, type Access, type FileInfo } from "../api";
import { formatBytes } from "../upload";

export function QcBadge({ status }: { status: FileInfo["qc_status"] }) {
  return <span className={`badge qc-${status}`}>{status}</span>;
}

function formatSize(bytes: number | null): string {
  return bytes === null ? "metadata only" : formatBytes(bytes);
}

interface Props {
  file: FileInfo;
  access: Access;
  onDelete?: () => void;
}

export function FileRow({ file, access, onDelete }: Props) {
  const [error, setError] = useState<string | null>(null);
  const canDownload = access !== "none" && file.qc_status === "passed";
  const why =
    access === "none"
      ? "You have no access to this sample's files"
      : file.qc_status === "pending"
        ? "Waiting for QC"
        : file.qc_status === "failed"
          ? "QC failed: this file can't be downloaded"
          : undefined;

  const download = async () => {
    setError(null);
    try {
      const { url } = await api.downloadUrl(file.id);
      window.location.assign(url); // a short-lived signed URL; the server streams the file
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Download failed");
    }
  };

  return (
    <tr>
      <td>
        {file.filename}
        {file.content_type && <div className="muted small">{file.content_type}</div>}
        <div className="muted small mono" title="File ID">
          {file.id}
        </div>
        {error && <div className="error small">{error}</div>}
      </td>
      <td>{formatSize(file.size_bytes)}</td>
      <td>
        <QcBadge status={file.qc_status} />
      </td>
      <td className="actions">
        <button onClick={download} disabled={!canDownload} title={why}>
          Download
        </button>
        {onDelete && (
          <button className="danger" onClick={onDelete}>
            Delete
          </button>
        )}
      </td>
    </tr>
  );
}
