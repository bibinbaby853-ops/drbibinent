# Publishing drbibinent.com

The live site is the Cloudflare Worker **red-wave-f642**, attached to drbibinent.com and
www.drbibinent.com. The source files are in `website/`.

## Published 24 Nov 2026
- Version `4d5aaf69-9130-423a-9d7e-80143b1d1450` (100% of traffic).
- The pages are embedded in the Worker code, built by `tools/build_worker.py`.
  Cloudflare's usual drag-and-drop asset upload could not be done from the cloud
  session (see the note in that script).
- Visitors see the same behaviour as before: `/page` redirects to `/page/`,
  unknown pages return 404, and both domains serve the site.
- `READ-THIS-FIRST.txt` and `SEO-CHANGES.txt` are internal notes. They are not
  published.

## Roll back to the previous site (11 Oct 2026)
Cloudflare dashboard → Workers & Pages → **red-wave-f642** → Deployments, then pick
version `2483acdf-fb40-4835-9a01-c3040e0ed600` → Deploy.

## Publish a future change
1. Edit files in `website/`.
2. `python3 tools/build_worker.py website out/worker.js`
3. Upload `out/worker.js` as the Worker's module. The metadata needs
   `main_module` set to `worker.js`, `compatibility_date` set to `2026-10-11` and
   `bindings` set to `[]`. Upload with
   `PUT /accounts/<account>/workers/scripts/red-wave-f642`.

Alternatively, upload the contents of `website/` through the Cloudflare dashboard.
Leave out the two notes files. Uploaded static files are served before the Worker
code, so a dashboard upload takes over the site.
