# Website SEO Audit — drbibinent.com

**Prepared:** 18 November 2026
**Site audited:** https://drbibinent.com (live site, all 12 URLs in the live sitemap)
**Scope:** technical SEO, on-page SEO, local SEO, competitors, keyword opportunities, 90-day plan

> **How to read this report**
> - **[Verified]** = checked directly on the live site or from a cited source during this audit.
> - **[Assumption]** = reasoned judgement that still needs confirming (e.g. in Google Search Console).
> - No search volumes, rankings or competitor traffic figures are included, because no keyword tool or Search Console data was available. Nothing here is a ranking guarantee.

---

## 0. Important discovery before anything else

**The website in this GitHub repository is NOT the website that is live.** [Verified]

| | GitHub repository | Live site (drbibinent.com) |
|---|---|---|
| Pages | 6 short pages (`about.html`, `vertigo.html`, `sinus.html`, `blog.html`, `contact.html`, `index.html`) | 12 pages (home, 8 detailed condition/procedure pages, 3 legal pages) |
| Content depth | ~100 words per page | ~1,250–1,450 words per service page, ~3,800 on home |
| URL format | `/about`, `/vertigo`, `/sinus` | `/vertigo-balance/`, `/sinus-surgery-fess/` etc. |

All of the repository's URLs (`/about`, `/vertigo`, `/sinus`, `/blog`, `/contact`) return **404 Not Found** on the live site.

**Risk:** if anyone deploys this repository to the domain, it would replace the far better live site and break every URL Google knows about.
**Action:** confirm where the live site's source files are kept (Cloudflare Pages upload, another repo, a website builder?). Either move the live source into this repository or clearly mark this repository as an old draft. Also remove `Dr_Bibin_Baby_Full_Website_Package.zip` from the repository root so it can never be published as a downloadable file.

Everything below audits the **live** site.

---

## 1. Overall SEO health assessment

**Overall: Good foundation, held back by a few trust-critical errors and local-SEO gaps.**

| Area | Rating | Summary |
|---|---|---|
| Content quality & depth | 🟢 Strong | Detailed, balanced, patient-friendly pages ("when surgery is *not* needed", honest recovery and risk sections), Malayalam summaries, emergency red-flag guidance, medical disclaimer. This is genuinely people-first content. |
| Technical foundation | 🟢 Good | HTTPS, Cloudflare CDN, Brotli compression, clean URLs, canonical tags on every page, sitemap + robots.txt correct, proper 404s, mobile-friendly. |
| Page speed (mobile) | 🟢 Good | Measured on throttled mobile: largest content shown in ~1.6 s (home) and ~0.7 s (service pages); layout shift ≈ 0.01. |
| Trust signals (E-E-A-T) | 🔴 Critical issue | Placeholder text `[[REGISTRATION-NUMBER]]` / `[[REGISTERING-COUNCIL]]` is **visible on all 12 pages**. No "medically reviewed / last updated" dates, no references on medical pages, no standalone doctor profile page. |
| Local SEO | 🟠 Needs work | No consultation hours anywhere; three different business names used; Pala map embed points to a road, not the clinic; no dedicated contact/location pages. |
| Search coverage | 🟠 Gaps | No pages for hearing loss / hearing test, tinnitus, medical sinusitis & allergy, ear infections/ear wax. No blog articles yet. |
| Structured data | 🟡 Mostly good | Physician, MedicalProcedure, BreadcrumbList present. Placeholders, missing opening hours/geo/sameAs, and a weak "Place" type for the Pala clinic need fixing. |
| Indexing | ⚪ Unknown | Sitemap lastmod is 11 Oct 2026, so the site is likely new. Indexing status must be confirmed in Search Console. [Assumption] |

---

## 2. Technical SEO issues (priority order)

### 🔴 Critical

