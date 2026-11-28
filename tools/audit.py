#!/usr/bin/env python3
"""Digital-presence audit for drbibinent.com.

Checks the site source in site/ against presence/profile.json:
  * every page has a title, description, canonical, Open Graph tags, one <h1>
  * internal links and #anchors resolve; sitemap and pages agree
  * phone, WhatsApp, address and map links match the profile (NAP consistency)
  * JSON-LD parses, and its telephone and sameAs links match the profile
  * optionally (--external) every external link answers over HTTP
and lists the profile items still waiting for confirmation.

Usage:
  python3 tools/audit.py                 # offline checks
  python3 tools/audit.py --external      # also request every external link
  python3 tools/audit.py --live          # also compare drbibinent.com with site/
  python3 tools/audit.py --report out.md # write the Markdown report to a file
Exit status is 1 when any error is found.
"""
import argparse
import base64
import datetime
import hashlib
import html.parser
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_worker  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
PROFILE = json.loads((ROOT / "presence" / "profile.json").read_text(encoding="utf-8"))
ORIGIN = PROFILE["website"]["canonical_origin"]
# Sites that refuse or rate-limit automated requests. A non-2xx answer from
# them is reported as "could not verify", not as a broken link.
BOT_HOSTILE = ("instagram.com", "facebook.com", "linkedin.com", "wa.me", "whatsapp.com")


class Page(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.ids, self.meta, self.h1, self.ldjson = [], set(), {}, 0, []
        self.title, self.canonical, self.lang = None, None, None
        self._in_title = self._in_ld = False
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "html":
            self.lang = a.get("lang")
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "iframe" and a.get("src"):
            self.links.append(a["src"])
        elif tag == "meta":
            k = a.get("property") or a.get("name")
            if k:
                self.meta[k] = a.get("content")
        elif tag == "link" and a.get("rel") == "canonical":
            self.canonical = a.get("href")
        elif tag == "title":
            self._in_title, self._buf = True, []
        elif tag == "h1":
            self.h1 += 1
        elif tag == "script" and a.get("type") == "application/ld+json":
            self._in_ld, self._buf = True, []

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self.title, self._in_title = "".join(self._buf).strip(), False
        elif tag == "script" and self._in_ld:
            self.ldjson.append("".join(self._buf))
            self._in_ld = False

    def handle_data(self, data):
        if self._in_title or self._in_ld:
            self._buf.append(data)


def url_path(rel):
    """site/vertigo-balance/index.html -> /vertigo-balance/"""
    p = "/" + rel.as_posix()
    return p[: -len("index.html")] if p.endswith("/index.html") else p


def walk_ld(node, out):
    if isinstance(node, dict):
        out.append(node)
        for v in node.values():
            walk_ld(v, out)
    elif isinstance(node, list):
        for v in node:
            walk_ld(v, out)


BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}


