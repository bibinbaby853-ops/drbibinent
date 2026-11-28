#!/usr/bin/env python3
"""Generate docs/digital-presence/LINKS.md: the link to paste into each platform.

Every link back to the website carries UTM parameters so GA4 (once enabled) can
attribute visits and appointment enquiries to the platform that sent them.
Regenerate after editing presence/profile.json:  python3 tools/make_links.py
"""
import json
import pathlib
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
P = json.loads((ROOT / "presence" / "profile.json").read_text(encoding="utf-8"))
ORIGIN = P["website"]["canonical_origin"]

# (platform, where the link goes, utm_source, utm_medium, utm_campaign, landing)
PLACEMENTS = [
    ("Google Business Profile – Pala", "Website field", "google", "organic", "gbp_pala", "/"),
    ("Google Business Profile – Pala", "Appointment links field", "google", "organic", "gbp_pala_appointment", "/#appointment"),
    ("Google Business Profile – Erattupetta", "Website field (if a practitioner listing exists)", "google", "organic", "gbp_erattupetta", "/#locations"),
    ("Instagram", "Bio – website link", "instagram", "social", "bio", "/"),
    ("Instagram", "Bio – extra link: Book appointment", "instagram", "social", "bio_appointment", "/#appointment"),
    ("Instagram", "Story / reel link sticker on a vertigo post", "instagram", "social", "story_vertigo", "/vertigo-balance/"),
    ("Facebook", "Page > Website", "facebook", "social", "page_website", "/"),
    ("Facebook", "Page > Action button: Book now", "facebook", "social", "page_book_button", "/#appointment"),
    ("YouTube", "Channel > Links: Website", "youtube", "social", "channel_links", "/"),
    ("YouTube", "Video description template", "youtube", "video", "video_description", "/"),
    ("WhatsApp Business", "Business profile > Website", "whatsapp", "messaging", "business_profile", "/"),
    ("WhatsApp Business", "Away / greeting message link", "whatsapp", "messaging", "auto_reply", "/#locations"),
    ("LinkedIn", "Profile > Contact info > Website", "linkedin", "social", "profile", "/"),
]


def utm(path, source, medium, campaign):
    frag = ""
    if "#" in path:
        path, frag = path.split("#", 1)
        frag = "#" + frag
    q = urllib.parse.urlencode({"utm_source": source, "utm_medium": medium, "utm_campaign": campaign})
    return "%s%s?%s%s" % (ORIGIN, path, q, frag)


def main():
    s = P["social"]
    out = [
        "# Link registry",
        "",
        "Generated from `presence/profile.json` by `tools/make_links.py`. Do not edit by hand.",
        "",
        "## Links into the website (paste these into each platform)",
        "",
        "Each link carries UTM tags so visits can be attributed once GA4 is on. The query string is kept "
        "when the site redirects, and the `#section` part still scrolls to the right place.",
        "",
        "| Platform | Where to paste it | Link |",
        "|---|---|---|",
    ]
    for plat, where, src, med, camp, path in PLACEMENTS:
        out.append("| %s | %s | `%s` |" % (plat, where, utm(path, src, med, camp)))
    wa = s["whatsapp"]["url"]
    out += [
        "",
        "## Official profile links (use these when one platform links to another)",
        "",
        "| Platform | Link | Status |",
        "|---|---|---|",
    ]
    for k in ("instagram", "facebook", "youtube", "linkedin", "whatsapp"):
        v = s[k]
        out.append("| %s | %s | %s |" % (k.capitalize(), v["url"] or "**missing**", v["status"].replace("_", " ")))
    for loc in P["locations"]:
        out.append("| Google Maps – %s | %s | in use on site, unverified |" % (loc["locality"], loc["maps_url"]))
        g = loc.get("google_review_url")
        if g is not None:
            out.append("| Google reviews – %s | %s | %s |" % (loc["locality"], g["value"] or "**missing**", g["status"].replace("_", " ")))
    out += [
        "",
        "## WhatsApp click-to-chat",
        "",
        "- Plain: `%s`" % wa,
        "- With appointment message: `%s?text=%s`" % (wa, urllib.parse.quote("Hello Doctor, I would like to book an appointment.")),
        "",
        "## Contact details to use everywhere (NAP)",
        "",
        "- Name on every platform: **%s** (Instagram, 30-character limit: %s)" % (
            P["identity"]["platform_display_name"]["value"], P["identity"]["platform_display_name"]["short_form_max_30_chars"]),
        "- Phone / WhatsApp: %s" % P["phone"]["display"],
    ]
    for loc in P["locations"]:
        out.append("- %s: %s, %s, %s %s" % (loc["name"], loc["street"], loc["locality"], loc["region"], loc["postal_code"]))
    out.append("- Website: %s/" % ORIGIN)
    dest = ROOT / "docs" / "digital-presence" / "LINKS.md"
    dest.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("wrote", dest)


if __name__ == "__main__":
    main()
