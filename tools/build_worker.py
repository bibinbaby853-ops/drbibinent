#!/usr/bin/env python3
"""Build the Cloudflare Worker that serves drbibinent.com.

Reads every file under site/, injects tracking snippets configured in
presence/profile.json into HTML pages, and writes dist/worker.mjs: a single
self-contained module with all files embedded (the format of the deployed
Worker "red-wave-f642").

Usage:  python3 tools/build_worker.py [--out dist/worker.mjs]

Nothing is deployed by this script. Deployment is a separate, manual step
(see README.md) so that no change reaches the live site without approval.
"""
import argparse
import base64
import hashlib
import json
import mimetypes
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
PROFILE = ROOT / "presence" / "profile.json"

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".pdf": "application/pdf",
}
TEXT_PREFIXES = ("text/", "application/xml", "application/json", "image/svg+xml")

# Sends a GA4 event for each patient-contact action. Only included when a
# GA4 measurement ID is configured. Uses event delegation so it also covers
# links whose href is rewritten by the page's own scripts.
CLICK_TRACKING = """<script>
(function(){
  function kind(a){
    var h=a.getAttribute('href')||'';
    if(h.indexOf('https://wa.me/')===0||h.indexOf('https://api.whatsapp.com/')===0) return 'whatsapp_click';
    if(h.indexOf('tel:')===0) return 'phone_click';
    if(/maps\\.app\\.goo\\.gl|google\\.[a-z.]+\\/maps/.test(h)) return 'directions_click';
    if(/instagram\\.com|facebook\\.com|linkedin\\.com|youtube\\.com|youtu\\.be/.test(h)) return 'social_click';
    return null;
  }
  document.addEventListener('click',function(e){
    var a=e.target.closest&&e.target.closest('a[href]'); if(!a||typeof gtag!=='function') return;
    var k=kind(a); if(!k) return;
    gtag('event',k,{link_url:a.href,link_text:(a.textContent||a.getAttribute('aria-label')||'').trim().slice(0,80),page_path:location.pathname});
  },true);
  var b=document.getElementById('apptSubmit');
  if(b) b.addEventListener('click',function(){ if(typeof gtag==='function') gtag('event','appointment_request',{method:'whatsapp_form'}); },true);
})();
</script>"""


def tracking_snippets(tracking):
    head = []
    if tracking.get("google_site_verification"):
        head.append('<meta name="google-site-verification" content="%s">'
                    % tracking["google_site_verification"])
    ga4 = tracking.get("ga4_measurement_id")
    if ga4:
        if not re.fullmatch(r"G-[A-Z0-9]{4,}", ga4):
            raise SystemExit("ga4_measurement_id must look like G-XXXXXXXXXX")
        head.append(
            '<script async src="https://www.googletagmanager.com/gtag/js?id=%s"></script>\n'
            "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
            "gtag('js',new Date());gtag('config','%s');</script>" % (ga4, ga4))
    body = []
    if ga4:
        body.append(CLICK_TRACKING)
    token = tracking.get("cloudflare_web_analytics_token")
    if token:
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            raise SystemExit("cloudflare_web_analytics_token must be 32 hex characters")
        body.append("<script defer src=\"https://static.cloudflareinsights.com/beacon.min.js\" "
                    "data-cf-beacon='{\"token\": \"%s\"}'></script>" % token)
    return "\n".join(head), "\n".join(body)


def inject(html, head, body):
    if head:
        html = html.replace("</head>", head + "\n</head>", 1)
    if body:
        i = html.rfind("</body>")
        if i == -1:
            raise SystemExit("HTML page without </body>")
        html = html[:i] + body + "\n" + html[i:]
    return html


def content_type(path):
    t = TYPES.get(path.suffix.lower())
    if not t:
        t = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return t


def build(out):
    tracking = json.loads(PROFILE.read_text(encoding="utf-8")).get("tracking", {})
    head, body = tracking_snippets(tracking)
    entries = []
    for p in sorted(SITE.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        key = "/" + p.relative_to(SITE).as_posix()
        ctype = content_type(p)
        data = p.read_bytes()
        if ctype.startswith("text/html"):
            data = inject(data.decode("utf-8"), head, body).encode("utf-8")
        etag = '"%s"' % hashlib.md5(data).hexdigest()
        entry = {"type": ctype, "etag": etag}
        if ctype.startswith(TEXT_PREFIXES):
            entry["text"] = data.decode("utf-8")
        else:
            entry["b64"] = base64.b64encode(data).decode("ascii")
        entries.append((key, entry))

    parts = ["// Generated by tools/build_worker.py - do not edit by hand.\nconst FILES = {"]
    for n, (key, e) in enumerate(entries):
        lines = [json.dumps(key) + ": {",
                 '"type": ' + json.dumps(e["type"]) + ",",
                 '"etag": ' + json.dumps(e["etag"]) + ","]
        if "text" in e:
            lines.append('"text": ' + json.dumps(e["text"], ensure_ascii=False))
        else:
            lines.append('"b64": ' + json.dumps(e["b64"]))
        lines.append("}" + ("," if n < len(entries) - 1 else ""))
        parts.append("\n".join(lines))
    parts.append("};")
    parts.append(RUNTIME)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    print("wrote %s (%d files, %d bytes)" % (out, len(entries), out.stat().st_size))


RUNTIME = r'''const BYTES = new Map();
function bytesOf(key) {
  let b = BYTES.get(key);
  if (!b) {
    const f = FILES[key];
    b = f.text !== undefined ? new TextEncoder().encode(f.text)
                             : Uint8Array.from(atob(f.b64), (c) => c.charCodeAt(0));
    BYTES.set(key, b);
  }
  return b;
}
export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method !== "GET" && request.method !== "HEAD")
      return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
    let path;
    try { path = decodeURIComponent(url.pathname); } catch { path = url.pathname; }
    const redirect = (to) => new Response(null, { status: 307, headers: { Location: url.origin + encodeURI(to) + url.search } });
    if (path.endsWith("/index.html")) {
      const dir = path.slice(0, -"index.html".length);
      if (FILES[dir + "index.html"]) return redirect(dir);
    }
    if (!path.endsWith("/") && !FILES[path] && FILES[path + "/index.html"]) return redirect(path + "/");
    const key = path.endsWith("/") ? path + "index.html" : path;
    const f = FILES[key];
    if (!f) return new Response("Not Found", { status: 404, headers: { "content-type": "text/plain; charset=utf-8" } });
    const headers = { "content-type": f.type, "cache-control": "public, max-age=0, must-revalidate", "etag": f.etag };
    if (request.headers.get("if-none-match") === f.etag) return new Response(null, { status: 304, headers });
    return new Response(request.method === "HEAD" ? null : bytesOf(key), { status: 200, headers });
  },
};
'''

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist" / "worker.mjs"))
    build(pathlib.Path(ap.parse_args().out))
