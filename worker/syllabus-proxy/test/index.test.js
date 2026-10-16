import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { handle } from "../src/index.js";

const ENV = { ALLOWED_ORIGINS: "https://rekhet.github.io" };
const OWN = "https://proxy.example.workers.dev";
const OK_REF = "https://rekhet.github.io/class-checker/";

function fake(response) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url: String(url), init });
    if (response instanceof Error) throw response;
    return typeof response === "function" ? response() : response ?? new Response("ok");
  };
  fn.calls = calls;
  return fn;
}

function req(path, { referer = OK_REF, method = "GET", body, headers = {} } = {}) {
  const h = new Headers(headers);
  if (referer !== null) h.set("Referer", referer);
  return new Request(OWN + path, { method, body, headers: h });
}

const PAGE = "/sugang/cc/cc103.action?openSchyy=2026&sbjtCd=100.100&ltNo=001";

test("default export exposes fetch", () => {
  assert.equal(typeof worker.fetch, "function");
});

test("referer: rekhet origin with path is relayed", async () => {
  const up = fake();
  const res = await handle(req(PAGE), ENV, up);
  assert.equal(res.status, 200);
  assert.equal(up.calls.length, 1);
});

test("referer: own origin is relayed", async () => {
  const up = fake();
  const res = await handle(req(PAGE, { referer: OWN + "/sugang/cc/cc103.action" }), ENV, up);
  assert.equal(res.status, 200);
});

test("referer: evil, none, garbage, lookalike -> 403 without upstream call", async () => {
  for (const referer of ["https://evil.example/", null, "not a url",
    "https://rekhet.github.io.evil.example/", "http://rekhet.github.io/"]) {
    const up = fake();
    const res = await handle(req(PAGE, { referer }), ENV, up);
    assert.equal(res.status, 403, String(referer));
    assert.equal(up.calls.length, 0);
  }
});

test("ALLOWED_ORIGINS is comma-separated", async () => {
  const env = { ALLOWED_ORIGINS: "https://a.example, https://b.example" };
  const res = await handle(req(PAGE, { referer: "https://b.example/x" }), env, fake());
  assert.equal(res.status, 200);
});

test("upstream: url, method, Referer set, Cookie dropped, headers passed", async () => {
  const up = fake();
  await handle(req(PAGE, { headers: { Cookie: "a=b", "Accept-Language": "ko", Accept: "text/html", "User-Agent": "UA/1", "X-Other": "no" } }), ENV, up);
  const { url, init } = up.calls[0];
  assert.equal(url, "https://sugang.snu.ac.kr" + PAGE);
  assert.equal(init.method, "GET");
  assert.equal(init.redirect, "manual");
  assert.ok(init.signal instanceof AbortSignal);
  const h = new Headers(init.headers);
  assert.equal(h.get("Referer"), "https://sugang.snu.ac.kr/");
  assert.equal(h.get("Cookie"), null);
  assert.equal(h.get("Accept-Language"), "ko");
  assert.equal(h.get("Accept"), "text/html");
  assert.equal(h.get("User-Agent"), "UA/1");
  assert.equal(h.get("X-Other"), null);
});

test("response: Set-Cookie dropped, others kept, status kept", async () => {
  const r = new Response("<html>", { status: 200, headers: { "Set-Cookie": "JSESSIONID=1", "Content-Type": "text/html" } });
  const res = await handle(req(PAGE), ENV, fake(r));
  assert.equal(res.headers.get("Set-Cookie"), null);
  assert.equal(res.headers.get("Content-Type"), "text/html");
  assert.equal(await res.text(), "<html>");
});

const SENTENCE = "수강신청 사이트가 지금 계획서를 보여주지 않습니다(점검 중일 수 있음). 잠시 후 다시 시도해 주세요.";

test("upstream 3xx on action routes -> 502 Korean HTML, no Location/Set-Cookie", async () => {
  for (const p of [PAGE, "/sugang/cc/cc103ajax.action"]) {
    const r = new Response(null, { status: 302, headers: { Location: "/", "Set-Cookie": "a=b" } });
    const res = await handle(req(p), ENV, fake(r));
    assert.equal(res.status, 502);
    assert.equal(res.headers.get("Content-Type"), "text/html; charset=utf-8");
    assert.equal(res.headers.get("Location"), null);
    assert.equal(res.headers.get("Set-Cookie"), null);
    const html = await res.text();
    assert.ok(html.includes('<meta charset="utf-8">'));
    assert.ok(html.includes('lang="ko"'));
    assert.ok(html.includes(SENTENCE));
    assert.ok(html.includes('<a href="https://sugang.snu.ac.kr/">수강신청 사이트로 가기</a>'));
    assert.ok(!/(src|href)="(?!https:\/\/sugang\.snu\.ac\.kr\/")/.test(html));
  }
});

