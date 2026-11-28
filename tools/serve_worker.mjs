// Serve a built Worker module on http://localhost:PORT for local testing.
// Usage: node tools/serve_worker.mjs dist/worker.mjs [port]
import http from "node:http";
import { pathToFileURL } from "node:url";
const [file, port = "8787"] = process.argv.slice(2);
const worker = (await import(pathToFileURL(file).href)).default;
http.createServer(async (req, res) => {
  const r = await worker.fetch(new Request("https://drbibinent.com" + req.url, { method: req.method, headers: req.headers }));
  const headers = Object.fromEntries(r.headers);
  if (headers.location) headers.location = headers.location.replace("https://drbibinent.com", "http://localhost:" + port);
  res.writeHead(r.status, headers);
  res.end(Buffer.from(await r.arrayBuffer()));
}).listen(+port, () => console.log("serving " + file + " on http://localhost:" + port));
