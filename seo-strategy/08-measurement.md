# 8. Monitoring – Search Console, GA4 & GBP Performance

## A. Set-up (one time, ~2 hours)

### Google Search Console (GSC)
1. search.google.com/search-console → Add property → **Domain** → `drbibinent.com` → verify with the DNS TXT record at your domain registrar.
2. Sitemaps → submit `https://drbibinent.com/sitemap.xml` (live URLs only).
3. Pages report → fix anything under "Not found (404)" or "Crawled – currently not indexed".
4. Link GSC to GA4 (GA4 Admin → Product links → Search Console).

### Google Analytics 4 (GA4)
1. Create a GA4 property and add the tag to every page (or via Google Tag Manager).
2. **Key events (conversions)** to create:
   - `click_call`: clicks on `tel:` links
   - `click_whatsapp`: clicks on `wa.me` links
   - `form_submit_appointment`: appointment form sent
   - `click_directions`: clicks on Maps/directions links
   Implementation: in GTM, use a *Click – Just Links* trigger where `Click URL` starts with `tel:` / contains `wa.me` / contains `maps`.
3. Turn on Enhanced Measurement (scrolls, outbound clicks).
4. **Privacy:** don't send names, phone numbers or symptoms to GA4. Add a cookie and privacy notice in line with India's DPDP Act, 2023.

### Separate GBP traffic with UTM tags
- GBP website link: `https://drbibinent.com/?utm_source=google&utm_medium=organic&utm_campaign=gbp`
- GBP appointment link: `…&utm_campaign=gbp_booking`
- GBP post buttons: `…&utm_campaign=gbp_post_<topic>`
In GA4 this lets you see "Google Maps / GBP" traffic separately from normal Google search.

### GBP Performance
Business Profile → Performance: interactions, calls, website clicks, direction requests, bookings/messages, and **"Searches breakdown"** (the actual search terms people used).

### Call tracking (low-cost, ethical)
- Ask new patients "How did you hear about us?" at reception (Google Maps / Google search / Practo / referral / other) and keep a simple tally. This is often more accurate than any tool.

## B. Monthly KPI dashboard (copy into a Google Sheet)

| KPI | Source | Baseline (Month 0) | M1 | M2 | M3 | Notes |
|---|---|---|---|---|---|---|
| Organic clicks | GSC → Performance | | | | | |
| Impressions | GSC | | | | | |
| Average CTR | GSC | | | | | If CTR is low on a high-impression page, rewrite its title and meta |
| Avg. position for 10 tracked queries | GSC (filter by query) | | | | | Queries from file 2 |
| Brand queries (contains "bibin") | GSC | | | | | |
| Pages indexed | GSC → Pages | | | | | |
| Website users (organic) | GA4 | | | | | |
| Calls from website (`click_call`) | GA4 | | | | | |
| WhatsApp clicks | GA4 | | | | | |
| Appointment form submissions | GA4 | | | | | |
| GBP calls | GBP Performance | | | | | |
| GBP website clicks | GBP Performance | | | | | |
| GBP direction requests | GBP Performance | | | | | |
| GBP searches (discovery vs brand) | GBP Performance | | | | | |
| New reviews / avg rating / response rate | GBP | | | | | |
| Map Pack rank for 6 queries | Manual audit (file 7 B) | | | | | |
| New patients citing Google | Reception tally | | | | | **The metric that matters most** |
| Core Web Vitals pass (mobile) | GSC → Core Web Vitals | | | | | |

## C. How to read the numbers
- **Impressions rising, clicks flat** → titles and metas aren't compelling, or you rank on page 2. Improve the page and its titles.
- **Clicks rising, calls flat** → a conversion problem: check phone and WhatsApp buttons, timings, and mobile layout.
- **GBP "direction requests" rising** → Map Pack visibility is improving.
- Expect **4–12 weeks** before new content shows meaningful impressions. Local SEO is cumulative. No ranking position is guaranteed.

## D. Monthly 30-minute review ritual
1. GSC: top 20 queries, new queries, pages with high impressions and CTR below 2%.
2. GA4: conversions by source.
3. GBP: calls, directions, search terms, reviews.
4. Manual Map Pack check (6 queries).
5. Choose 3 actions for next month.
