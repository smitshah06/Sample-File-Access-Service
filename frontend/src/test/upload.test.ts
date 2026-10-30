import { ApiError, api, type UploadInfo } from "../api";
import { formatBytes, NAVIGATED_AWAY, uploadFile } from "../upload";

const CHUNK = 4;

function session(received: number, size: number): UploadInfo {
  return {
    id: "up-1",
    sample_id: "s-1",
    filename: "video.mp4",
    size_bytes: size,
    received_bytes: received,
    chunk_bytes: CHUNK,
    expires_at: "2026-10-30T00:00:00Z",
  };
}

// Fake server: tracks received bytes and records which offsets were sent.
function fakeServer(size: number, startAt = 0) {
  let received = startAt;
  const offsets: number[] = [];
  vi.spyOn(api, "startUpload").mockResolvedValue(session(0, size));
  vi.spyOn(api, "getUpload").mockImplementation(async () => session(received, size));
  vi.spyOn(api, "putChunk").mockImplementation(async (_id, offset, chunk) => {
    offsets.push(offset);
    received = offset + chunk.size;
    return session(received, size);
  });
  vi.spyOn(api, "completeUpload").mockResolvedValue({} as never);
  vi.spyOn(api, "abortUpload").mockResolvedValue(undefined);
  return { offsets };
}

const file = (size: number) => new File([new Uint8Array(size)], "video.mp4", { lastModified: 1 });

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("uploadFile", () => {
  it("sends the file in chunks and reports progress", async () => {
    const { offsets } = fakeServer(10);
    const progress: number[] = [];

    await uploadFile("s-1", file(10), (p) => progress.push(p.sent), new AbortController().signal);

    expect(offsets).toEqual([0, 4, 8]);
    expect(progress).toEqual([0, 4, 8, 10]);
    expect(api.completeUpload).toHaveBeenCalledWith("up-1");
  });

  it("resumes a previous upload of the same file from the server's offset", async () => {
    localStorage.setItem("upload:s-1:video.mp4:10:1", "up-1");
    const { offsets } = fakeServer(10, 8);

    await uploadFile("s-1", file(10), () => {}, new AbortController().signal);

    expect(api.startUpload).not.toHaveBeenCalled();
    expect(offsets).toEqual([8]);
  });

  it("retries after a network error and resyncs with the server", async () => {
    vi.useFakeTimers();
    const { offsets } = fakeServer(8);
    const put = vi.mocked(api.putChunk);
    const real = put.getMockImplementation()!;
    put.mockImplementationOnce(real).mockRejectedValueOnce(new TypeError("network down"));

    const done = uploadFile("s-1", file(8), () => {}, new AbortController().signal);
    await vi.runAllTimersAsync();
    await done;

    expect(offsets).toEqual([0, 4]); // the failed attempt at 4 is retried, nothing resent
    vi.useRealTimers();
  });

  it("keeps the upload resumable when the user navigates away, discards it on cancel", async () => {
    for (const [reason, discarded] of [[NAVIGATED_AWAY, false], ["cancelled", true]] as const) {
      vi.restoreAllMocks();
      localStorage.clear();
      fakeServer(8);
      const controller = new AbortController();
      vi.mocked(api.putChunk).mockImplementationOnce(async () => {
        controller.abort(reason);
        throw new DOMException("aborted", "AbortError");
      });

      await expect(uploadFile("s-1", file(8), () => {}, controller.signal)).rejects.toThrow();

      expect(vi.mocked(api.abortUpload).mock.calls.length > 0).toBe(discarded);
      expect(localStorage.getItem("upload:s-1:video.mp4:8:1") === null).toBe(discarded);
    }
  });

  it("does not retry a client error", async () => {
    fakeServer(8);
    vi.mocked(api.putChunk).mockRejectedValue(new ApiError(404, "upload_not_found", "gone"));

    await expect(uploadFile("s-1", file(8), () => {}, new AbortController().signal)).rejects.toThrow(
      "gone",
    );
    expect(api.putChunk).toHaveBeenCalledTimes(1);
  });
});

it("formats sizes", () => {
  expect(formatBytes(512)).toBe("512 B");
  expect(formatBytes(1536)).toBe("1.5 KB");
  expect(formatBytes(10 * 1024 ** 3)).toBe("10 GB");
});
