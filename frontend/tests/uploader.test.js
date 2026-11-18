import { test } from "node:test";
import assert from "node:assert/strict";

import { uploadFile, UploadError } from "../js/uploader.js";

const MiB = 1024 * 1024;

const json = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function fakeFile(size, name = "film.mp4") {
  return { name, size, slice: (a, b) => ({ a, b, length: b - a }) };
}

// A scriptable backend. `script` may return a Response (or throw) to override
// the default well-behaved reply for a given call; return undefined for default.
function fakeServer({ size, script = () => undefined, received = 0 } = {}) {
  const calls = [];
  let have = received;
  const fetchFn = async (url, init = {}) => {
    const method = init.method || "GET";
    const u = new URL(url, "http://x");
    const call = { method, path: u.pathname, offset: u.searchParams.get("offset"), init };
    calls.push(call);
    const custom = script(call, calls.length);
    if (custom !== undefined) return custom;
    if (method === "POST" && u.pathname === "/api/uploads") {
      return json(200, { id: "u1", kind: "video", filename: "film.mp4", size, received: have });
    }
    if (method === "GET") return json(200, { id: "u1", size, received: have });
    if (method === "PUT") {
      have = Number(u.searchParams.get("offset")) + call.init.body.length;
      return json(200, { received: have });
    }
    if (method === "POST" && u.pathname.endsWith("/finish")) return json(200, { received: have, size });
    throw new Error(`unexpected ${method} ${url}`);
  };
  return { fetchFn, calls };
}

const noSleep = () => Promise.resolve();

test("10 MiB in 4 MiB chunks: three PUTs, one finish, progress ends at 1", async () => {
  const size = 10 * MiB;
  const { fetchFn, calls } = fakeServer({ size });
  const progress = [];
  const id = await uploadFile(fakeFile(size), "video", {
    chunkSize: 4 * MiB,
    fetchFn,
    sleepFn: noSleep,
    onProgress: (f) => progress.push(f),
  });
  assert.equal(id, "u1");
  assert.deepEqual(
    calls.filter((c) => c.method === "PUT").map((c) => Number(c.offset)),
    [0, 4194304, 8388608],
  );
  assert.equal(calls.filter((c) => c.path.endsWith("/finish")).length, 1);
  assert.equal(calls.at(-1).path, "/api/uploads/u1/finish");
  assert.equal(progress.at(-1), 1);
  assert.deepEqual(progress, [...progress].sort((a, b) => a - b));
  const create = calls[0];
  assert.equal(create.method, "POST");
  assert.deepEqual(JSON.parse(create.init.body), { kind: "video", filename: "film.mp4", size });
  const put = calls.find((c) => c.method === "PUT");
  assert.equal(put.init.headers["Content-Type"], "application/octet-stream");
});

test("a failed PUT triggers a status GET and resumes at the server's offset", async () => {
  const size = 10 * MiB;
  let failed = false;
  const { fetchFn, calls } = fakeServer({
    size,
    script: (c) => {
      if (c.method === "PUT" && c.offset === String(4 * MiB) && !failed) {
        failed = true;
        return json(500, { detail: "boom" });
      }
      // The server says it got the whole chunk after all: the client must trust that, not its own offset.
      if (c.method === "GET") return json(200, { id: "u1", size, received: 8 * MiB });
    },
  });
  await uploadFile(fakeFile(size), "video", { chunkSize: 4 * MiB, fetchFn, sleepFn: noSleep });
  const seq = calls.map((c) => `${c.method} ${c.offset ?? c.path}`);
  const i = seq.indexOf(`PUT ${4 * MiB}`);
  assert.equal(seq[i + 1], "GET /api/uploads/u1");
  assert.equal(seq[i + 2], `PUT ${8 * MiB}`);
});

test("retry waits the backoff via sleepFn", async () => {
  const size = 2 * MiB;
  let n = 0;
  const { fetchFn } = fakeServer({ size, script: (c) => (c.method === "PUT" && n++ === 0 ? json(503, {}) : undefined) });
  const sleeps = [];
  await uploadFile(fakeFile(size), "image", {
    fetchFn,
    sleepFn: (ms) => (sleeps.push(ms), Promise.resolve()),
    backoffMs: 250,
  });
  assert.deepEqual(sleeps, [250]);
});

test("a 409 reply resumes from the received offset in its body, without a GET", async () => {
  const size = 10 * MiB;
  let first = true;
  const { fetchFn, calls } = fakeServer({
    size,
    script: (c) => {
      if (c.method === "PUT" && first) {
        first = false;
        return json(409, { received: 4 * MiB });
      }
    },
  });
  await uploadFile(fakeFile(size), "video", { chunkSize: 4 * MiB, fetchFn, sleepFn: noSleep });
  const puts = calls.filter((c) => c.method === "PUT").map((c) => Number(c.offset));
  assert.deepEqual(puts, [0, 4 * MiB, 8 * MiB]);
  assert.equal(calls.filter((c) => c.method === "GET").length, 0);
});