**T1. Unfilled registration-number placeholders are visible to patients and Google** [Verified]
- Footer on all 12 pages reads: *"Registration number [[REGISTRATION-NUMBER]] · [[REGISTERING-COUNCIL]]"*.
- Also inside the homepage Physician structured data (`identifier.value`).
- Why it matters: for medical ("Your Money or Your Life") sites, Google's quality guidelines put heavy weight on verifiable credentials. A visible template placeholder looks unfinished and undermines trust for patients too.
- **Fix:** replace with the real Travancore-Cochin Medical Council (or relevant council) registration number on every page and in the schema. If you prefer not to show it, remove the line and the `identifier` block entirely; do not leave placeholders.

**T2. Repository vs live site mismatch** — see Section 0.

### 🟠 High

**T3. `www.drbibinent.com` loads the full site instead of redirecting** [Verified]
- `https://www.drbibinent.com/` returns 200 (canonical tag points to the non-www version, which limits harm).
- **Fix:** add a permanent (301) redirect from `www` → `https://drbibinent.com` in Cloudflare (Rules → Redirect Rules). Also confirm `http://` redirects to `https://` (enable "Always Use HTTPS" in Cloudflare). [Assumption — http test was inconclusive through our network proxy]

**T4. Titles and meta descriptions are too long on service pages** [Verified]
Google typically shows ~55–60 characters of a title and ~150–160 of a description on mobile; the rest is cut off.

| Page | Title length | Description length |
|---|---|---|
| /ear-surgery-eardrum/ | 89 | 183 |
| /tonsil-adenoid-surgery/ | 86 | 230 |
| /snoring-sleep-apnoea/ | 79 | 188 |
| /septoplasty-deviated-septum/ | 77 | 224 |
| /sinus-surgery-fess/ | 74 | 202 |
| /voice-swallowing/ | 74 | 201 |
| /pediatric-ent/ | 73 | 208 |
| /vertigo-balance/ | 71 | 210 |

Suggested rewrites (lead with what patients search, keep "Pala"):

| Page | Suggested title | Suggested description |
|---|---|---|
| Home | ENT Specialist in Pala, Kottayam \| Dr. Bibin Baby | Consultant ENT surgeon in Pala and Erattupetta. Vertigo and balance care, sinus and nasal surgery, ear surgery, hearing and children's ENT. Book on WhatsApp. |
| Vertigo | Vertigo & Dizziness Treatment in Pala \| Dr. Bibin Baby | Vertigo and balance clinic in Pala, Kottayam. Assessment and treatment for BPPV, vestibular migraine, Ménière's disease and long-standing dizziness. |
| Sinus (FESS) | Sinus Surgery (FESS) in Pala \| Dr. Bibin Baby ENT | Endoscopic sinus surgery in Pala for chronic sinusitis and nasal polyps, done through the nostrils. Medical treatment is tried first. |
| Septoplasty | Septoplasty for Deviated Septum in Pala \| Dr. Bibin Baby | Blocked nose from a deviated septum? Septoplasty in Pala, done inside the nose with no external cuts, usually as day-care surgery. |
| Tonsils | Tonsil & Adenoid Surgery for Children, Pala \| Dr. Bibin Baby | Coblation tonsil and adenoid surgery in Pala for snoring, mouth breathing and repeated tonsillitis in children. When it is and isn't needed. |
| Ear surgery | Eardrum Repair (Tympanoplasty) in Pala \| Dr. Bibin Baby | Tympanoplasty in Pala to repair a perforated eardrum, stop recurring ear discharge and help hearing. Assessment, recovery and risks explained. |
| Snoring | Snoring & Sleep Apnoea Treatment, Pala \| Dr. Bibin Baby | Snoring and obstructive sleep apnoea assessment in Pala: airway examination, sleep study guidance, CPAP and surgical options compared. |
| Pediatric | Children's ENT Specialist in Pala \| Dr. Bibin Baby | Paediatric ENT in Pala for ear infections, glue ear, snoring, mouth breathing, tonsils and hearing concerns in children. |
| Voice | Hoarseness & Swallowing Problems, Pala \| Dr. Bibin Baby | Persistent hoarseness or difficulty swallowing? Laryngoscopy and treatment in Pala for vocal cord nodules, polyps and throat reflux. |

*(Draft copy — for your approval before anything is changed.)*

