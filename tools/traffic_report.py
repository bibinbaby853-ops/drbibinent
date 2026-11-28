#!/usr/bin/env python3
"""Which platforms send visitors to drbibinent.com?

Queries Cloudflare Web Analytics (the cookieless page-load beacon) through the
Cloudflare GraphQL API and groups visits by referring platform: Google search,
Google Maps / Business Profile, Instagram, Facebook, YouTube, WhatsApp,
LinkedIn, other sites, and direct / unknown.

Needs a Cloudflare API token with Account Analytics: Read in the environment
variable CLOUDFLARE_API_TOKEN (in a Claude Code cloud session the proxy supplies
it). Usage:
  python3 tools/traffic_report.py [--days 30] [--report traffic.md] [--json traffic.json]

Limits: Cloudflare's free plan samples the beacon, so counts are estimates
(multiples of 10). Referrers are often missing for visits from WhatsApp and from
in-app browsers, and the beacon cannot read UTM parameters; per-campaign UTM
attribution needs GA4 (see docs/digital-presence/TRACKING.md).
"""
import argparse
import datetime
import json
import os
import sys
import urllib.request

ACCOUNT = "74ae374b09ece5710a571af7b9927037"
SITE_TAG = "c652ca16d583445c9d339073fe343377"
API = "https://api.cloudflare.com/client/v4/graphql"

PLATFORMS = [  # (label, substrings of the referrer host), first match wins
    ("Google Maps / Business Profile", ("maps.google.", "google.com/maps", "business.google.")),
    ("Google search", ("google.", "googlequicksearchbox")),
    ("Instagram", ("instagram.com",)),
    ("Facebook", ("facebook.com", "fb.com", "fb.me")),
    ("YouTube", ("youtube.com", "youtu.be")),
    ("WhatsApp", ("whatsapp.com", "wa.me", "com.whatsapp")),
    ("LinkedIn", ("linkedin.com", "lnkd.in")),
    ("Bing / other search", ("bing.com", "duckduckgo.com", "yahoo.", "yandex.")),
]


def classify(host):
    if not host:
        return "Direct / unknown"
    if host.endswith("drbibinent.com"):
        return "Internal (page to page)"
    for label, keys in PLATFORMS:
        if any(k in host for k in keys):
            return label
    return "Other websites"


def query(dim, since, token):
    q = ('{ viewer { accounts(filter:{accountTag:"%s"}) { rumPageloadEventsAdaptiveGroups('
         'limit:500, filter:{datetime_geq:"%s", siteTag:"%s"}, orderBy:[count_DESC]) '
         '{ count sum { visits } dimensions { %s } } } } }' % (ACCOUNT, since, SITE_TAG, dim))
    req = urllib.request.Request(API, data=json.dumps({"query": q}).encode(), method="POST",
                                 headers={"content-type": "application/json",
                                          "authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    if d.get("errors"):
        raise SystemExit("Cloudflare API error: %s" % d["errors"])
    return [(g["dimensions"][dim], g["count"], g["sum"]["visits"])
            for g in d["data"]["viewer"]["accounts"][0]["rumPageloadEventsAdaptiveGroups"]]


def table(rows, head):
    total = sum(r[1] for r in rows) or 1
    out = ["| %s | Page loads | Visits | Share of page loads |" % head, "|---|---:|---:|---:|"]
    out += ["| %s | %d | %d | %.0f%% |" % (k or "(none)", c, v, 100.0 * c / total) for k, c, v in rows]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--report")
    ap.add_argument("--json")
    a = ap.parse_args()
    if not 1 <= a.days <= 90:
        raise SystemExit("--days must be between 1 and 90 (Cloudflare's query window)")
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "proxy-injected")
    now = datetime.datetime.now(datetime.timezone.utc)
    since = (now - datetime.timedelta(days=a.days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    agg = {}
    for host, c, v in query("refererHost", since, token):
        k = classify(host)
        pc, pv = agg.get(k, (0, 0))
        agg[k] = (pc + c, pv + v)
    platforms = sorted(((k, c, v) for k, (c, v) in agg.items()), key=lambda r: -r[1])
    paths = query("requestPath", since, token)
    devices = query("deviceType", since, token)
    days = sorted(query("date", since, token))

    out = ["# Website traffic by platform, last %d days" % a.days, "",
           "Source: Cloudflare Web Analytics (sampled; counts are estimates). Generated %s UTC."
           % now.strftime("%Y-%m-%d %H:%M"), "",
           "## Where visitors came from", ""] + table(platforms, "Platform")
    out += ["", "## Landing pages", ""] + table(paths[:15], "Page")
    out += ["", "## Devices", ""] + table(devices, "Device")
    out += ["", "## Page loads by day", "", "| Date | Page loads |", "|---|---:|"]
    out += ["| %s | %d |" % (d, c) for d, c, _ in days]
    out += ["", "Visits from WhatsApp chats and some in-app browsers arrive without a referrer and "
            "are counted under Direct / unknown. Use the UTM links in docs/digital-presence/LINKS.md "
            "together with GA4 to attribute those."]
    report = "\n".join(out) + "\n"
    if a.report:
        open(a.report, "w", encoding="utf-8").write(report)
    if a.json:
        json.dump({"since": since, "platforms": platforms, "paths": paths, "devices": devices,
                   "days": days}, open(a.json, "w"), indent=1)
    print(report)


if __name__ == "__main__":
    sys.exit(main())
