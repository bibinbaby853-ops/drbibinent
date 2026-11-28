// Smoke-test a built Worker module locally (Node 18+), without deploying.
// Usage: node tools/check_worker.mjs dist/worker.mjs [compare.mjs]
// Requests every embedded file plus redirect and 404 cases; with a second
// module, also checks that both serve identical bodies for every path.
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
const load = async (p) => (await import(pathToFileURL(p).href)).default;
const [a, b] = process.argv.slice(2);
const W = await load(a), W2 = b ? await load(b) : null;
const src = await import("node:fs").then((fs) => fs.readFileSync(a, "utf8"));
const keys = [...src.matchAll(/^"(\/[^"]*)": \{$/gm)].map((m) => m[1]);
const get = async (w, path, init) => w.fetch(new Request("https://drbibinent.com" + path, init));
const hash = async (r) => createHash("sha256").update(Buffer.from(await r.arrayBuffer())).digest("hex");
let fail = 0;
const check = (ok, msg) => { if (!ok) { fail++; console.log("FAIL", msg); } };
for (const k of keys) {
  const path = k.endsWith("/index.html") ? k.slice(0, -10) : k;
  const r = await get(W, path);
  check(r.status === 200, `${path} -> ${r.status}`);
  if (W2) {
    const r2 = await get(W2, path);
    const [h1, h2] = [await hash(r), await hash(r2)];
    check(r2.status === 200 && h1 === h2 && r.headers.get("content-type") === r2.headers.get("content-type"), `${path} differs from comparison worker`);
  }
}
const red = await get(W, "/vertigo-balance");
check(red.status === 307 && red.headers.get("location") === "https://drbibinent.com/vertigo-balance/", "directory redirect");
const idx = await get(W, "/index.html?utm_source=instagram");
check(idx.status === 307 && idx.headers.get("location") === "https://drbibinent.com/?utm_source=instagram", "index.html redirect keeps query string");
check((await get(W, "/no-such-page")).status === 404, "404 for unknown path");
check((await get(W, "/", { method: "POST" })).status === 405, "405 for POST");
const home = await get(W, "/");
check((await get(W, "/", { headers: { "if-none-match": home.headers.get("etag") } })).status === 304, "304 on matching etag");
console.log(`${keys.length} files checked, ${fail} failure(s)`);
process.exit(fail ? 1 : 0);
