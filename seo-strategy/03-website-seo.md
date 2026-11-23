# 3. Website SEO Strategy

## A. Urgent live-site fixes (found on drbibinent.com, 23 Nov 2026)

| # | Problem | Fix | Impact |
|---|---|---|---|
| 1 | Phone number links to `#` | `<a href="tel:+919645564883">+91 96455 64883</a>` | High: calls are your main conversion |
| 2 | "WhatsApp the Clinic" and booking buttons link to `#` | `https://wa.me/919645564883?text=Hello%2C%20I%20would%20like%20an%20ENT%20appointment` | High |
| 3 | `[[REGISTRATION-NUMBER]]` and `[[REGISTERING-COUNCIL]]` placeholders in footer | "Reg. No. XXXXX, Travancore-Cochin Medical Council" | High: trust (E-E-A-T) and NMC transparency |
| 4 | No consultation timings | Add a timings table for Pala and Erattupetta (same as GBP) | High |
| 5 | "Blog" goes off-site | Create `/blog/` on your domain; keep the Manorama article as an "In the media" link | Medium |
| 6 | "Questionnaires" link is `#` | Remove it until it's built, or link to a real dizziness or snoring questionnaire page | Low |
| 7 | No embedded map | Embed the Google Map of your **GBP listing** on the Locations section/contact page | Medium |
| 8 | Schema not confirmed | Add the JSON-LD in section F; test it in the Rich Results Test | Medium |
| 9 | Two versions of the site exist (this repo vs live) | Pick one source of truth. If the live site is the real one, don't deploy this repo's `sitemap.xml` (it lists URLs that 404) | Medium |
| 10 | Homepage is ~118 KB of HTML with many sections | Fine to keep, but make sure each condition has its **own page** (already partly done) and that the homepage links to every one | Medium |

## B. Ideal site structure

```
drbibinent.com/
├── /about-dr-bibin-baby/          ← full bio, qualifications, registration no., memberships, photo
├── /locations/
│   ├── /pala-ent-clinic/           ← address, map, hours, parking, directions from nearby towns
│   └── /erattupetta-sunrise-hospital/
├── /conditions/                    ← hub page linking to all below
│   ├── /nose-sinus/
│   │   ├── /sinusitis/
│   │   ├── /deviated-nasal-septum/   (live: /septoplasty-deviated-septum/)
│   │   ├── /allergic-rhinitis/
│   │   └── /nasal-polyps/
│   ├── /ear-hearing/
│   │   ├── /hearing-loss/
│   │   ├── /ear-infections/
│   │   ├── /ear-discharge-eardrum-perforation/ (live: /ear-surgery-eardrum/)
│   │   └── /tinnitus/
│   ├── /vertigo-balance/            (live)
│   │   ├── /bppv/
│   │   ├── /vestibular-migraine/
│   │   └── /menieres-disease/
│   ├── /throat-voice/
│   │   ├── /voice-disorders/         (live: /voice-swallowing/)
│   │   └── /tonsillitis/
│   ├── /sleep/
│   │   └── /snoring-sleep-apnoea/    (live)
│   └── /children/
│       ├── /pediatric-ent/           (live)
│       ├── /adenoids-tonsils-children/ (live: /tonsil-adenoid-surgery/)
│       └── /glue-ear-children/
├── /procedures/
│   ├── /nasal-endoscopy/  /septoplasty/  /fess/  /tympanoplasty/  /tonsillectomy-adenoidectomy/
├── /blog/                          ← seasonal and FAQ-style articles
├── /faq/
├── /book-appointment/
├── /privacy/ /terms/ /disclaimer/  (live)
```

**[Recommendation]** Don't rename existing live URLs that already work. Google has already found them. If you must rename one, add a **301 redirect** from the old URL to the new one.

## C. Technical SEO in plain language