test("a network failure (fetch rejects) is followed by a status GET", async () => {
  const size = 6 * MiB;
  let thrown = false;
  const { fetchFn, calls } = fakeServer({
    size,
    script: (c) => {
      if (c.method === "PUT" && c.offset === String(4 * MiB) && !thrown) {
        thrown = true;
        throw new TypeError("Failed to fetch");
      }
      // The server got part of the chunk before the connection died.
      if (c.method === "GET") return json(200, { id: "u1", size, received: 5 * MiB });
    },
  });
  await uploadFile(fakeFile(size), "video", { chunkSize: 4 * MiB, fetchFn, sleepFn: noSleep });
  const seq = calls.map((c) => c.method);
  const i = seq.lastIndexOf("GET");
  assert.ok(i > 0);
  assert.equal(calls[i + 1].offset, String(5 * MiB));
});

test("five consecutive failures reject with an UploadError", async () => {
  const size = 10 * MiB;
  const { fetchFn, calls } = fakeServer({ size, script: (c) => (c.method === "PUT" ? json(500, { detail: "x" }) : undefined) });
  await assert.rejects(
    uploadFile(fakeFile(size), "video", { chunkSize: 4 * MiB, fetchFn, sleepFn: noSleep }),
    (e) => e instanceof UploadError && e.status === 500,
  );
  assert.equal(calls.filter((c) => c.method === "PUT").length, 5);
});

test("a success resets the failure count", async () => {
  const size = 20 * MiB;
  // Fail 4 times before every chunk: would exceed 5 in total but never consecutively.
  let streak = 0;
  const { fetchFn } = fakeServer({
    size,
    script: (c) => {
      if (c.method !== "PUT") return undefined;
      if (streak < 4) {
        streak++;
        return json(502, {});
      }
      streak = 0;
    },
  });
  await uploadFile(fakeFile(size), "video", { chunkSize: 4 * MiB, fetchFn, sleepFn: noSleep });
});

test("a non-409 4xx on a chunk is not retried", async () => {
  const size = 4 * MiB;
  const { fetchFn, calls } = fakeServer({ size, script: (c) => (c.method === "PUT" ? json(413, { detail: "too_large" }) : undefined) });
  await assert.rejects(
    uploadFile(fakeFile(size), "video", { fetchFn, sleepFn: noSleep }),
    (e) => e instanceof UploadError && e.status === 413 && e.detail === "too_large",
  );
  assert.equal(calls.filter((c) => c.method === "PUT").length, 1);
});

test("the initial POST failing with 507 rejects with status 507", async () => {
  const { fetchFn, calls } = fakeServer({
    size: MiB,
    script: (c) => (c.method === "POST" ? json(507, { detail: "disk_full" }) : undefined),
  });
  await assert.rejects(
    uploadFile(fakeFile(MiB), "video", { fetchFn, sleepFn: noSleep }),
    (e) => e instanceof UploadError && e.status === 507 && e.detail === "disk_full",
  );
  assert.equal(calls.length, 1);
});

test("a zero-byte file is rejected by the backend and surfaces as UploadError", async () => {
  const { fetchFn } = fakeServer({ size: 0, script: () => json(413, { detail: "empty" }) });
  await assert.rejects(uploadFile(fakeFile(0), "video", { fetchFn, sleepFn: noSleep }), (e) => e.status === 413);
});

test("a network error on the initial POST is an UploadError with status 0", async () => {
  const fetchFn = async () => {
    throw new TypeError("Failed to fetch");
  };
  await assert.rejects(
    uploadFile(fakeFile(MiB), "video", { fetchFn, sleepFn: noSleep }),
    (e) => e instanceof UploadError && e.status === 0,
  );
});

test("aborting the signal rejects", async () => {
  const size = 10 * MiB;
  const ac = new AbortController();
  const { fetchFn, calls } = fakeServer({ size });
  await assert.rejects(
    uploadFile(fakeFile(size), "video", {
      chunkSize: 4 * MiB,
      fetchFn,
      sleepFn: noSleep,
      signal: ac.signal,
      onProgress: () => ac.abort(),
    }),
    (e) => e.name === "AbortError",
  );
  assert.equal(calls.filter((c) => c.method === "PUT").length, 1);
});

test("an already aborted signal rejects before any request", async () => {
  const ac = new AbortController();
  ac.abort();
  const { fetchFn, calls } = fakeServer({ size: MiB });
  await assert.rejects(uploadFile(fakeFile(MiB), "video", { fetchFn, signal: ac.signal }), (e) => e.name === "AbortError");
  assert.equal(calls.length, 0);
});

