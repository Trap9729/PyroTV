// Resumable chunked upload client. The server is the source of truth for how
// many bytes it has ("received"); after any hiccup we ask it and carry on from there.

export class UploadError extends Error {
  constructor(message, { status = 0, detail = null, uploadId = null } = {}) {
    super(message);
    this.name = "UploadError";
    this.status = status;
    this.detail = detail;
    this.uploadId = uploadId; // set once the server knows the upload: lets the caller resume
  }
}

const abortError = (signal) => signal?.reason ?? new DOMException("Upload aborted", "AbortError");

const defaultSleep = (ms, signal) =>
  new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(abortError(signal));
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError(signal));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });

async function readJson(res) {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

const detailOf = (body) => (body && typeof body === "object" ? (body.detail ?? null) : null);

export async function uploadFile(
  file,
  kind,
  {
    onProgress,
    onRetry,
    onUploadId,
    resumeId,
    chunkSize = 4 * 1024 * 1024,
    fetchFn = (...a) => fetch(...a),
    sleepFn,
    signal,
    maxRetries = 5,
    backoffMs = 1000,
  } = {},
) {
  const sleep = sleepFn ?? ((ms) => defaultSleep(ms, signal));
  const checkAbort = () => {
    if (signal?.aborted) throw abortError(signal);
  };
  const send = async (url, init) => {
    checkAbort();
    try {
      return await fetchFn(url, { ...init, signal });
    } catch (err) {
      checkAbort();
      throw err; // network failure: the caller decides whether to retry
    }
  };
  const size = file.size;
  let id = null;

  const create = async () => {
    try {
      const res = await send("/api/uploads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, filename: file.name, size }),
      });
      const body = await readJson(res);
      if (!res.ok) throw new UploadError(`HTTP ${res.status}`, { status: res.status, detail: detailOf(body) });
      return body;
    } catch (err) {
      if (err instanceof UploadError || err?.name === "AbortError") throw err;
      throw new UploadError("network", { status: 0 });
    }
  };

  // 1. announce a new upload, or pick up an unfinished one.
  checkAbort();
  let created = null;
  if (resumeId) {
    try {
      const res = await send(`/api/uploads/${encodeURIComponent(resumeId)}`, { method: "GET" });
      const body = await readJson(res);
      if (res.ok && Number.isFinite(body?.received) && (body.size === undefined || body.size === size)) {
        created = { id: resumeId, received: body.received };
      } else if (res.status !== 404 && res.status !== 200) {
        throw new UploadError(`HTTP ${res.status}`, { status: res.status, detail: detailOf(body), uploadId: resumeId });
      } // 404 (gone) or another file's upload: start afresh below
    } catch (err) {
      if (err instanceof UploadError || err?.name === "AbortError") throw err;
      throw new UploadError("network", { status: 0, uploadId: resumeId });
    }
  }
  if (!created) created = await create();

  id = created.id;
  onUploadId?.(id);
  const base = `/api/uploads/${encodeURIComponent(id)}`;
  let received = Number(created.received) || 0;
  let failures = 0; // consecutive failed retry cycles

  const report = () => {
    if (onProgress) onProgress(size > 0 ? Math.min(1, received / size) : 1);
  };
  if (received > 0) report();

  // One strike per retry cycle (failed request + the status check that follows).
  const fail = (status, detail) => {
    failures++;
    if (failures >= maxRetries) {
      throw new UploadError(status ? `HTTP ${status}` : "network", { status, detail, uploadId: id });
    }
    onRetry?.(failures);
  };
  // 1 s, 2 s, 4 s, ... (backoffMs is the base).
  const backoff = () => sleep(backoffMs * 2 ** (failures - 1));

  // Ask the server where it is. Not a strike of its own: if the network is
  // still down the next request fails and counts then.
  const resync = async () => {
    await backoff();
    try {
      const res = await send(base, { method: "GET" });
      const body = await readJson(res);
      if (res.status === 404) throw new UploadError("HTTP 404", { status: 404, detail: detailOf(body), uploadId: id });
      if (res.ok && Number.isFinite(body?.received)) received = body.received;
    } catch (err) {
      if (err instanceof UploadError || err?.name === "AbortError") throw err;
    }
  };

  // 2. chunks, then 3. finish; any hiccup loops back here.
  for (;;) {
    checkAbort();
    if (received < size) {
      const end = Math.min(size, received + chunkSize);
      let res;
      try {
        res = await send(`${base}?offset=${received}`, {
          method: "PUT",
          headers: { "Content-Type": "application/octet-stream" },
          body: file.slice(received, end),
        });
      } catch (err) {
        if (err?.name === "AbortError") throw err;
        fail(0, null);
        await resync();
        continue;
      }
      const body = await readJson(res);
      if (res.ok) {
        received = Number(body?.received);
        failures = 0;
        report();
      } else if (res.status === 409 && Number.isFinite(body?.received)) {
        fail(409, detailOf(body));
        received = body.received;
      } else if (res.status >= 500) {
        fail(res.status, detailOf(body));
        await resync();
      } else {
        throw new UploadError(`HTTP ${res.status}`, { status: res.status, detail: detailOf(body), uploadId: id });
      }
      continue;
    }

    let res;
    try {
      res = await send(`${base}/finish`, { method: "POST" });
    } catch (err) {
      if (err?.name === "AbortError") throw err;
      fail(0, null);
      await backoff();
      continue;
    }
    const body = await readJson(res);
    if (res.ok) return id;
    if (res.status === 409) {
      fail(409, detailOf(body));
      await resync();
    } else if (res.status >= 500) {
      fail(res.status, detailOf(body));
      await backoff();
    } else {
      throw new UploadError(`HTTP ${res.status}`, { status: res.status, detail: detailOf(body), uploadId: id });
    }
  }
}
