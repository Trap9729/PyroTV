import { test, afterEach } from "node:test";
import assert from "node:assert/strict";

import * as apiMod from "../js/api.js";

const { api, ApiError, getPieces, thumbUrl, videoUrl, openEvents } = apiMod;

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

function stubFetch(status, body, { json = true } = {}) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, init });
    const text = body === undefined ? "" : json ? JSON.stringify(body) : body;
    return new Response(status === 204 ? null : text, {
      status,
      headers: { "content-type": json ? "application/json" : "text/plain" },
    });
  };
  return calls;
}

test("api() returns parsed JSON", async () => {
  const calls = stubFetch(200, { shop_name: "Pyro", setup_done: true, tv_connected: false });
  const out = await api("GET", "/api/state");
  assert.deepEqual(out, { shop_name: "Pyro", setup_done: true, tv_connected: false });
  assert.equal(calls[0].url, "/api/state");
  assert.equal(calls[0].init.method, "GET");
});

test("api() sends a JSON body", async () => {
  const calls = stubFetch(200, { ok: true });
  await api("POST", "/api/play", { key: "a:b" });
  assert.equal(calls[0].init.body, JSON.stringify({ key: "a:b" }));
  assert.equal(calls[0].init.headers["Content-Type"], "application/json");
});

test("api() throws ApiError with status 401", async () => {
  stubFetch(401, { detail: "nope" });
  await assert.rejects(api("GET", "/api/state"), (err) => {
    assert.ok(err instanceof ApiError);
    assert.ok(err instanceof Error);
    assert.equal(err.status, 401);
    assert.deepEqual(err.body, { detail: "nope" });
    return true;
  });
});

test("api() ApiError carries plain text bodies", async () => {
  stubFetch(502, "Bad Gateway", { json: false });
  await assert.rejects(api("GET", "/x"), (err) => err.status === 502 && err.body === "Bad Gateway");
});

test("api() resolves null on 204", async () => {
  stubFetch(204);
  assert.equal(await api("POST", "/api/player/stop"), null);
});

test("api() raw mode sends the body unchanged", async () => {
  const calls = stubFetch(200, { received: 3 });
  const bytes = new Uint8Array([1, 2, 3]);
  await api("PUT", "/api/uploads/u1?offset=0", bytes, { raw: true });
  assert.equal(calls[0].init.body, bytes);
  assert.equal(calls[0].init.headers["Content-Type"], "application/octet-stream");
});

test("getPieces builds the query string and omits empty params", async () => {
  const calls = stubFetch(200, []);
  await getPieces({ sort: "views" });
  assert.equal(calls[0].url, "/api/pieces?sort=views");
  await getPieces({ sort: "name", category: "Tårtor", q: "åska 49&x" });
  const url = new URL(calls[1].url, "http://h");
  assert.equal(url.pathname, "/api/pieces");
  assert.equal(url.searchParams.get("sort"), "name");
  assert.equal(url.searchParams.get("category"), "Tårtor");
  assert.equal(url.searchParams.get("q"), "åska 49&x");
  await getPieces({ sort: "name", category: "", q: "" });
  assert.equal(calls[2].url, "/api/pieces?sort=name");
  await getPieces();
  assert.equal(calls[3].url, "/api/pieces");
});

test("thumbUrl and videoUrl encode the key", () => {
  assert.equal(thumbUrl("lesli:Åska 49"), "/api/thumb/lesli%3A%C3%85ska%2049");
  assert.equal(videoUrl("lesli:Åska 49"), "/media/video/lesli%3A%C3%85ska%2049");
});

test("path helpers encode piece keys", async () => {
  const calls = stubFetch(204);
  await apiMod.recordView("a/b c");
  assert.equal(calls[0].url, "/api/pieces/a%2Fb%20c/view");
  assert.equal(calls[0].init.method, "POST");
});

test("a named helper exists for every route", () => {
  for (const name of [
    "getState", "getPieces", "getCategories", "getManufacturers", "play", "playerSeek",
    "playerPause", "playerStop", "recordView", "setPieceEnabled", "putSettings",
    "setupComplete", "setupReset", "createUpload", "getUpload", "putUploadChunk",
    "finishUpload", "createCustom", "getCustomJob", "listCustom", "updateCustom",
    "deleteCustom", "getStorage", "getHotspot", "setHotspot", "backupExport",
    "backupImport", "getDiagnostics",
  ]) {
    assert.equal(typeof apiMod[name], "function", name);
  }
});

class FakeEventSource {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.closed = false;
    this.onmessage = null;
    this.onerror = null;
    FakeEventSource.instances.push(this);
  }
  close() {
    this.closed = true;
  }
  emit(data) {
    this.onmessage?.({ data });
  }
  fail() {
    this.onerror?.(new Event("error"));
  }
}

function fakeTimers() {
  const pending = [];
  return {
    pending,
    setTimeoutFn: (fn, ms) => {
      const h = { fn, ms, cleared: false };
      pending.push(h);
      return h;
    },
    clearTimeoutFn: (h) => {
      if (h) h.cleared = true;
    },
    runNext() {
      const h = pending.shift();
      if (h && !h.cleared) h.fn();
      return h;
    },
  };
}

test("openEvents parses events and reconnects with backoff", () => {
  FakeEventSource.instances = [];
  const timers = fakeTimers();
  const got = [];
  const conn = openEvents((ev) => got.push(ev), {
    EventSourceImpl: FakeEventSource,
    setTimeoutFn: timers.setTimeoutFn,
    clearTimeoutFn: timers.clearTimeoutFn,
  });
  assert.equal(FakeEventSource.instances.length, 1);
  const es1 = FakeEventSource.instances[0];
  assert.equal(es1.url, "/api/events");
  es1.emit('{"type":"tv","connected":true}');
  es1.emit("not json"); // ignored
  assert.deepEqual(got, [{ type: "tv", connected: true }]);

  es1.fail();
  assert.ok(es1.closed);
  assert.equal(timers.pending.length, 1);
  assert.equal(timers.pending[0].ms, 1000);
  timers.runNext();
  assert.equal(FakeEventSource.instances.length, 2);

  // consecutive failures double up to 10 s
  const delays = [];
  for (let i = 0; i < 6; i++) {
    FakeEventSource.instances.at(-1).fail();
    delays.push(timers.pending[0].ms);
    timers.runNext();
  }
  assert.deepEqual(delays, [2000, 4000, 8000, 10000, 10000, 10000]);

  // a successful message resets the backoff
  const es = FakeEventSource.instances.at(-1);
  es.emit('{"type":"stopped","reason":"ended"}');
  es.fail();
  assert.equal(timers.pending[0].ms, 1000);

  conn.close();
  assert.ok(timers.pending[0].cleared);
  timers.runNext();
  const count = FakeEventSource.instances.length;
  assert.equal(count, 8, "no reconnect after close()");
});

test("openEvents close() closes the live source", () => {
  FakeEventSource.instances = [];
  const timers = fakeTimers();
  const conn = openEvents(() => {}, {
    EventSourceImpl: FakeEventSource,
    setTimeoutFn: timers.setTimeoutFn,
    clearTimeoutFn: timers.clearTimeoutFn,
  });
  conn.close();
  assert.ok(FakeEventSource.instances[0].closed);
  FakeEventSource.instances[0].fail();
  assert.equal(timers.pending.length, 0);
});