test("upstream 4xx/5xx on the page -> 502 Korean HTML; ajax and statics pass through", async () => {
  for (const status of [404, 500, 503]) {
    const page = await handle(req(PAGE), ENV, fake(new Response("err", { status, headers: { "Set-Cookie": "a=b" } })));
    assert.equal(page.status, 502, `page ${status}`);
    assert.ok((await page.text()).includes(SENTENCE), `page ${status}`);
    assert.equal(page.headers.get("Set-Cookie"), null);
    for (const p of ["/sugang/cc/cc103ajax.action", "/kor/js/common.js"]) {
      const res = await handle(req(p), ENV, fake(new Response("err", { status })));
      assert.equal(res.status, status, `${p} ${status}`);
      assert.equal(await res.text(), "err");
    }
  }
});

test("upstream 3xx on static route -> plain 502 upstream error", async () => {
  const r = new Response(null, { status: 302, headers: { Location: "/" } });
  const res = await handle(req("/kor/js/common.js"), ENV, fake(r));
  assert.equal(res.status, 502);
  assert.equal(await res.text(), "upstream error");
  assert.equal(res.headers.get("Location"), null);
});

test("action allow-list: cc103 GET and cc103ajax POST with body passthrough", async () => {
  const up = fake();
  const body = "openSchyy=2026&sbjtCd=100.100";
  const res = await handle(req("/sugang/cc/cc103ajax.action", {
    method: "POST", body, headers: { "Content-Type": "application/x-www-form-urlencoded" },
  }), ENV, up);
  assert.equal(res.status, 200);
  const { url, init } = up.calls[0];
  assert.equal(url, "https://sugang.snu.ac.kr/sugang/cc/cc103ajax.action");
  assert.equal(init.method, "POST");
  assert.equal(new Headers(init.headers).get("Content-Type"), "application/x-www-form-urlencoded");
  assert.equal(await new Response(init.body).text(), body);
});

test("not allowed paths -> 404 without upstream call", async () => {
  for (const p of ["/", "/sugang/cc/cc999x.action", "/sugang/cc/cc10.action",
    "/sugang/cc/cc103.action/x", "/sugang/xx/cc103.action", "/etc/passwd",
    "/kor/../x", "/kor/%2e%2e/x", "/kor/..;x/y", "/adm/../sugang",
    "/kor", "/korea/x",
    "/kor/a%2f..%2fx", "/kor/a%2F..%2Fx", "/kor/a%5c..%5cx", "/kor/x%00", "/kor/a%20b.css",
    "/kor//x.css", "/kor/..x/y"]) {
    const up = fake();
    const res = await handle(req(p), ENV, up);
    assert.equal(res.status, 404, p);
    assert.equal(up.calls.length, 0, p);
  }
});

test("methods: statics GET only, actions GET|POST only", async () => {
  for (const [m, p] of [["POST", "/kor/js/a.js"], ["PUT", PAGE], ["DELETE", PAGE], ["POST", "/adm/x.css"]]) {
    const up = fake();
    const res = await handle(req(p, { method: m, body: m === "DELETE" ? undefined : "x" }), ENV, up);
    assert.equal(res.status, 404, m + p);
    assert.equal(up.calls.length, 0);
  }
});

test("static: Cache-Control added when missing (200, 304), kept when present, not on errors or actions", async () => {
  const get = async (p, r) => (await handle(req(p), ENV, fake(r))).headers.get("Cache-Control");
  assert.equal(await get("/kor/js/common.js", new Response("x")), "public, max-age=86400");
  assert.equal(await get("/adm/a.css", new Response("x")), "public, max-age=86400");
  assert.equal(await get("/kor/js/common.js", new Response(null, { status: 304 })), "public, max-age=86400");
  assert.equal(await get("/kor/js/common.js", new Response("x", { headers: { "Cache-Control": "no-cache" } })), "no-cache");
  assert.equal(await get("/kor/js/x.js", new Response("n", { status: 404 })), null);
  assert.equal(await get(PAGE, new Response("x")), null);
});

test("upstream throw or timeout -> 502 upstream error", async () => {
  for (const err of [new Error("boom"), new DOMException("t", "TimeoutError")]) {
    const res = await handle(req(PAGE), ENV, fake(err));
    assert.equal(res.status, 502);
    assert.equal(await res.text(), "upstream error");
  }
});
