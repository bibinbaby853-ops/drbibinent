# Stage 1 – Digital audit (28 November 2026)

## What was found

### Website
| Item | Finding |
|---|---|
| Domain | `drbibinent.com` on Cloudflare (account of the practice). `www.drbibinent.com` serves the same site. |
| Hosting | Cloudflare Worker `red-wave-f642`, all pages embedded in one script. Last deployed 24 Nov 2026. |
| Source code | **Was not in this repository.** The repository held an older 6-page site (`about.html`, `contact.html`, …) that is not live: `/about` and `/contact` return 404 on the real site. The live source is now in `site/`, and the old pages are in `archive/`. |
| Pages | Home (one long page with sections), 8 service pages, privacy, terms, disclaimer: 12 in total. |
| Contact | Phone and WhatsApp `+91 96455 64883` used consistently on every page. WhatsApp appointment form on the home page. |
| Locations | Pala (Opposite Lalam Church Road, 686575) and Sunrise Hospital, Erattupetta (Kaduvamuzhi, 686121), each with a Google Maps link and embedded map. |
| Structured data | Physician, Place, Hospital and WebSite on the home page, and MedicalProcedure + FAQPage on each service page. Registration number, qualifications and languages are included. |
| Social links on site | Instagram, Facebook, LinkedIn, WhatsApp in the home page header and footer. **Service and legal pages had none.** |
| YouTube | **Not linked anywhere.** No channel URL is known. |
| Google reviews link | **None on the site.** |
| Google Business Profile | **No link known.** The site uses `maps.app.goo.gl` share links, which may or may not point to a claimed Business Profile. |
| Appointment platform | None besides WhatsApp and phone. No Practo or similar link known. |
| Open Graph image | An image (`/images/dr-bibin-og.jpg`) was deployed but **no page referenced it**, so links shared on WhatsApp and Facebook showed no picture. |
| Legal pages | Privacy, terms and disclaimer had **no Open Graph tags**. |
| Analytics | Cloudflare Web Analytics has run since 1 Aug 2026. Cloudflare adds its beacon to every page served to a browser; on the free plan the data are sampled (about 1 visit in 10 recorded). No GA4, Search Console verification tag or Meta Pixel. |
| Privacy page accuracy | It says fonts are embedded in the pages. **In fact the home page and all 8 service pages load Google Fonts.** It does not mention visitor statistics. |
| Audit result before changes | 29 errors found by `tools/audit.py`: 21 missing Open Graph tags; the other 8 turned out to be valid questionnaire deep links that the audit then learned to recognise. |

### Traffic baseline (Cloudflare Web Analytics, sampled, last 90 days)
Google search 38% of page loads, Facebook 3%, direct/unknown 48% (this includes WhatsApp, which sends no referrer), internal 10%. **No visits from Instagram, YouTube or LinkedIn were recorded.** 59% of visits are on mobile. The full table is in `traffic-baseline-2026-11-28.md`.

## Inconsistencies to resolve

1. **Display name.** The brief uses "Dr. Bibin Baby ENT". The website uses "Dr. Bibin Baby – ENT Consultation" (site name) and "Dr. Bibin Baby" (person). Choose one name for Google Business Profile, Facebook, Instagram, YouTube and WhatsApp. Suggestion: keep **"Dr. Bibin Baby – ENT Consultation"** for the Pala Business Profile so it matches the website and structured data.
2. **Qualification wording.** The service-page footers say "MBBS, MS (ENT), DNB". The privacy page and structured data say "MBBS, MS (ENT), DNB (ENT)". This is minor; use "DNB (ENT)" everywhere on external profiles.
3. **Instagram URL form.** The site used both `instagram.com/dr.bibin` and `instagram.com/dr.bibin/`. Now standardised on the trailing-slash form.

## Information needing confirmation (not invented)

Tracked in `presence/profile.json` and printed at the end of every audit run:

- Google Business Profile URL(s) for Pala, and for Erattupetta if a practitioner listing exists
- Google review link ("Ask for reviews" short link)
- Consultation days and hours for each location (no hours appear anywhere on the site)
- YouTube channel URL, if one exists
- Appointment platform link (Practo, Eka Care, hospital page), if one is used
- Healthcare directory listings (Practo, Justdial, Lybrate, Sunrise Hospital doctor page) so that their details can be checked
- That the Instagram, Facebook and LinkedIn profiles linked from the site are the official, current ones
- Duplicate accounts: none could be detected from here (see below)

## What could not be checked from here, and why

- **External links.** This environment's network blocks Facebook, Instagram, LinkedIn, Google Maps and WhatsApp. A separate web fetch was used instead (results in REPORT.md). The scheduled GitHub audit (`.github/workflows/site-audit.yml`) re-checks every link from GitHub's network each week.
- **Duplicate or unofficial accounts.** Searching social networks for duplicates needs logged-in access or their official APIs. Neither is connected, and scraping is out of scope.
- **Google Business Profile and Search Console.** No Google account access is connected to this session.