| Topic | What it means | What to do |
|---|---|---|
| **Indexing** | Google must find and store your pages before they can rank | Verify the domain in Google Search Console; submit `sitemap.xml`; use "URL Inspection → Request indexing" for each new page |
| **Crawlability** | Google follows links from page to page | Every page should be reachable by a normal link from the menu or a hub page. Links that only work through JavaScript or `#` anchors don't count as separate pages |
| **robots.txt** | A file telling Google what it may visit | Keep `User-agent: * Allow: /` and a `Sitemap:` line pointing to the **live** sitemap |
| **Sitemap** | A list of all your real pages | List only live URLs that return 200, with trailing slashes exactly as the site uses them. Regenerate it whenever you add pages |
| **Canonical tag** | Tells Google which URL is the "official" copy | Each page has `<link rel="canonical" href="https://drbibinent.com/<that-page>/">`. Choose one of `www` or non-`www` and redirect the other |
| **HTTPS** | Secure padlock | Already on HTTPS; make sure `http://` redirects to `https://` |
| **Mobile** | Google ranks using the mobile version | Tap targets at least 44 px; phone and WhatsApp buttons visible without scrolling; sticky "Call" bar at the bottom on mobile |
| **Speed / Core Web Vitals** | How fast the page loads and becomes stable | Targets: LCP < 2.5 s, INP < 200 ms, CLS < 0.1. Test at pagespeed.web.dev. Compress images to WebP, ideally under 150 KB; lazy-load images below the fold; set width/height on images; avoid heavy sliders |
| **Structured data** | Machine-readable labels (doctor, address, hours) | Add section F code |
| **404s** | Broken pages | Check GSC → Pages → "Not found" monthly |

## D. On-page SEO template (apply to every condition page)

- **Title** (≤ 60 characters): `<Condition> Treatment in Pala | Dr. Bibin Baby ENT`
- **Meta description** (≤ 155 characters): what it is + who it's for + where + soft CTA
- **One H1** containing the condition name and "Pala"
- **First 100 words:** plain-language answer to "what is it?"
- **H2 sections:** Symptoms → Causes → When to see a doctor → How it's diagnosed → Treatment options → Recovery → FAQs
- **Author box:** "Written and medically reviewed by Dr. Bibin Baby, MS ENT, DNB ENT – Reg. No. … – Last reviewed: <date>"
- **References** list (guidelines, see file 4)
- **CTA:** "To book a consultation at Pala or Erattupetta, call +91 96455 64883 or WhatsApp." No urgency tricks.
- **Medical disclaimer:** "This page is for general information and does not replace a medical consultation."
- **Internal links:** 3–5 links to related conditions plus 1 to the location page.

## E. Internal linking rules
1. Home → every hub page (Nose & Sinus, Ear & Hearing, Vertigo, Throat & Voice, Sleep, Children).
2. Hub → each condition page; each condition page → its hub and 2–4 related pages (for example Sinusitis ↔ Deviated septum ↔ Allergic rhinitis ↔ Nasal polyps; Snoring ↔ Adenoids ↔ Deviated septum).
3. Blog posts → the matching condition page using descriptive link text ("treatment for sinusitis in Pala"), never "click here".
4. Every page → a location page and the booking page.

## F. Ready-to-paste schema (JSON-LD)

