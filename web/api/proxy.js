// GVO edge proxy — forwards /api/* to the live backend tunnel.
// The backend URL is synced from backend/TUNNEL_URL (pushed by the VPS
// tunnel-url-pusher every 5 min). Same-origin proxy = no CORS, no
// mixed-content, and the browser never talks to the tunnel directly.
export const config = { runtime: "edge" };

const RAW_URL =
  "https://raw.githubusercontent.com/faisalnugroho/genlayer-verification-oracle/main/backend/TUNNEL_URL";

const mem = { url: null, at: 0 };

async function backendUrl() {
  // Refresh the tunnel URL at most every 5 minutes (it rotates on restart).
  if (mem.url && Date.now() - mem.at < 5 * 60 * 1000) return mem.url;
  const res = await fetch(RAW_URL, { cache: "no-store" });
  const url = (await res.text()).trim();
  if (!/^https:\/\/[a-z0-9-]+\.trycloudflare\.com$/.test(url)) {
    if (mem.url) return mem.url; // keep the last known-good URL
    throw new Error("no tunnel url available");
  }
  mem.url = url;
  mem.at = Date.now();
  return url;
}

const BODYLESS = new Set(["GET", "HEAD"]);

export default async function handler(req) {
  const url = new URL(req.url);
  // The backend exposes the identical /api/v1/* paths — forward as-is.
  const path = url.pathname;

  let base;
  try {
    base = await backendUrl();
  } catch (e) {
    return json({ detail: "backend tunnel URL unavailable" }, 502);
  }

  const upstream = new URL(path + url.search, base);
  const init = {
    method: req.method,
    headers: {
      "content-type": req.headers.get("content-type") || "application/json",
      "accept": req.headers.get("accept") || "application/json",
    },
    ...(BODYLESS.has(req.method)
      ? {}
      : { body: await req.text() }),
  };
  // Preserve the real client IP for the backend's per-IP rate limiter.
  const xff = req.headers.get("x-forwarded-for");
  if (xff) init.headers["x-forwarded-for"] = xff;

  let upstreamRes;
  try {
    upstreamRes = await fetch(upstream, init);
  } catch (e) {
    return json({ detail: "backend unreachable (tunnel down?)", error: String(e) }, 502);
  }

  const body = await upstreamRes.arrayBuffer();
  return new Response(body, {
    status: upstreamRes.status,
    headers: { "content-type": upstreamRes.headers.get("content-type") || "application/json" },
  });
}

function json(obj, status) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json" },
  });
}
