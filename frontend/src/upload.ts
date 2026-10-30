// Resumable, chunked upload of a file of any type and size (up to the server's cap).
//
// The file is sent as a series of raw chunks (16 MB by default). After each chunk the server
// reports how many bytes it holds, so:
// - a dropped connection is retried from the last stored byte, not from zero
// - choosing the same file again later (even after a page reload) resumes it
// - progress is exact
import { api, ApiError, type FileInfo, type UploadInfo } from "./api";

const MAX_RETRIES = 5;

/** Abort reason for leaving the page: stop sending, but keep the upload resumable. */
export const NAVIGATED_AWAY = "navigated away";

export interface Progress {
  sent: number;
  total: number;
}

function resumeKey(sampleId: string, file: File): string {
  return `upload:${sampleId}:${file.name}:${file.size}:${file.lastModified}`;
}

function storedUploadId(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null; // storage can be unavailable (private mode); resuming is then just off
  }
}

function remember(key: string, id: string | null): void {
  try {
    if (id) localStorage.setItem(key, id);
    else localStorage.removeItem(key);
  } catch {
    // ignore: see storedUploadId
  }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** A failure worth retrying: network errors, server errors, or an offset we can resync. */
function retryable(err: unknown): boolean {
  if (!(err instanceof ApiError)) return true; // fetch threw: network problem
  return err.status >= 500 || err.code === "offset_mismatch";
}

export async function uploadFile(
  sampleId: string,
  file: File,
  onProgress: (p: Progress) => void,
  signal: AbortSignal,
): Promise<FileInfo> {
  const key = resumeKey(sampleId, file);
  let upload: UploadInfo | null = null;

  const previous = storedUploadId(key);
  if (previous) {
    upload = await api.getUpload(previous).catch(() => null); // expired or gone: start over
  }
  if (!upload) {
    upload = await api.startUpload(sampleId, file);
    remember(key, upload.id);
  }

  const id = upload.id;
  let offset = upload.received_bytes;
  let failures = 0;
  onProgress({ sent: offset, total: file.size });

  try {
    while (offset < file.size) {
      const end = Math.min(offset + upload.chunk_bytes, file.size);
      try {
        // file.slice() is lazy: only this chunk is read from disk into memory.
        upload = await api.putChunk(id, offset, file.slice(offset, end), signal);
        offset = upload.received_bytes;
        failures = 0;
        onProgress({ sent: offset, total: file.size });
      } catch (err) {
        if (signal.aborted || !retryable(err) || ++failures > MAX_RETRIES) throw err;
        await sleep(Math.min(1000 * 2 ** (failures - 1), 15000));
        try {
          upload = await api.getUpload(id); // resync: continue from what the server has
          offset = upload.received_bytes;
        } catch {
          // still offline: the next attempt retries the same chunk
        }
      }
    }
    const created = await api.completeUpload(id);
    remember(key, null);
    return created;
  } catch (err) {
    // Only an explicit cancel discards the upload; leaving the page keeps it resumable.
    if (signal.aborted && signal.reason !== NAVIGATED_AWAY) {
      await api.abortUpload(id).catch(() => undefined);
      remember(key, null);
    }
    throw err;
  }
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}