test("finish answering 409 resumes from the server's received count", async () => {
  const size = 5 * MiB;
  let finishes = 0;
  const { fetchFn, calls } = fakeServer({
    size,
    script: (c) => {
      if (c.path.endsWith("/finish") && finishes++ === 0) return json(409, { detail: "incomplete" });
    },
  });
  await uploadFile(fakeFile(size), "video", { chunkSize: 4 * MiB, fetchFn, sleepFn: noSleep });
  assert.equal(calls.filter((c) => c.path.endsWith("/finish")).length, 2);
});

test("onRetry is told about each counted failure", async () => {
  const size = 2 * MiB;
  let n = 0;
  const { fetchFn } = fakeServer({ size, script: (c) => (c.method === "PUT" && n++ < 2 ? json(500, {}) : undefined) });
  const seen = [];
  await uploadFile(fakeFile(size), "video", { fetchFn, sleepFn: noSleep, onRetry: (k) => seen.push(k) });
  assert.deepEqual(seen, [1, 2]);
});

test("a long outage (PUT and status GET both failing) is one strike per cycle and resumes without restarting", async () => {
  const size = 10 * MiB;
  let down = 0; // number of PUT attempts to fail before the network returns
  const { fetchFn, calls } = fakeServer({
    size,
    script: (c) => {
      if (c.offset === String(4 * MiB) && c.method === "PUT" && down < 3) {
        down++;
        throw new TypeError("Failed to fetch");
      }
      if (c.method === "GET" && down > 0 && down < 3) throw new TypeError("Failed to fetch");
      if (c.method === "GET") return json(200, { id: "u1", size, received: 4 * MiB });
    },
  });
  const sleeps = [];
  await uploadFile(fakeFile(size), "video", {
    chunkSize: 4 * MiB,
    fetchFn,
    sleepFn: (ms) => (sleeps.push(ms), Promise.resolve()),
  });
  assert.equal(calls.filter((c) => c.method === "POST" && c.path === "/api/uploads").length, 1);
  assert.deepEqual(sleeps, [1000, 2000, 4000]);
  assert.deepEqual(
    calls.filter((c) => c.method === "PUT").map((c) => Number(c.offset)).slice(-2),
    [4 * MiB, 8 * MiB],
  );
});

test("backoff doubles 1000/2000/4000/8000 and the final error carries the upload id", async () => {
  const size = 4 * MiB;
  const { fetchFn } = fakeServer({
    size,
    script: (c) => {
      if (c.method !== "POST" || c.path !== "/api/uploads") throw new TypeError("Failed to fetch");
    },
  });
  const sleeps = [];
  await assert.rejects(
    uploadFile(fakeFile(size), "video", { fetchFn, sleepFn: (ms) => (sleeps.push(ms), Promise.resolve()) }),
    (e) => e instanceof UploadError && e.uploadId === "u1" && e.status === 0,
  );
  assert.deepEqual(sleeps, [1000, 2000, 4000, 8000]);
});

test("onUploadId reports the id as soon as the upload exists", async () => {
  const { fetchFn } = fakeServer({ size: MiB });
  const ids = [];
  await uploadFile(fakeFile(MiB), "video", { fetchFn, sleepFn: noSleep, onUploadId: (i) => ids.push(i) });
  assert.deepEqual(ids, ["u1"]);
});

test("resumeId skips the POST, asks the server for received and continues from there", async () => {
  const size = 10 * MiB;
  const { fetchFn, calls } = fakeServer({ size, received: 4 * MiB });
  const progress = [];
  const id = await uploadFile(fakeFile(size), "video", {
    chunkSize: 4 * MiB,
    fetchFn,
    sleepFn: noSleep,
    resumeId: "u1",
    onProgress: (f) => progress.push(f),
  });
  assert.equal(id, "u1");
  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].path, "/api/uploads/u1");
  assert.ok(!calls.some((c) => c.method === "POST" && c.path === "/api/uploads"));
  assert.deepEqual(
    calls.filter((c) => c.method === "PUT").map((c) => Number(c.offset)),
    [4 * MiB, 8 * MiB],
  );
  assert.equal(progress[0], 0.4);
  assert.equal(progress.at(-1), 1);
});

test("a 404 on resume falls back to a fresh upload", async () => {
  const size = 5 * MiB;
  const { fetchFn, calls } = fakeServer({
    size,
    script: (c) => (c.method === "GET" && c.path === "/api/uploads/old" ? json(404, { detail: "unknown" }) : undefined),
  });
  const ids = [];
  const id = await uploadFile(fakeFile(size), "video", {
    chunkSize: 4 * MiB,
    fetchFn,
    sleepFn: noSleep,
    resumeId: "old",
    onUploadId: (i) => ids.push(i),
  });
  assert.equal(id, "u1");
  assert.deepEqual(ids, ["u1"]);
  assert.deepEqual(
    calls.slice(0, 2).map((c) => `${c.method} ${c.path}`),
    ["GET /api/uploads/old", "POST /api/uploads"],
  );
  assert.equal(calls.filter((c) => c.method === "PUT").length, 2);
});
