# Stage 2 – Integration map

The website is the hub. Every platform sends people to it with a tracked link (see LINKS.md), and the website links back out to each official profile and to the patient-contact actions: call, WhatsApp, directions and the appointment form.

```mermaid
flowchart LR
  subgraph Discover["Where patients find the practice"]
    GS[Google Search]
    GBP[Google Business Profile<br/>and Maps]
    IG[Instagram<br/>@dr.bibin]
    FB[Facebook<br/>/drbibinent]
    YT[YouTube<br/>channel to be confirmed]
    LI[LinkedIn]
    DIR[Health directories<br/>to be confirmed]
  end

  subgraph Hub["drbibinent.com"]
    HOME[Home page<br/>about · services · FAQ]
    SVC[8 service pages]
    APPT[#appointment<br/>WhatsApp request form]
    LOC[#locations<br/>Pala · Erattupetta]
    Q[Symptom questionnaires]
  end

  subgraph Act["Patient actions"]
    CALL[Call +91 96455 64883]
    WA[WhatsApp chat]
    MAPS[Directions in Google Maps]
  end

  GS -->|organic results| HOME & SVC
  GBP -->|Website field: utm gbp_pala| HOME
  GBP -->|Appointment link| APPT
  IG -->|bio link: utm bio| HOME
  IG -->|story links| SVC
  FB -->|Website and Book now button| HOME & APPT
  YT -->|channel links and video descriptions| HOME
  LI -->|profile website| HOME
  DIR -.->|to confirm| HOME

  HOME --> APPT & LOC & Q & SVC
  SVC --> APPT
  APPT --> WA
  HOME & SVC --> CALL & WA
  LOC --> MAPS --> GBP

  HOME -->|header and footer icons| IG & FB & LI
  SVC -->|footer links, added| IG & FB & LI
  HOME -. YouTube icon once channel confirmed .-> YT
  HOME -. Leave a Google review link once confirmed .-> GBP
  WA -->|business profile: website| HOME
```

## Cross-link checklist

Each row is one link that should exist. ✅ means it is in the website source and was tested in a browser. 🔧 means a manual step is needed on that platform (see PLATFORM_PLAYBOOK.md). ❓ means it is waiting for details from the practice.

| From | To | Status |
|---|---|---|
| Website (all pages) | Phone, WhatsApp | ✅ |
| Website (home, service pages) | Google Maps directions | ✅ |
| Website (home) | Embedded maps for both locations | ✅ |
| Website (all pages) | Instagram, Facebook, LinkedIn | ✅ (service and legal pages added) |
| Website | YouTube | ❓ channel URL needed |
| Website | Google reviews | ❓ review link needed |
| Website (structured data) | Instagram, Facebook, LinkedIn as `sameAs` | ✅ |
| Google Business Profile | Website, appointment link, social profiles | 🔧 |
| Instagram | Website (bio), appointment, Facebook | 🔧 |
| Facebook | Website, WhatsApp button, Instagram, Book now | 🔧 |
| YouTube | Website, Instagram, Facebook, contact | 🔧 ❓ |
| WhatsApp Business | Website, description, social links | 🔧 |
| LinkedIn | Website | 🔧 |
