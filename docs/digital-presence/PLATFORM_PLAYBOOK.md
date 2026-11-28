# Stage 3 – Platform playbook (manual steps)

These changes have to be made by the practice, logged in to each platform. No account in this list is connected to the automation, and nothing here is posted or changed automatically. Copy the links from **LINKS.md**: each one is tagged so its visits can be counted.

The profile text below uses only facts already published on drbibinent.com. Under the NMC Registered Medical Practitioner (Professional Conduct) Regulations, keep profiles factual: no patient testimonials or before/after images, no "best" or "No. 1" claims, no guaranteed outcomes, and no fees offered as inducements. Share patient images or stories only with written consent.

---

## 1. Google Business Profile (highest impact: 38% of visits already come from Google)

1. Open business.google.com and check whether a profile already exists for the Pala consultation. Search Maps for "Dr. Bibin Baby" and "ENT Pala". If two exist, **request removal of the duplicate** instead of keeping both.
2. Name: **Dr. Bibin Baby – ENT Consultation** (confirmed), typed exactly like this. Do not add keywords to the name: Google suspends profiles for that.
3. Primary category: **Otolaryngologist**. Additional category: *ENT clinic* only if the Pala address is a clinic that patients visit.
4. Address and pin: Opposite Lalam Church Road, Pala, Kerala 686575. Check the pin is on the right building.
5. Phone: +91 96455 64883.
6. Website: the `gbp_pala` link in LINKS.md. Appointment link: the `gbp_pala_appointment` link.
7. Hours: enter the real consultation hours. These are **needed from the practice** and are also missing from the website.
8. Description (≤750 characters; no links):

   > Dr. Bibin Baby is a consultant ENT surgeon (MBBS, MS ENT, DNB ENT; Kerala State Medical Council registration 66908) seeing patients in Pala and at Sunrise Hospital, Erattupetta. Care covers ear, nose and throat problems in adults and children, including endoscopic sinus surgery, septoplasty, ear and eardrum surgery, tonsil and adenoid surgery, snoring and sleep apnoea, and voice and swallowing problems. Vertigo and balance disorders are treated as an allied speciality, with training in vestibular medicine. Consultations in Malayalam, English and Hindi. Appointments by phone or WhatsApp.

9. Services: add the eight service names from the website, using the same wording as its pages.
10. Social profiles (Edit profile > Social profiles): Instagram, Facebook, YouTube (once confirmed), LinkedIn.
11. Reviews: **Ask for reviews > copy the short link**, and send it to the developer to add to `presence/profile.json` (`google_review_url`). A "Leave a Google review" button will then be added to the website. Do not offer anything in exchange for reviews.
12. Erattupetta: Sunrise Hospital usually owns its own listing. A separate practitioner listing at the hospital is allowed under Google's rules for individual practitioners. Create one only if the practice wants it, using the `gbp_erattupetta` link.

## 2. Instagram (@dr.bibin)

- Switch to a **Professional > Business** account (category: *Doctor*) if it is not one already. Contact buttons need this.
- Name field: **Dr Bibin Baby ENT Consultation**. This is the confirmed name shortened to Instagram's 30-character limit; the name field is searchable, so "ENT" helps people find the account.
- Bio (142 of 150 characters):
  ```
  Consultant ENT Surgeon · MBBS, MS, DNB (ENT)
  Sinus, ear, throat, vertigo & balance care
  Pala · Sunrise Hospital, Erattupetta
  📞 +91 96455 64883
  ```
- Links (up to 5): `bio` link as the first link, then `bio_appointment` titled "Book appointment", then the Facebook page.
- Contact options: Call and WhatsApp, both +91 96455 64883. Address: the Pala address.
- Link Instagram to the Facebook page in Accounts Center, so the two show each other.
- Highlights: consider "Locations" and "Vertigo" highlights that use the story links in LINKS.md.

## 3. Facebook (/drbibinent)

- Page name: **Dr. Bibin Baby – ENT Consultation**. A Page name change goes to Meta for review and may take a few days.
- Page category: *Doctor* or *Medical & Health*. Intro (≤101): *Consultant ENT surgeon in Pala and at Sunrise Hospital, Erattupetta. Ear, nose, throat and vertigo care.* (104 characters; drop the final "and vertigo care" wording if Facebook rejects it.)
- Website: the `page_website` link. Phone, address and hours: the same as Google.
- Action button: **WhatsApp** (connect the WhatsApp Business number) or **Book now** pointing to the `page_book_button` link. Facebook allows one button, so choose WhatsApp if most enquiries come in by chat.
- Linked accounts: connect the Instagram account and add the YouTube channel once confirmed.

## 4. YouTube (channel to be confirmed)

- Channel name: **Dr. Bibin Baby – ENT Consultation**. If there is no channel, create one under the same Google account as the Business Profile, using this name and the same photo.
- Description: the Google description above, plus "Appointments: +91 96455 64883 (call or WhatsApp)".
- Links: website (`channel_links` link), Instagram, Facebook. Use the `video_description` link in each video description.
- Send the channel URL to the developer. A YouTube icon will then be added to the website header and footer, and the channel to the structured data.

## 5. WhatsApp Business (+91 96455 64883)

- Use the **WhatsApp Business** app (not personal WhatsApp) on this number.
- Business name: **Dr. Bibin Baby – ENT Consultation**.
- Business profile: category *Doctor*; description (≤512): the first two sentences of the Google description; address: Pala; hours: as confirmed; website 1: the `business_profile` link; website 2: the Instagram profile.
- Greeting message, suggested: *Thank you for contacting Dr. Bibin Baby's ENT consultation. Please share the patient's name, age and the main concern, and your preferred location (Pala or Sunrise Hospital, Erattupetta). For emergencies, please go to the nearest emergency department or call 108.*
- Away message: the same message plus the `auto_reply` link, which shows the locations and directions.
- Do not ask for, or keep, detailed medical records over WhatsApp. The website's privacy page tells patients the same.

## 6. LinkedIn

- Contact info > Website: the `profile` link (type: *Personal*).
- Headline: *Consultant ENT Surgeon · Pala & Sunrise Hospital, Erattupetta · MBBS, MS, DNB (ENT)*.

## 7. Healthcare directories (to be confirmed)

For each listing that exists (Practo, Justdial, Lybrate, the Sunrise Hospital doctors page), check that the name, phone, address and qualifications match LINKS.md exactly. Add the website link where the directory allows one, and send the listing URL to the developer so the weekly audit can watch it.