def check_url(url, timeout=20):
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    # Many sites (Google Maps embeds among them) answer HEAD differently from a
    # real page load, so any HEAD failure is retried as a GET.
    for method in ("HEAD", "GET"):
        req.method = method
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.geturl()
        except urllib.error.HTTPError as e:
            if method == "HEAD":
                continue
            return e.code, url
        except Exception as e:  # DNS, TLS, timeout, proxy refusal
            if method == "HEAD":
                continue
            return None, "%s: %s" % (type(e).__name__, e)
    return None, url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--external", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--report")
    args = ap.parse_args()

    errors, warnings, info = [], [], []
    pages = {}
    for f in sorted(SITE.rglob("*.html")):
        p = Page()
        p.feed(f.read_text(encoding="utf-8"))
        pages[url_path(f.relative_to(SITE))] = p
    files = {"/" + f.relative_to(SITE).as_posix() for f in SITE.rglob("*") if f.is_file()}

    # The home page opens questionnaires from #q/<id> links; the ids live in
    # the embedded questionnaire app (QAPP_B64).
    m = re.search(r'var QAPP_B64 = "([^"]+)"', (SITE / "index.html").read_text(encoding="utf-8"))
    qapp = base64.b64decode(m.group(1)).decode("utf-8", "replace") if m else ""
    questionnaire_ids = set(re.findall(r'\{id:"([a-z0-9]+)"', qapp))

    phone = PROFILE["phone"]
    maps_urls = {loc["maps_url"] for loc in PROFILE["locations"]}
    social = {k: v["url"] for k, v in PROFILE["social"].items() if v.get("url")}
    external = {}

    for path, p in pages.items():
        where = path
        for key, label in (("description", "meta description"), ("og:title", "og:title"),
                           ("og:description", "og:description"), ("og:url", "og:url"),
                           ("og:image", "og:image")):
            if not p.meta.get(key):
                errors.append("%s: missing %s" % (where, label))
        if not p.title:
            errors.append("%s: missing <title>" % where)
        elif len(p.title) > 70:
            warnings.append("%s: title is %d characters (over ~70 may be cut off)" % (where, len(p.title)))
        d = p.meta.get("description") or ""
        if d and not 70 <= len(d) <= 170:
            warnings.append("%s: meta description is %d characters" % (where, len(d)))
        if p.canonical != ORIGIN + path:
            errors.append("%s: canonical is %r, expected %r" % (where, p.canonical, ORIGIN + path))
        if p.meta.get("og:url") and p.meta["og:url"] != ORIGIN + path:
            errors.append("%s: og:url %r does not match canonical" % (where, p.meta["og:url"]))
        if p.h1 != 1:
            errors.append("%s: has %d <h1> elements" % (where, p.h1))
        if not p.lang:
            errors.append("%s: <html> has no lang" % where)
        if "viewport" not in p.meta:
            errors.append("%s: no viewport meta (mobile)" % where)

        for href in p.links:
            if href.startswith("#"):
                if href.startswith("#q/") and path == "/":
                    if href[3:] not in questionnaire_ids:
                        errors.append("%s: questionnaire link %s does not exist" % (where, href))
                elif href != "#" and href[1:] not in p.ids:
                    errors.append("%s: anchor %s not found on page" % (where, href))
            elif href.startswith("/"):
                target = href.split("#")[0].split("?")[0]
                ok = target in files or (target.endswith("/") and target + "index.html" in files)
                if not ok:
                    errors.append("%s: internal link %s is broken" % (where, href))
                elif "#" in href:
                    frag = href.split("#", 1)[1]
                    tp = pages.get(target)
                    if frag.startswith("q/") and target == "/":
                        if frag[2:] not in questionnaire_ids:
                            errors.append("%s: link %s opens questionnaire %r, which does not exist" % (where, href, frag[2:]))
                    elif tp and frag not in tp.ids:
                        errors.append("%s: link %s points to a missing anchor" % (where, href))
            elif href.startswith("tel:"):
                if href != "tel:" + phone["e164"]:
                    errors.append("%s: phone link %s differs from %s" % (where, href, phone["e164"]))
            elif href.startswith("https://wa.me/"):
                num = href[len("https://wa.me/"):].split("?")[0]
                if num != phone["whatsapp_number"]:
                    errors.append("%s: WhatsApp link uses %s, expected %s" % (where, num, phone["whatsapp_number"]))
            elif href.startswith(("http://", "https://")):
                if href.startswith("http://"):
                    warnings.append("%s: insecure http link %s" % (where, href))
                if "maps.app.goo.gl" in href and href not in maps_urls:
                    errors.append("%s: map link %s is not one of the profile's locations" % (where, href))
                for name, url in social.items():
                    host = re.sub(r"^https://(www\.)?", "", url).split("/")[0]
                    if host in href and name != "whatsapp" and href.rstrip("/") != url.rstrip("/"):
                        errors.append("%s: %s link %s differs from profile %s" % (where, name, href, url))
                if not href.startswith(ORIGIN):
                    external.setdefault(href, set()).add(path)

        # Visible phone numbers in text must all be the practice number.
        txt = re.sub(r"<[^>]+>", " ", (SITE / (path.lstrip("/") + ("index.html" if path.endswith("/") else ""))).read_text(encoding="utf-8"))
        for m in set(re.findall(r"\+91[\s-]?\d{5}[\s-]?\d{5}", txt)):
            if re.sub(r"\D", "", m) != phone["e164"].lstrip("+"):
                errors.append("%s: text shows phone %s, profile has %s" % (where, m, phone["display"]))

        for raw in p.ldjson:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                errors.append("%s: JSON-LD does not parse (%s)" % (where, e))
                continue
            nodes = []
            walk_ld(data, nodes)
            for n in nodes:
                if n.get("telephone") and n["telephone"] != phone["e164"]:
                    errors.append("%s: JSON-LD telephone %s differs" % (where, n["telephone"]))
                for s in n.get("sameAs", []) if isinstance(n.get("sameAs"), list) else []:
                    if s.rstrip("/") not in {u.rstrip("/") for u in social.values()}:
                        errors.append("%s: JSON-LD sameAs %s is not in profile.json" % (where, s))
                addr = n.get("address")
                if isinstance(addr, dict) and addr.get("postalCode"):
                    if addr["postalCode"] not in {l["postal_code"] for l in PROFILE["locations"]}:
                        errors.append("%s: JSON-LD postal code %s not in profile" % (where, addr["postalCode"]))

    # Sitemap <-> pages
    sm = (SITE / "sitemap.xml").read_text(encoding="utf-8")
    locs = set(re.findall(r"<loc>([^<]+)</loc>", sm))
    for path in pages:
        if ORIGIN + path not in locs:
            errors.append("sitemap.xml: page %s is not listed" % path)
    for loc in locs:
        if loc[len(ORIGIN):] not in pages:
            errors.append("sitemap.xml: %s is not a page in site/" % loc)
    robots = (SITE / "robots.txt").read_text(encoding="utf-8")
    if "Sitemap: %s/sitemap.xml" % ORIGIN not in robots:
        errors.append("robots.txt does not point to the sitemap")

    # External links
    ext_rows = []
    if args.external:
        for url in sorted(external):
            status, final = check_url(url)
            bot = any(h in url for h in BOT_HOSTILE)
            if status and 200 <= status < 400:
                state = "ok"
            elif bot:
                state = "could not verify (site blocks automated checks)"
                warnings.append("external %s: %s (%s)" % (url, state, status or final))
            else:
                state = "BROKEN"
                errors.append("external link %s -> %s" % (url, status or final))
            ext_rows.append((url, status, state, ", ".join(sorted(external[url]))))

    # Live site vs source
    if args.live:
        for path in sorted(pages):
            try:
                req = urllib.request.Request(ORIGIN + path, headers=BROWSER_HEADERS)
                with urllib.request.urlopen(req, timeout=20) as r:
                    body = r.read()
            except urllib.error.HTTPError as e:
                if e.code in (403, 429, 503):
                    # Cloudflare's bot protection refuses data-centre clients such as
                    # GitHub's runners; that says nothing about what visitors get.
                    warnings.append("live %s: could not compare, Cloudflare refused the automated request (%s)" % (path, e.code))
                else:
                    errors.append("live %s: HTTP %s" % (path, e.code))
                continue
            except Exception as e:
                errors.append("live %s: %s" % (path, e))
                continue
            src = (SITE / (path.lstrip("/") + ("index.html" if path.endswith("/") else ""))).read_text(encoding="utf-8")
            built = build_worker.inject(src, *build_worker.tracking_snippets(PROFILE.get("tracking", {}))).encode("utf-8")
            # Cloudflare injects its Web Analytics beacon into pages served to browsers.
            body = re.sub(rb'<script[^>]*static\.cloudflareinsights\.com/beacon[^>]*></script>\n?', b"", body)
            if hashlib.md5(body).digest() != hashlib.md5(built).digest():
                info.append("live %s differs from the current build of site/ (changes not yet deployed)" % path)
            else:
                info.append("live %s matches the current build" % path)

    # Pending confirmations
    pending = []

    def walk(node, trail):
        if isinstance(node, dict):
            if node.get("status") == "needs_confirmation":
                pending.append((".".join(trail), node.get("note", "")))
            for k, v in node.items():
                walk(v, trail + [k])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, trail + [str(i)])
    walk(PROFILE, [])

    out = ["# drbibinent.com digital presence audit", "",
           "Run: %s UTC" % datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M"), "",
           "Pages checked: %d. Errors: %d. Warnings: %d." % (len(pages), len(errors), len(warnings)), ""]
    for title, items in (("Errors", errors), ("Warnings", warnings), ("Notes", info)):
        out += ["## " + title, ""] + (["- " + i for i in items] or ["- None"]) + [""]
    out += ["## External links (%d)" % len(external), ""]
    if ext_rows:
        out += ["| Link | HTTP | Result | Used on |", "|---|---|---|---|"]
        out += ["| %s | %s | %s | %s |" % (u, s or "-", st, w) for u, s, st, w in ext_rows]
    else:
        out += ["- %s (%s)" % (u, ", ".join(sorted(w))) for u, w in sorted(external.items())]
        out += ["", "Run with --external to request each one."]
    out += ["", "## Waiting for confirmation (presence/profile.json)", ""]
    out += ["- `%s` %s" % (k, n) for k, n in pending] or ["- None"]
    report = "\n".join(out) + "\n"
    if args.report:
        pathlib.Path(args.report).write_text(report, encoding="utf-8")
    print(report)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
