// Thin client for the PyroTV backend. JSON in/out; non-2xx -> ApiError.

export class ApiError extends Error {
  constructor(status, body) {
    const detail = body && typeof body === "object" ? body.detail : body;
    super(`HTTP ${status}${detail ? `: ${typeof detail === "string" ? detail : JSON.stringify(detail)}` : ""}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

const enc = encodeURIComponent;

export async function api(method, path, body, { raw = false } = {}) {
  const init = { method, headers: {} };
  if (body !== undefined && body !== null) {
    if (raw) {
      init.headers["Content-Type"] = "application/octet-stream";
      init.body = body;
    } else {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
  }
  const res = await fetch(path, init);
  if (res.status === 204) {
    if (!res.ok) throw new ApiError(res.status, null);
    return null;
  }
  const text = await res.text();
  let parsed = text;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  } else {
    parsed = null;
  }
  if (!res.ok) throw new ApiError(res.status, parsed);
  return parsed;
}

function query(params) {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") usp.append(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

// state & settings
export const getState = () => api("GET", "/api/state");
export const putSettings = (settings) => api("PUT", "/api/settings", settings);
export const setupComplete = () => api("POST", "/api/setup/complete");
export const setupReset = () => api("POST", "/api/setup/reset");

// library
export const getPieces = ({ sort, category, q } = {}) =>
  api("GET", `/api/pieces${query({ sort, category, q })}`);
export const getCategories = () => api("GET", "/api/categories");
export const getManufacturers = () => api("GET", "/api/manufacturers");
export const setPieceEnabled = (key, enabled) =>
  api("PUT", `/api/pieces/${enc(key)}/enabled`, { enabled });
export const recordView = (key) => api("POST", `/api/pieces/${enc(key)}/view`);

// media
export const thumbUrl = (key) => `/api/thumb/${enc(key)}`;
export const videoUrl = (key) => `/media/video/${enc(key)}`;

// playback
export const play = (key) => api("POST", "/api/play", { key });
export const playerSeek = (delta) => api("POST", "/api/player/seek", { delta });
export const playerPause = () => api("POST", "/api/player/pause");
export const playerStop = () => api("POST", "/api/player/stop");

// uploads
export const createUpload = ({ kind, filename, size }) =>
  api("POST", "/api/uploads", { kind, filename, size });
export const getUpload = (id) => api("GET", `/api/uploads/${enc(id)}`);
export const putUploadChunk = (id, offset, bytes) =>
  api("PUT", `/api/uploads/${enc(id)}${query({ offset })}`, bytes, { raw: true });
export const finishUpload = (id) => api("POST", `/api/uploads/${enc(id)}/finish`);

// custom pieces
export const createCustom = (body) => api("POST", "/api/custom", body);
export const getCustomJob = (jobId) => api("GET", `/api/custom/jobs/${enc(jobId)}`);
export const listCustom = () => api("GET", "/api/custom");
export const updateCustom = (id, { name, category }) =>
  api("PATCH", `/api/custom/${enc(id)}`, { name, category });
export const deleteCustom = (id) => api("DELETE", `/api/custom/${enc(id)}`);

// operations
export const getStorage = () => api("GET", "/api/storage");
export const getHotspot = () => api("GET", "/api/hotspot");
export const setHotspot = (on) => api("POST", "/api/hotspot", { on });
export const backupExport = () => api("POST", "/api/backup/export");
export const backupImport = () => api("POST", "/api/backup/import");
export const getDiagnostics = () => api("GET", "/api/diagnostics");

// Server-Sent Events with reconnect (1 s doubling to 10 s; reset once data flows).
const BACKOFF_MIN = 1000;
const BACKOFF_MAX = 10000;

export function openEvents(
  onEvent,
  {
    url = "/api/events",
    EventSourceImpl = globalThis.EventSource,
    setTimeoutFn = (fn, ms) => setTimeout(fn, ms),
    clearTimeoutFn = (h) => clearTimeout(h),
  } = {},
) {
  let es = null;
  let timer = null;
  let delay = BACKOFF_MIN;
  let closed = false;

  function connect() {
    timer = null;
    if (closed) return;
    const src = new EventSourceImpl(url);
    es = src;
    src.onopen = () => {
      delay = BACKOFF_MIN;
    };
    src.onmessage = (msg) => {
      if (src !== es) return;
      delay = BACKOFF_MIN;
      let data;
      try {
        data = JSON.parse(msg.data);
      } catch {
        return;
      }
      onEvent(data);
    };
    src.onerror = () => {
      if (src !== es) return;
      src.close();
      es = null;
      if (closed) return;
      timer = setTimeoutFn(connect, delay);
      delay = Math.min(delay * 2, BACKOFF_MAX);
    };
  }

  connect();
  return {
    close() {
      closed = true;
      if (timer !== null) clearTimeoutFn(timer);
      timer = null;
      if (es) es.close();
      es = null;
    },
  };
}
