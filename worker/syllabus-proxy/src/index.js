// Relay for sugang's syllabus page (cc103) so a link from the static site opens it.
// Modeled on wafflestudio/snutt-proxy. Allowed: GET|POST /sugang/cc/cc1NN(ajax).action,
// GET /kor/* and GET /adm/* statics.
const UPSTREAM = "https://sugang.snu.ac.kr";
const ACTION = /^\/sugang\/cc\/cc1\d{2}(ajax)?\.action$/;
// Plain ASCII segments only: no percent-encoding (%2f, %5c, %00), backslash,
// empty segment or `..`, so nothing reaches sugang that it could decode into
// another path. sugang's own static paths all fit (checked 2026-10-17).
const STATIC = /^\/(kor|adm)(\/[A-Za-z0-9_.-]+)+$/;
const PASS_HEADERS = ["Content-Type", "Accept", "Accept-Language", "User-Agent"];
const TIMEOUT_MS = 15000;

const text = (status, body) => new Response(body, { status, headers: { "Content-Type": "text/plain; charset=utf-8" } });

const UNAVAILABLE_HTML = `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>강의계획서</title>
</head>
<body>
<p>수강신청 사이트가 지금 계획서를 보여주지 않습니다(점검 중일 수 있음). 잠시 후 다시 시도해 주세요.</p>
<p><a href="https://sugang.snu.ac.kr/">수강신청 사이트로 가기</a></p>
</body>
</html>
`;

const unavailablePage = () =>
  new Response(UNAVAILABLE_HTML, { status: 502, headers: { "Content-Type": "text/html; charset=utf-8" } });

function refererAllowed(request, env) {
  let origin;
  try {
    origin = new URL(request.headers.get("Referer") ?? "").origin;
  } catch {
    return false;
  }
  const allowed = String(env?.ALLOWED_ORIGINS ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  return origin === new URL(request.url).origin || allowed.includes(origin);
}

function hasDotDot(pathname) {
  return pathname.split("/").some((seg) => {
    let s = seg;
    try { s = decodeURIComponent(seg); } catch { return true; }
    return s.startsWith("..");
  });
}

function route(method, pathname) {
  if (hasDotDot(pathname)) return null;
  if (ACTION.test(pathname)) return method === "GET" || method === "POST" ? "action" : null;
  if (STATIC.test(pathname)) return method === "GET" ? "static" : null;
  return null;
}

export async function handle(request, env, upstreamFetch = fetch) {
  if (!refererAllowed(request, env)) return text(403, "forbidden");
  const url = new URL(request.url);
  const kind = route(request.method, url.pathname);
  if (!kind) return text(404, "not found");

  const headers = new Headers({ Referer: UPSTREAM + "/" });
  for (const name of PASS_HEADERS) {
    const v = request.headers.get(name);
    if (v !== null) headers.set(name, v);
  }
  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  let up;
  try {
    up = await upstreamFetch(UPSTREAM + url.pathname + url.search, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      redirect: "manual",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    return text(502, "upstream error");
  }

  if (up.status >= 300 && up.status < 400 && up.status !== 304) {
    return kind === "action" ? unavailablePage() : text(502, "upstream error");
  }

  const out = new Response(up.body, up);
  out.headers.delete("Set-Cookie");
  if (kind === "static" && (up.status < 300 || up.status === 304) && !out.headers.has("Cache-Control")) {
    out.headers.set("Cache-Control", "public, max-age=86400");
  }
  return out;
}

export default { fetch: (request, env) => handle(request, env) };