Put this in the `<head>` of the **homepage** and the **Pala location page**. Replace the `XX:XX` hours, the registration number and the GBP map URL. Validate it at search.google.com/test/rich-results.

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": ["MedicalClinic", "MedicalBusiness"],
      "@id": "https://drbibinent.com/#clinic-pala",
      "name": "Dr. Bibin Baby – ENT Consultation",
      "url": "https://drbibinent.com/",
      "telephone": "+91-96455-64883",
      "image": "https://drbibinent.com/images/dr-bibin-baby-ent-clinic-pala.jpg",
      "medicalSpecialty": "Otolaryngologic",
      "address": {
        "@type": "PostalAddress",
        "streetAddress": "Opposite Lalam Church Road",
        "addressLocality": "Pala",
        "addressRegion": "Kerala",
        "postalCode": "686575",
        "addressCountry": "IN"
      },
      "hasMap": "https://maps.app.goo.gl/xQgMM4eWyyvT7hyx7",
      "openingHoursSpecification": [
        {
          "@type": "OpeningHoursSpecification",
          "dayOfWeek": ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"],
          "opens": "XX:XX",
          "closes": "XX:XX"
        }
      ],
      "availableLanguage": ["Malayalam","English","Hindi"],
      "areaServed": ["Pala","Bharananganam","Ramapuram","Erattupetta","Kottayam district"],
      "employee": { "@id": "https://drbibinent.com/#dr-bibin-baby" }
    },
    {
      "@type": "Physician",
      "@id": "https://drbibinent.com/#dr-bibin-baby",
      "name": "Dr. Bibin Baby",
      "url": "https://drbibinent.com/about-dr-bibin-baby/",
      "medicalSpecialty": "Otolaryngologic",
      "telephone": "+91-96455-64883",
      "address": { "@type": "PostalAddress", "addressLocality": "Pala", "addressRegion": "Kerala", "postalCode": "686575", "addressCountry": "IN" },
      "hasCredential": [
        { "@type": "EducationalOccupationalCredential", "credentialCategory": "degree", "name": "MBBS", "recognizedBy": { "@type": "CollegeOrUniversity", "name": "Government Medical College, Kottayam" } },
        { "@type": "EducationalOccupationalCredential", "credentialCategory": "degree", "name": "MS (Otorhinolaryngology)", "recognizedBy": { "@type": "CollegeOrUniversity", "name": "Government Medical College, Kozhikode" } },
        { "@type": "EducationalOccupationalCredential", "credentialCategory": "degree", "name": "DNB (ENT)", "recognizedBy": { "@type": "Organization", "name": "National Board of Examinations in Medical Sciences" } }
      ],
      "memberOf": [
        { "@type": "Organization", "name": "Association of Otolaryngologists of India" },
        { "@type": "Organization", "name": "Indian Medical Association" }
      ],
      "knowsLanguage": ["ml","en","hi"],
      "sameAs": ["https://www.instagram.com/dr.bibin/"]
    },
    {
      "@type": "WebSite",
      "@id": "https://drbibinent.com/#website",
      "url": "https://drbibinent.com/",
      "name": "Dr. Bibin Baby ENT"
    }
  ]
}
</script>
```

**Condition pages:** add `MedicalWebPage` with `about` → `MedicalCondition`, plus `BreadcrumbList`:

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "MedicalWebPage",
  "name": "Sinusitis: Symptoms, Causes and Treatment",
  "url": "https://drbibinent.com/sinusitis/",
  "about": { "@type": "MedicalCondition", "name": "Sinusitis", "alternateName": "Rhinosinusitis" },
  "audience": "https://schema.org/Patient",
  "lastReviewed": "2026-11-23",
  "reviewedBy": { "@id": "https://drbibinent.com/#dr-bibin-baby" },
  "author": { "@id": "https://drbibinent.com/#dr-bibin-baby" }
}
</script>
```

Notes:
- **FAQPage** schema no longer gives FAQ rich results to most sites (Google limited this in 2023). It is still fine to add, but expect no visual change in search results.
- **Do not** add `aggregateRating` or `review` markup to your own pages. Google ignores self-serving review markup for local businesses, and showing ratings may count as a testimonial under the NMC rules.

## G. Image SEO
- File names: `septoplasty-deviated-septum-diagram.webp`, not `IMG_2034.jpg`.
- Alt text describes the image: `alt="Diagram of a deviated nasal septum blocking one side of the nose"`.
- Use diagrams and illustrations, never patient photos or endoscopy images (NMC).
- WebP format, ≤ 150 KB, with explicit `width` and `height`.

## H. Off-page SEO (ethical authority building)

| Action | Why | Effort |
|---|---|---|
| Consistent NAP (name, address, phone) on Practo, Justdial, Sulekha, Lybrate, Google, Bing Places, Apple Business Connect | Citations confirm you're real and local | Low |
| Profile on Sunrise Hospital's website linking to drbibinent.com | Strong local relevance link | Low |
| AOI / IMA Kottayam or Pala branch member listings | Professional authority | Low |
| Health talks at schools, parishes, Rotary/Lions clubs, with coverage on their sites or local news | Real-world authority and local links | Medium |
| Expert quotes for Malayalam media (Manorama Online already did one; ask for a link to your site) | Authoritative links | Medium |
| CME talks or publications listed on the About page | E-E-A-T (expertise) | Medium |
| **Never:** buy backlinks, PBNs, link exchanges, "SEO packages" offering 500 links | Can get the site penalised | – |
