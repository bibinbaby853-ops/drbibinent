// Desktop + mobile browser check of every page served by tools/serve_worker.mjs.
// Usage: node tools/browser_check.js http://localhost:8787 [screenshot-dir]
const { chromium, devices } = require("playwright");
const base = process.argv[2] || "http://localhost:8787";
const shots = process.argv[3];
const pages = ["/", "/vertigo-balance/", "/sinus-surgery-fess/", "/septoplasty-deviated-septum/",
  "/ear-surgery-eardrum/", "/tonsil-adenoid-surgery/", "/snoring-sleep-apnoea/", "/pediatric-ent/",
  "/voice-swallowing/", "/privacy/", "/terms/", "/disclaimer/"];
const profiles = { desktop: { viewport: { width: 1366, height: 900 } }, mobile: devices["Pixel 7"] };
(async () => {
  const browser = await chromium.launch();
  let fail = 0;
  for (const [name, opts] of Object.entries(profiles)) {
    const ctx = await browser.newContext(opts);
    // External hosts are unreachable from the test box; block them so pages settle.
    await ctx.route(/^https?:\/\/(?!localhost)/, (r) => r.abort());
    for (const path of pages) {
      const page = await ctx.newPage();
      const errs = [];
      page.on("pageerror", (e) => errs.push(e.message));
      const resp = await page.goto(base + path, { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(300);
      const r = await page.evaluate(() => {
        const hrefs = [...document.querySelectorAll("a[href]")].map((a) => a.href);
        const has = (re) => hrefs.some((h) => re.test(h));
        return {
          overflow: document.documentElement.scrollWidth - window.innerWidth,
          tel: has(/^tel:\+919645564883$/), wa: has(/^https:\/\/wa\.me\/919645564883/),
          insta: has(/instagram\.com\/dr\.bibin\/$/), fb: has(/facebook\.com\/drbibinent$/),
          li: has(/linkedin\.com\/in\/bibin-baby-75a553154$/), maps: has(/maps\.app\.goo\.gl\//),
          beacon: !!document.querySelector('script[src*="cloudflareinsights"]'),
          ogimg: !!document.querySelector('meta[property="og:image"]'),
        };
      });
      const legal = ["/privacy/", "/terms/", "/disclaimer/"].includes(path);
      const need = legal ? ["insta", "fb", "li", "beacon", "ogimg"] : ["tel", "wa", "insta", "fb", "li", "maps", "beacon", "ogimg"];
      const missing = need.filter((k) => !r[k]);
      const ok = resp.status() === 200 && r.overflow <= 1 && !missing.length && !errs.length;
      if (!ok) fail++;
      console.log(`${ok ? "ok  " : "FAIL"} ${name.padEnd(7)} ${path.padEnd(32)} status=${resp.status()} overflow=${r.overflow}px` +
        (missing.length ? ` missing=${missing}` : "") + (errs.length ? ` js-errors=${JSON.stringify(errs)}` : ""));
      if (shots && (path === "/" || path === "/vertigo-balance/"))
        await page.screenshot({ path: `${shots}/${name}${path === "/" ? "-home" : path.replace(/\//g, "-").replace(/-$/, "")}.png`, fullPage: false });
      await page.close();
    }
    if (name === "mobile") {
      // Appointment form must hand off to WhatsApp with the patient's details.
      const page = await ctx.newPage();
      await page.goto(base + "/#appointment");
      await page.fill("#fName", "Test Patient"); await page.fill("#fPhone", "9000000000");
      let opened = null;
      await page.exposeFunction("__opened", (u) => { opened = u; });
      await page.evaluate(() => { window.open = (u) => { window.__opened(u); return null; }; });
      await page.click("#apptSubmit"); await page.waitForTimeout(300);
      const formOk = !!opened && opened.startsWith("https://wa.me/919645564883?text=") && decodeURIComponent(opened).includes("Test Patient");
      if (!formOk) fail++;
      console.log(`${formOk ? "ok  " : "FAIL"} mobile  appointment form -> ${opened ? opened.slice(0, 60) + "…" : "nothing opened"}`);
      // Questionnaire deep link from a service page opens the overlay.
      await page.goto(base + "/#q/dhi"); await page.waitForTimeout(500);
      const qOk = await page.evaluate(() => getComputedStyle(document.getElementById("qOverlay")).display !== "none");
      if (!qOk) fail++;
      console.log(`${qOk ? "ok  " : "FAIL"} mobile  questionnaire deep link #q/dhi opens overlay`);
    }
    await ctx.close();
  }
  await browser.close();
  console.log(fail ? `${fail} failure(s)` : "all checks passed");
  process.exit(fail ? 1 : 0);
})();
