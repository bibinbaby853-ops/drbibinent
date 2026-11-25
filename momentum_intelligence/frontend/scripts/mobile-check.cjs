// Mobile check: every page at an Android width (default 360x780). Fails on console errors, failed API
// calls, horizontal overflow, or a page without its heading. Saves a screenshot per page.
// Usage: node scripts/mobile-check.cjs http://127.0.0.1:8000 out-dir [scale]
const { chromium } = require("playwright");
const fs = require("fs");

const base = process.argv[2] || "http://127.0.0.1:8000";
const out = process.argv[3] || "screens";
const scale = Number(process.argv[4] || 1);
const pages = [
  ["/", "Command Centre"], ["/market", "Market Overview"], ["/scanner", "All-Stock Scanner"], ["/top", "Top 20 Opportunities"],
  ["/breakouts", "Breakout Watchlist"], ["/pullbacks", "Pullback Watchlist"], ["/sectors", "Sector Rotation"],
  ["/portfolio", "Paper Portfolio"], ["/intraday", "Intraday Monitoring"], ["/risk", "Risk Analytics"],
  ["/backtests", "Backtesting Lab"], ["/journal", "Trade Journal"], ["/report", "AI Daily Report"], ["/settings", "Settings"],
];

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 360, height: 780 }, deviceScaleFactor: scale, isMobile: true, hasTouch: true });
  const results = [];
  let stockPath = null;
  for (const [path, heading] of pages) results.push(await check(ctx, path, heading));
  // a stock detail page, taken from the Top 20 page
  const p = await ctx.newPage();
  await p.goto(base + "/top", { waitUntil: "networkidle" });
  stockPath = await p.locator(".sc-symbol").first().getAttribute("href").catch(() => null);
  await p.close();
  if (stockPath) results.push(await check(ctx, stockPath, decodeURIComponent(stockPath.split("/").pop())));
  await browser.close();
  let failed = 0;
  for (const r of results) {
    const bad = r.errors.length || r.overflow > 0 || !r.heading;
    failed += bad ? 1 : 0;
    console.log(`${bad ? "FAIL" : "ok  "} ${r.path.padEnd(22)} heading=${r.heading} overflow=${r.overflow}px ${r.errors.join(" | ")}`);
  }
  console.log(`${results.length - failed}/${results.length} pages passed`);
  process.exit(failed ? 1 : 0);

  async function check(ctx, path, heading) {
    const page = await ctx.newPage();
    const errors = [];
    page.on("console", (m) => m.type() === "error" && errors.push("console: " + m.text().slice(0, 160)));
    page.on("pageerror", (e) => errors.push("pageerror: " + String(e).slice(0, 160)));
    page.on("response", (r) => r.url().includes("/api/") && r.status() >= 500 && errors.push(`HTTP ${r.status()} ${r.url().replace(base, "")}`));
    await page.goto(base + path, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(400);
    const h1 = await page.locator("h1").first().textContent().catch(() => null);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    const file = `${out}/${path === "/" ? "command-centre" : path.replace(/^\//, "").replace(/\//g, "-")}.png`;
    await page.screenshot({ path: file, fullPage: true });
    await page.close();
    return { path, heading: h1 && h1.trim() === heading, overflow, errors };
  }
})();
