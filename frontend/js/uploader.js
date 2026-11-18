// Resumable chunked upload client. The server is the source of truth for how
// many bytes it has ("received"); after any hiccup we ask it and carry on from there.

export class UploadError extends Error {
  constructor(message, { status = 0, detail = null } = {}) {
    super(message);
    this.name = "UploadError";
    this.status = status;
    this.detail = detail;
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

  // 1. announce
  checkAbort();
  let created;
  try {
    const res = await send("/api/uploads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, filename: file.name, size: file.size }),
    });
    const body = await readJson(res);
    if (!res.ok) throw new UploadError(`HTTP ${res.status}`, { status: res.status, detail: detailOf(body) });
    created = body;
  } catch (err) {
    if (err instanceof UploadError || err?.name === "AbortError") throw err;
    throw new UploadError("network", { status: 0 });
  }

  const id = created.id;
  const base = `/api/uploads/${encodeURIComponent(id)}`;
  const size = file.size;
  let received = Number(created.received) || 0;
  let failures = 0;

  const report = () => {
    if (onProgress) onProgress(size > 0 ? Math.min(1, received / size) : 1);
  };
  if (received > 0) report();

  // A transient failure: count it, give up after maxRetries in a row.
  const fail = (status, detail) => {
    failures++;
    if (onRetry && failures < maxRetries) onRetry(failures);
    if (failures >= maxRetries) {
      throw new UploadError(status ? `HTTP ${status}` : "network", { status, detail });
    }
  };

  // Ask the server where it is; a failure here simply counts as another strike.
  const resync = async () => {
    await sleep(backoffMs);
    try {
      const res = await send(base, { method: "GET" });
      const body = await readJson(res);
      if (res.status === 404) throw new UploadError("HTTP 404", { status: 404, detail: detailOf(body) });
      if (res.ok && Number.isFinite(body?.received)) received = body.received;
      else fail(res.status, detailOf(body));
    } catch (err) {
      if (err instanceof UploadError || err?.name === "AbortError") throw err;
      fail(0, null);
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
        throw new UploadError(`HTTP ${res.status}`, { status: res.status, detail: detailOf(body) });
      }
      continue;
    }

    let res;
    try {
      res = await send(`${base}/finish`, { method: "POST" });
    } catch (err) {
      if (err?.name === "AbortError") throw err;
      fail(0, null);
      await sleep(backoffMs);
      continue;
    }
    const body = await readJson(res);
    if (res.ok) return id;
    if (res.status === 409) {
      fail(409, detailOf(body));
      await resync();
    } else if (res.status >= 500) {
      fail(res.status, detailOf(body));
      await sleep(backoffMs);
    } else {
      throw new UploadError(`HTTP ${res.status}`, { status: res.status, detail: detailOf(body) });
    }
  }
}