**T5. Homepage H1 doesn't say what or where** [Verified]
- Current H1: "Ear, Nose, Throat and Balance Care". It contains no "ENT specialist" and no "Pala".
- **Fix:** e.g. *"ENT Specialist in Pala — Ear, Nose, Throat & Balance Care"*. One natural mention is enough.

**T6. Structured data improvements** [Verified]
- Pala clinic is typed as generic `Place` (which doesn't support `medicalSpecialty`, opening hours or reviews properly) → change to `MedicalClinic`.
- Missing on both locations: `openingHoursSpecification`, `geo` coordinates, `hasMap` (your Google Maps link), `image`.
- Physician is missing `image` (a real photo URL) and `sameAs` (Instagram, LinkedIn, Google Business Profile URL).
- Service pages repeat a separate Physician block with a partial address instead of referencing the homepage entity (`"provider": {"@id": "https://drbibinent.com/#physician"}`).
- Corrected, implementation-ready code is in **Appendix A**.

**T7. FAQ structured data no longer produces rich results** [Verified via news coverage of Google's documentation update]
- Google retired FAQ rich results in Search on **7 May 2026**, including for health websites. Your FAQPage markup is harmless and valid. Keep the visible FAQs, since they help patients and AI answers, but don't expect FAQ snippets in Google. No action needed beyond not investing further in it.

### 🟡 Medium

**T8. No "medically reviewed" information, dates or references on medical pages** [Verified]
- Add to each condition page: *"Written and medically reviewed by Dr. Bibin Baby, MBBS, MS (ENT), DNB (ENT) — Consultant ENT Surgeon. Last reviewed: [date]."* plus 3–5 references (e.g. AAO-HNS clinical practice guidelines, NICE, NHS, Cochrane). This is a strong trust signal for health content.

**T9. No standalone doctor profile or contact page** [Verified]
- Doctor bio, qualifications and locations exist only as sections of the homepage (`/#about`, `/#locations`). Separate `/dr-bibin-baby/` and `/contact/` pages give Google clear pages to rank for name and "clinic near me" searches, and give the GBP a better link target.

**T10. Homepage has no social-share image** [Verified]
- Service pages have `og:image`; the homepage (the most-shared page, e.g. on WhatsApp) doesn't. Add `<meta property="og:image" content="https://drbibinent.com/images/dr-bibin-og.jpg">` and set `twitter:card` to `summary_large_image`.

**T11. Internal linking gaps** [Verified]
- Each service page's "Related information" links to only 4 pages; vertigo, pediatric and voice pages are not linked from several siblings. Add a consistent "All services" menu in the service-page header/footer listing all 8 pages.
- Legal pages link only to each other and home. Add the service menu there too.

**T12. Images embedded as base64 inside the homepage HTML** [Verified]
- ~90 KB of logo/photo images are inlined in the HTML, so browsers re-download them on every visit and Google Images can't index the doctor's photo. Serve them as normal image files (WebP, with width/height), with descriptive ALT text. ALT text is already present on all 3 homepage images ✓.

**T13. Some small tap targets on mobile** [Verified — 18 links/buttons under 24 px tall on home]
- Minor accessibility issue; increase padding on footer/inline links.

### 🟢 Low

- Redirects for `/index.html` and missing trailing slashes use 307 (temporary). 301/308 is preferable; low impact. [Verified]
- Legal pages have no Open Graph tags (low priority).
- Sitemap: add new pages as they're created; keep `lastmod` accurate (Google ignores `changefreq`/`priority`).

### ✅ Already correct (no action)
Canonical tags on all pages · `lang` attribute · mobile viewport · HTTPS · Brotli compression · real 404 status for missing pages · robots.txt allows crawling and lists sitemap · BreadcrumbList on service pages · one H1 per page with logical H2/H3 structure · emergency red-flag guidance · medical disclaimer, privacy and terms pages · WhatsApp + call buttons · Malayalam summaries.

---

## 3. Local SEO opportunities

**L1. Publish consultation hours everywhere** [Verified missing]
No hours appear on the site. Patients searching "ENT doctor near me open now" and Google's local results depend on hours. Add them to the website, schema and Google Business Profile, identically for both locations.

**L2. Use one business name everywhere (NAP consistency)** [Verified]
The site currently uses three names: "Dr. Bibin Baby — ENT Consultation", "ENT Consultation, Pala" (schema), and "Dr. Bibin ENT Clinic" (footer copyright). Choose the exact name on your Google Business Profile and use it identically on the website, schema, GBP, Practo/Justdial and social profiles. Same for the address and phone number.

**L3. Make the Pala address unambiguous** [Verified]
"Opposite Lalam Church Road" is vague for patients and for Google's address matching. Add building name / floor / nearest landmark, matching your GBP pin exactly. Replace the Pala map embed (currently a search for "Lalam Church Road") with the embed of your actual Google Business Profile listing.

**L4. Google Business Profile(s)**
- Confirm whether you have **one profile per location** (Pala clinic; and an individual practitioner profile at Sunrise Hospital, Erattupetta, if the hospital permits). Google's guidelines allow practitioner profiles; avoid duplicates. [Assumption — not verified]
- Primary category: **"ENT Specialist"**. Consider secondary categories that are true for your practice (to be selected in GBP after checking what's offered there).
- Link the GBP website button to the homepage (or a future `/contact/` page) with UTM tracking, e.g. `?utm_source=google&utm_medium=organic&utm_campaign=gbp-pala`.
- A full GBP description, service list, weekly posts and FAQ set will be the next deliverable.

**L5. Reviews — ethical, ongoing process**
Ask satisfied patients for a review via a short link after their visit (a printed QR card works well). Never offer incentives, never write reviews, never post reviews on patients' behalf. Respond to all reviews without confirming any patient's medical details (patient confidentiality).

**L6. Citations / directory listings**
Claim and correct your profiles (consistent NAP, link to website) on: Practo, Justdial, Lybrate, HexaHealth, Apollo 24|7 / Bajaj Finserv Health doctor directories, and Sunrise Hospital's own doctor page (ask them to link to drbibinent.com). These directories currently dominate search results for "ENT specialist Pala". [Verified that they rank; your presence on each is not yet verified]

**L7. Location pages**
Create `/ent-specialist-pala/` (or a strong `/contact/` page) and `/ent-erattupetta-sunrise-hospital/` with hours, directions, parking, landmarks, map, photos of the entrance, and the services available at each location. Avoid copy-paste text between the two.

**L8. Leverage existing local press** [Verified]
The homepage links to a Manorama Online article on PPPD (June 2026). Ask the publisher to add a link to drbibinent.com in the author/expert credit. Pursue more expert quotes in Malayalam health media.

---

## 4. Competitor analysis

**Method & limits:** web searches run during this audit (the search tool is not Google India, so local pack / map results could not be observed). Listings marked "directory" come from third-party sites and may be out of date. No traffic or ranking numbers are available.

| Competitor | Type | What I found [Verified from source] | Their strength | Your opportunity |
|---|---|---|---|---|
| **Mar Sleeva Medicity, Palai** (Cherpunkal) | Large hospital | Dedicated ENT, Laryngology & Head and Neck department page; describes itself as one of the most comprehensive ENT centres in Kottayam ([source](https://marsleevamedicity.com/departments/ent-laryngology/)). Directory lists 3 ENT doctors ([HexaHealth](https://www.hexahealth.com/kottayam/hospital/mar-sleeva-medicity-palai-cherpunkal/doctors-list/ent-specialist)). | Hospital brand authority, many backlinks, directory coverage | Their content is a department overview; your condition-level depth (BPPV, PPPD, septoplasty decisions) and Malayalam summaries answer specific patient questions they don't. |
| **MKM Hospital, Pala** | Hospital | Directory lists 1 ENT specialist ([HexaHealth](https://www.hexahealth.com/kottayam/hospital/mkm-hospital-palai-pravithanam/doctors-list/ent-specialist)) | Local presence | Limited ENT content online. |
| **Marian Medical Centre, Pala** | Hospital | Directory listing for ENT is unclear/inconsistent ([HexaHealth](https://www.hexahealth.com/kottayam/hospital/marian-medical-centre-pala/doctors-list/ent-specialist)) | Local presence | Weak ENT web presence. |
| **KIMS Health, Kottayam** | Hospital (Kottayam town) | ENT department; consultant profile lists sinus and dizziness/vertigo treatment ([source](https://www.kimshealth.org/kottayam/specialities/ent/)) | Strong domain, multi-specialty brand | Distance from Pala/Erattupetta; you can win "near me" searches from Pala, Bharananganam, Erattupetta, Ramapuram etc. |
| **Aster Medcity, Kochi — Vertigo & Balance Clinic** | Regional vertigo competitor | Dedicated vertigo & balance clinic pages ([source](https://www.asterhospitals.in/hospitals/aster-medcity-kochi/specialities/ent-and-head-neck-surgery/services/vertigo-balance-clinic)) | Ranks for "vertigo clinic Kerala" type searches | No dedicated vertigo clinic in Pala surfaced in searches; your AVISA vestibular training is a real differentiator for "vertigo specialist Kottayam / Pala". |
| **Directories** (Apollo 24\|7, HexaHealth, Bajaj Finserv Health, Practo) | Aggregators | Occupy many results for "ENT specialist Kottayam/Pala" | Huge domain authority | Be listed accurately on them; they pass trust and referral patients. |

**Key observation:** in these searches, drbibinent.com did not appear even for "Bibin Baby ENT Pala". That's likely because the site is new (sitemap dated Oct 2026) and/or the search tool isn't Google. **Confirm indexing in Google Search Console first.** [Assumption]

---

## 5. Top 30 keyword opportunities

Volumes are **not** given because I have no keyword-tool or Search Console data. Priority is a judgement based on how likely the search is to lead to a booking, how well the site matches it now, and realistic competition. **Validate with Google Keyword Planner and, after 4–6 weeks, Search Console impressions.**

| # | Keyword | Intent | Current page / gap | Priority |
|---|---|---|---|---|
| 1 | ENT specialist in Pala | Local / booking | Home | ★★★ |
| 2 | ENT doctor in Pala | Local / booking | Home | ★★★ |
| 3 | ENT clinic Pala | Local / booking | Home → new /contact/ | ★★★ |
| 4 | ENT doctor near me (searched in Pala area) | Local / booking | GBP + Home | ★★★ |
| 5 | Dr Bibin Baby ENT | Brand / navigational | Home → new doctor profile page | ★★★ |
| 6 | Vertigo specialist in Kerala | Commercial | /vertigo-balance/ | ★★★ |
| 7 | Vertigo treatment in Pala | Local / booking | /vertigo-balance/ | ★★★ |
| 8 | Vertigo doctor Kottayam | Local / booking | /vertigo-balance/ | ★★★ |
| 9 | BPPV treatment Kottayam / Epley manoeuvre Pala | Treatment | /vertigo-balance/ (+ new BPPV article) | ★★☆ |
| 10 | Sinus treatment in Pala | Local / treatment | Gap: needs medical sinusitis page (FESS page is surgery-focused) | ★★★ |
| 11 | Sinus surgery Kottayam / FESS Kottayam | Treatment | /sinus-surgery-fess/ | ★★☆ |
| 12 | Septoplasty in Kottayam / deviated septum surgery Pala | Treatment | /septoplasty-deviated-septum/ | ★★☆ |
| 13 | Hearing test in Pala / hearing assessment Pala | Local / service | **Gap — no page** | ★★★ |
| 14 | Hearing loss treatment Pala | Treatment | **Gap** | ★★☆ |
| 15 | Ear specialist in Pala | Local / booking | Home / ear page | ★★★ |
| 16 | Tinnitus treatment Kerala / ringing in ears doctor | Treatment | **Gap** | ★★☆ |
| 17 | Ear discharge treatment / perforated eardrum surgery Kottayam | Treatment | /ear-surgery-eardrum/ | ★★☆ |
| 18 | Tympanoplasty Kottayam | Treatment | /ear-surgery-eardrum/ | ★★☆ |
| 19 | Tonsil surgery for child Kottayam / adenoid surgery Pala | Treatment | /tonsil-adenoid-surgery/ | ★★☆ |
| 20 | Child snoring / mouth breathing in children doctor | Informational → booking | /pediatric-ent/ + blog | ★★☆ |
| 21 | Pediatric ENT Pala / children's ENT doctor Kottayam | Local / booking | /pediatric-ent/ | ★★☆ |
| 22 | Snoring treatment Pala / sleep apnoea doctor Kottayam | Treatment | /snoring-sleep-apnoea/ | ★★☆ |
| 23 | Hoarseness of voice treatment Kottayam | Treatment | /voice-swallowing/ | ★☆☆ |
| 24 | Nasal allergy / allergic rhinitis treatment Pala | Treatment | **Gap** | ★★☆ |
| 25 | Ear wax removal Pala | Local / service | **Gap** (high-frequency simple service) | ★★☆ |
| 26 | Ear infection treatment Pala | Local / treatment | **Gap** | ★★☆ |
| 27 | ENT doctor Erattupetta / Sunrise Hospital ENT | Local / booking | Home → new Erattupetta page | ★★★ |
| 28 | ENT doctor Bharananganam / Ramapuram / Kanjirappally (nearby towns) | Local | Location pages + GBP service area | ★☆☆ |
| 29 | Dizziness causes / why do I feel dizzy (Malayalam: തലകറക്കം കാരണം) | Informational | Blog + vertigo page | ★★☆ |
| 30 | PPPD dizziness / vestibular migraine treatment Kerala | Informational / niche | /vertigo-balance/ + dedicated article (low competition, matches your AVISA training) | ★★☆ |

Use these naturally, once in the title, H1 and opening paragraph, and then in normal language. No stuffing.

---

## 6. Prioritised 90-day SEO action plan

Nothing will be changed on the live site without your approval. Items marked 👨‍⚕️ need your input.

### Days 1–14 — Fix trust issues and set up measurement
| # | Action | Owner | Effort |
|---|---|---|---|
| 1 | 👨‍⚕️ Provide medical registration number and council; replace placeholders on all pages and in schema (T1) | You + developer | 1 hr |
| 2 | 👨‍⚕️ Confirm where the live site's source is kept; archive/replace the old repo version; delete the zip (Section 0) | You | 30 min |
| 3 | Set up / verify **Google Search Console** (domain property) and **Bing Webmaster Tools**; submit sitemap; check Page Indexing report | Developer | 1 hr |
| 4 | Add **Google Analytics 4** (or privacy-friendly alternative) and track WhatsApp/call clicks as conversions; update privacy page accordingly | Developer | 2 hrs |
| 5 | 👨‍⚕️ Confirm consultation hours for both locations and the single official business name; publish hours on site (L1, L2) | You | 30 min |
| 6 | 👨‍⚕️ Verify/claim Google Business Profile(s); set category "ENT Specialist", hours, phone, website with UTM (L4) | You | 1–2 hrs |
| 7 | Cloudflare: 301 redirect www → non-www; "Always Use HTTPS" (T3) | Developer | 15 min |

### Days 15–45 — On-page and technical improvements
| # | Action | Effort |
|---|---|---|
| 8 | Approve and apply new titles/meta descriptions (T4) | 1 hr |
| 9 | Update homepage H1 to include "ENT Specialist in Pala" (T5) | 10 min |
| 10 | Replace schema with Appendix A version (hours, geo, sameAs, MedicalClinic, provider @id); validate in Google's Rich Results Test and validator.schema.org (T6) | 2 hrs |
| 11 | Add "Medically reviewed by… / Last reviewed" box and references to all 8 service pages (T8) | 👨‍⚕️ review + 3 hrs |
| 12 | Create **/dr-bibin-baby/** profile page (photo, qualifications, training incl. AVISA, memberships, registration, languages, media mentions) (T9) | 4 hrs |
| 13 | Create **/contact/** page with both locations, hours, map embeds of the real GBP listings, parking/landmarks (L3, L7) | 3 hrs |
| 14 | Homepage og:image; serve images as files with WebP; all-services menu on subpages (T10–T12) | 3 hrs |
| 15 | Claim/correct directory listings: Practo, Justdial, Lybrate, HexaHealth, Sunrise Hospital website (L6) | 3 hrs |
| 16 | Start review-request process (QR card at reception) (L5) | 1 hr |

### Days 46–90 — Content expansion and local authority
| # | Action | Effort |
|---|---|---|
| 17 | New service pages (each ~1,000+ words, medically reviewed, with Malayalam summary): **Hearing loss & hearing test**, **Tinnitus**, **Sinusitis & nasal allergy (medical treatment)**, **Ear infections & ear wax** | 👨‍⚕️ review each |
| 18 | Erattupetta / Sunrise Hospital location page (L7) | 3 hrs |
| 19 | Launch blog with 4 articles: *Why do I feel dizzy?*, *BPPV and the Epley manoeuvre*, *Is my child's snoring a problem?*, *PPPD explained*: each with author box, references and internal links | 👨‍⚕️ review each |
| 20 | Weekly Google Business Profile posts (12 posts over the quarter) | 20 min/week |
| 21 | Local PR: request credit link from Manorama article; offer expert quotes to Malayalam health media (L8) | ongoing |
| 22 | Day-90 review: first Search Console report — high-impression/low-CTR pages, keywords in positions 5–20, next-quarter plan | 3 hrs |

### What success looks like by day 90 (realistic, not guaranteed)
- All pages indexed in Search Console; zero placeholder or NAP inconsistencies.
- Google Business Profile complete with hours, services, weekly posts and a steady trickle of genuine reviews.
- 6–8 new medically reviewed pages/articles live.
- Baseline established for impressions, clicks, CTR, average position, and WhatsApp/call enquiries, so the next quarter can be data-driven.

---

## 7. What I need from you next

1. **Medical registration number and council** (for T1).
2. **Consultation days/hours** at Pala and Sunrise Hospital, Erattupetta.
3. **Official business name** exactly as on your Google Business Profile (and the GBP link(s)).
4. **Where the live website's files are kept** and who can edit them.
5. **Google Search Console access or exports** (Performance → last 3 months: Queries and Pages), so I can replace assumptions with real data.
6. Approval of the title/description rewrites in T4.

---

## Appendix A — Corrected structured data (homepage) — draft for approval

Replace the existing homepage JSON-LD with this. Fill in the `«…»` values. Remove any property you cannot fill truthfully; do not leave placeholders.

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Physician",
      "@id": "https://drbibinent.com/#physician",
      "name": "Dr. Bibin Baby",
      "jobTitle": "Consultant ENT Surgeon",
      "medicalSpecialty": "Otolaryngologic",
      "url": "https://drbibinent.com/",
      "image": "https://drbibinent.com/images/dr-bibin-og.jpg",
      "telephone": "+919645564883",
      "description": "Consultant ENT Surgeon practising in Pala and at Sunrise Hospital, Erattupetta, Kottayam. MBBS, MS (ENT), DNB (ENT).",
      "identifier": {
        "@type": "PropertyValue",
        "name": "Medical registration number",
        "value": "«REAL NUMBER»",
        "description": "«COUNCIL NAME»"
      },
      "alumniOf": [
        {"@type": "CollegeOrUniversity", "name": "Government Medical College, Kottayam"},
        {"@type": "CollegeOrUniversity", "name": "Government Medical College, Kozhikode"}
      ],
      "memberOf": [
        {"@type": "Organization", "name": "Association of Otorhinolaryngologists of India"},
        {"@type": "Organization", "name": "Indian Medical Association"}
      ],
      "knowsLanguage": ["Malayalam", "English", "Hindi"],
      "sameAs": [
        "https://www.instagram.com/dr.bibin",
        "https://www.linkedin.com/in/bibin-baby-75a553154",
        "«GOOGLE BUSINESS PROFILE URL»"
      ],
      "worksFor": [
        {"@id": "https://drbibinent.com/#pala"},
        {"@id": "https://drbibinent.com/#erattupetta"}
      ]
    },
    {
      "@type": "MedicalClinic",
      "@id": "https://drbibinent.com/#pala",
      "name": "«EXACT NAME AS ON GOOGLE BUSINESS PROFILE»",
      "url": "https://drbibinent.com/",
      "image": "https://drbibinent.com/images/dr-bibin-og.jpg",
      "telephone": "+919645564883",
      "medicalSpecialty": "Otolaryngologic",
      "address": {
        "@type": "PostalAddress",
        "streetAddress": "«Building / landmark», Opposite Lalam Church Road",
        "addressLocality": "Pala",
        "addressRegion": "Kerala",
        "postalCode": "686575",
        "addressCountry": "IN"
      },
      "geo": {"@type": "GeoCoordinates", "latitude": «LAT», "longitude": «LNG»},
      "hasMap": "https://maps.app.goo.gl/xQgMM4eWyyvT7hyx7",
      "openingHoursSpecification": [
        {
          "@type": "OpeningHoursSpecification",
          "dayOfWeek": ["«Monday»", "«Tuesday»"],
          "opens": "«17:00»",
          "closes": "«20:00»"
        }
      ]
    },
    {
      "@type": "MedicalClinic",
      "@id": "https://drbibinent.com/#erattupetta",
      "name": "Sunrise Hospital, Erattupetta — ENT consultation",
      "telephone": "+919645564883",
      "medicalSpecialty": "Otolaryngologic",
      "address": {
        "@type": "PostalAddress",
        "streetAddress": "Pala–Erattupetta Road, Kaduvamuzhi",
        "addressLocality": "Erattupetta",
        "addressRegion": "Kerala",
        "postalCode": "686121",
        "addressCountry": "IN"
      },
      "hasMap": "https://maps.app.goo.gl/RG1hwVPUYW3QNCjC9",
      "openingHoursSpecification": [
        {
          "@type": "OpeningHoursSpecification",
          "dayOfWeek": ["«Wednesday»"],
          "opens": "«09:00»",
          "closes": "«13:00»"
        }
      ]
    }
  ]
}
</script>
```

On each service page, replace the inline Physician `provider` block with a reference:

```json
"provider": {"@id": "https://drbibinent.com/#physician"}
```

Note: Google shows local business details mainly from your Google Business Profile, not from schema. Schema helps Google connect the website to the profile, so hours and name must match the GBP exactly.

---

## Sources consulted
- Live site crawl of https://drbibinent.com and its sitemap (18 Nov 2026)
- Google FAQ rich results retirement coverage: [Search Engine Journal](https://www.searchenginejournal.com/google-drops-faq-rich-results-from-search/574429/), [PPC Land](https://ppc.land/google-kills-faq-rich-results-what-seos-saw-coming-since-2019/)
- Competitors: [Mar Sleeva Medicity ENT](https://marsleevamedicity.com/departments/ent-laryngology/), [HexaHealth – Mar Sleeva](https://www.hexahealth.com/kottayam/hospital/mar-sleeva-medicity-palai-cherpunkal/doctors-list/ent-specialist), [HexaHealth – MKM Hospital](https://www.hexahealth.com/kottayam/hospital/mkm-hospital-palai-pravithanam/doctors-list/ent-specialist), [HexaHealth – Marian Medical Centre](https://www.hexahealth.com/kottayam/hospital/marian-medical-centre-pala/doctors-list/ent-specialist), [KIMS Health Kottayam ENT](https://www.kimshealth.org/kottayam/specialities/ent/), [Aster Medcity Vertigo & Balance Clinic](https://www.asterhospitals.in/hospitals/aster-medcity-kochi/specialities/ent-and-head-neck-surgery/services/vertigo-balance-clinic), [Bajaj Finserv Health – ENT hospitals Kottayam](https://www.bajajfinservhealth.in/hospitals/kottayam/ent-hospitals-21)
