# Tracking and analytics

## What is in place now

| Layer | Status | Notes |
|---|---|---|
| Cloudflare Web Analytics | **Ready to deploy.** The beacon is added to every page at build time, so it no longer relies on sampled auto-injection. | Cookieless and needs no consent banner. Gives page views, referrers, landing pages, devices and countries. The privacy page has been updated to say so. |
| Traffic-by-platform report | **Working.** `python3 tools/traffic_report.py --days 30` | Groups referrers into Google search, Google Maps/Business Profile, Instagram, Facebook, YouTube, WhatsApp, LinkedIn, other and direct. Baseline: `traffic-baseline-2026-11-28.md`. |
| UTM-tagged links | **Generated.** `docs/digital-presence/LINKS.md` | They take effect once pasted into each platform. Cloudflare does not read UTM tags; GA4 does. |
| GA4 | **Prepared, off.** Set `tracking.ga4_measurement_id` in `presence/profile.json`. | The build then adds gtag plus events: `whatsapp_click`, `phone_click`, `directions_click`, `social_click`, `appointment_request` (WhatsApp form). |
| Google Search Console | **Needs owner action.** | Recommended: add a *Domain* property for `drbibinent.com` and verify it with the DNS TXT record in Cloudflare DNS. A meta-tag fallback is supported (`tracking.google_site_verification`). Then submit `https://drbibinent.com/sitemap.xml`. |
| Google Business Profile performance | **Needs owner action.** | Business Profile > Performance shows calls, chats, direction requests and website clicks. Add a monthly screenshot or export to the report. |
| Meta Pixel | **Not recommended now.** | It is an advertising tracker. On a medical site it can reveal which condition page a visitor read. Use it only if Meta ads are run, with a consent banner and a privacy-page update. Even then, keep it off the questionnaire and service pages. |

## Turning on GA4 (when the practice decides to)

1. Create a GA4 property (Admin > Create property, time zone India, currency INR) and a Web data stream for `https://drbibinent.com`. Copy the `G-…` measurement ID.
2. GA4 sets cookies. Before enabling it, add a short consent notice and a "Visitor statistics (Google Analytics)" row to `/privacy/`. India's Digital Personal Data Protection Act 2023 requires notice and consent for this.
3. Put the ID in `presence/profile.json`, run `python3 tools/build_worker.py`, test, and deploy (see README).
4. In GA4, mark `appointment_request`, `whatsapp_click` and `phone_click` as **key events** (conversions).
5. Link GA4 to Search Console (Admin > Product links).

## The dashboard: "which platform brings visits and enquiries"

| Metric | Source | Available |
|---|---|---|
| Visits by platform | Cloudflare referrers (`traffic_report.py`); GA4 *Session source / medium* once on | Now (Cloudflare); fuller with GA4 |
| Visits by UTM campaign (e.g. Instagram bio vs story) | GA4 *Session campaign* | After GA4 and the links are pasted |
| Enquiries (WhatsApp clicks, calls, form requests) by platform | GA4 key events × source | After GA4 |
| Google Business Profile calls, chats and directions | Business Profile Performance | Owner exports monthly |
| Search queries and positions (e.g. "ENT doctor Pala") | Search Console | After verification |

`tools/traffic_report.py` produces the Cloudflare part now. The weekly GitHub workflow can also produce it if a Cloudflare API token with *Account Analytics: Read* is saved as the repository secret `CLOUDFLARE_API_TOKEN`. Once GA4 and Search Console hold a few weeks of data, a Looker Studio report on GA4 + Search Console is the simplest single dashboard.
