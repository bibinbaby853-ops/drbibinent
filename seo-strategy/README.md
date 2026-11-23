# Dr. Bibin Baby ENT – Google Search & Maps Strategy

**Prepared:** 23 November 2026 · **Practice:** Dr. Bibin Baby – ENT Consultation, opposite Lalam Church Road, Pala, Kerala 686575 · **Second consulting location:** Sunrise Hospital, Erattupetta

This folder is a working playbook. Every file is ready to act on: copy-paste text, checklists, templates.

| # | File | What it gives you |
|---|------|-------------------|
| 1 | [01-google-business-profile.md](01-google-business-profile.md) | GBP audit checklist, categories, ready description, services, Q&A, photo plan |
| 2 | [02-local-keywords.md](02-local-keywords.md) | Keyword map by page and town, Malayalam/Manglish variants, how to validate volume |
| 3 | [03-website-seo.md](03-website-seo.md) | Live-site fixes, ideal site structure, technical SEO in plain language, ready schema |
| 4 | [04-medical-content.md](04-medical-content.md) | 10 complete article briefs (title, meta, keywords, headings, FAQs, links, references, CTA) |
| 5 | [05-gbp-30-day-calendar.md](05-gbp-30-day-calendar.md) | 30 ready-to-post Google Business Profile updates |
| 6 | [06-review-strategy.md](06-review-strategy.md) | NMC-aware feedback strategy and review-response templates |
| 7 | [07-competitor-analysis.md](07-competitor-analysis.md) | Who you compete with in Pala/Kottayam and where the gaps are |
| 8 | [08-measurement.md](08-measurement.md) | Search Console, GA4 and GBP tracking set-up and monthly KPI sheet |
| 9 | [09-90-day-roadmap.md](09-90-day-roadmap.md) | Prioritised 90-day plan (impact / difficulty / cost) |

---

## Verified findings vs. recommendations

Throughout these files, **[Verified]** means I checked it on 23 Nov 2026. **[Recommendation]** means my advice. **[Verify]** means you need to confirm it yourself, because I could not see it (for example, your private GBP dashboard).

### What I verified

1. **[Verified] The live website is not the code in this repository.** `https://drbibinent.com/` serves a large single-page site titled *"Dr. Bibin Baby — Consultant ENT Surgeon | Pala, Kottayam"* with clean URLs such as `/sinus-surgery-fess/`, `/vertigo-balance/`, `/pediatric-ent/`, `/snoring-sleep-apnoea/`, `/voice-swallowing/`. The repo's pages (`about.html`, `sinus.html` …) and its `sitemap.xml` (listing `/about`, `/sinus`, `/vertigo`, `/blog`, `/contact`) do **not** match it: `https://drbibinent.com/about` returns **404**. If this repo's `sitemap.xml` is ever uploaded, it will send Google to pages that don't exist. Decide which version is the real site and keep only one.
2. **[Verified] Live-site problems that cost you patients today:**
   - The phone number and the "WhatsApp the Clinic" / "Book a Vertigo Consultation" buttons link to `#` (they do nothing on a phone).
   - The footer still shows the placeholders `[[REGISTRATION-NUMBER]]` and `[[REGISTERING-COUNCIL]]`. For a doctor's site this is the single biggest trust (E-E-A-T) gap: fill in your TCMC registration.
   - No consultation timings are published.
   - No embedded Google Map; only "Get Directions" short links.
   - The "Blog" menu item goes to an external Manorama Online article, not to articles on your own site.
   - The "Questionnaires" menu item is a placeholder (`#`).
   - I could not confirm any structured data (schema) on the live homepage. Test it in Google's Rich Results Test.
3. **[Verified] Your site already appears** in web search results for "ENT specialist Pala Kottayam Kerala", alongside Mar Sleeva Medicity, Carmel Medical Centre Pala and Practo listings. A search for "Dr Bibin Baby ENT Pala" did **not** surface you; it returned similarly named doctors elsewhere. Your **brand-name search** is weak, which is the easiest thing to fix (GBP + consistent citations).
4. **[Verified] New NMC rules apply.** NMC's *Guidelines on Ethical Advertising and Public Communication* (public notice of 6 Oct 2026, effective immediately) bring Google profiles, social media and AI content under advertising rules, and bar testimonials and fake or paid reviews. Secondary sources disagree on whether *asking* patients for Google reviews is allowed. The review strategy (file 6) is written around this. Read the official NMC notice, or take advice from TCMC or a lawyer, before running any review-request campaign.
5. **[Verified] Qualifications on the live site:** MBBS (GMC Kottayam), MS ENT (GMC Kozhikode), DNB ENT, AVISA vestibular medicine programme, paediatric vestibular course, AOI and IMA membership; languages Malayalam, English, Hindi. All content in this folder uses only these facts.

### What I could not verify
- Your Google Business Profile: whether it exists, is verified, or which categories, photos and reviews it has. Use the checklist in file 1.
- Real keyword search volumes. I had no Keyword Planner or rank-tracker access, so **no volumes are invented here**. File 2 shows how to get real numbers free.
- Competitors' Google Maps ratings and review counts. File 7 includes a 15-minute manual method.

## The five things to do this week
1. Fix the dead phone and WhatsApp buttons (`tel:+919645564883`, `https://wa.me/919645564883`).
2. Replace the registration placeholders with your real TCMC number.
3. Claim and verify the Google Business Profile, then complete it with file 1.
4. Publish consultation hours on the site and GBP, identical in both.
5. Add Physician/MedicalClinic schema (ready code in file 3) and submit the site in Google Search Console.
