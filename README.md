# drbibinent.com

The website of Dr. Bibin Baby, Consultant ENT Surgeon (Pala and Sunrise Hospital, Erattupetta), and the tools that keep the practice's online presence consistent.

## Layout

| Path | What it is |
|---|---|
| `site/` | The website exactly as served: home page, 8 service pages, legal pages, images, `sitemap.xml`, `robots.txt`. Edit pages here. |
| `presence/profile.json` | Single source of truth for name, phone, addresses, map, social and review links, and tracking IDs. Items still waiting for confirmation are marked `needs_confirmation`. |
| `tools/build_worker.py` | Builds `dist/worker.mjs`, the Cloudflare Worker that serves the site, and adds the tracking snippets from `profile.json`. |
| `tools/check_worker.mjs` | Requests every route of a built Worker locally (status codes, redirects, 404/405/304). |
| `tools/serve_worker.mjs`, `tools/browser_check.js` | Serve the built Worker on localhost and test every page in desktop and mobile Chromium: no sideways scrolling, call/WhatsApp/map/social links present, appointment form, questionnaire links. |
| `tools/audit.py` | Digital-presence audit: SEO tags, broken links and anchors, consistent phone/address/map/social details, structured data, sitemap, and (`--external`) external links. |
| `tools/traffic_report.py` | Visits by platform (Google, Instagram, Facebook, …) from Cloudflare Web Analytics. |
| `tools/make_links.py` | Regenerates `docs/digital-presence/LINKS.md`, the UTM-tagged link for every platform. |
| `docs/digital-presence/` | Audit, integration map, per-platform playbook, tracking plan and final report. |
| `.github/workflows/site-audit.yml` | Runs the build, route check and full audit on every change and every Monday. |
| `archive/` | The earlier six-page site, which was never the live site. Kept for reference only. |

## Making a change

```bash
python3 tools/audit.py                      # 0 errors expected
python3 tools/build_worker.py               # writes dist/worker.mjs
node tools/check_worker.mjs dist/worker.mjs
node tools/serve_worker.mjs dist/worker.mjs 8787 &   # then:
node tools/browser_check.js http://localhost:8787
```

## Deploying (manual, needs approval)

The live site is the Cloudflare Worker **`red-wave-f642`** on account `74ae374b09ece5710a571af7b9927037`, routed to `drbibinent.com` and `www.drbibinent.com`. Nothing in this repository deploys automatically. After the checks above pass and the change has been approved:

```bash
npx wrangler@4 deploy dist/worker.mjs --name red-wave-f642 --compatibility-date 2026-10-11 --no-bundle
python3 tools/audit.py --live              # confirms the live pages match the source
```

To roll back, use Cloudflare dashboard > Workers & Pages > red-wave-f642 > Deployments > roll back to the previous version.

## Rules

- Never put passwords, API keys or tokens in this repository.
- Do not publish patient information, images or testimonials without written consent, and keep content within the NMC professional conduct regulations on advertising.
- Change business details in `presence/profile.json` first, then on the pages. The audit flags any page that disagrees with it.
