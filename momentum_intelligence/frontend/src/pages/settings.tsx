import { useEffect, useState } from "react";
import { ApiError, api, fmt, getToken, setToken, useApi } from "../api";
import { Card, ErrorBox, Loading, Notice, Page, Table } from "../components/ui";

const FIELDS: { path: string; label: string; step?: number; unit?: string }[] = [
  { path: "simulated_capital_inr", label: "Simulated capital", step: 1000, unit: "₹" },
  { path: "max_positions", label: "Maximum positions", step: 1 },
  { path: "risk_per_position_pct", label: "Risk per position", step: 0.05, unit: "%" },
  { path: "max_position_exposure_pct", label: "Maximum individual exposure", step: 1, unit: "%" },
  { path: "max_sector_exposure_pct", label: "Maximum sector exposure", step: 1, unit: "%" },
  { path: "cash_reserve_pct", label: "Cash reserve", step: 1, unit: "%" },
  { path: "max_initial_aggregate_risk_pct", label: "Maximum aggregate initial risk", step: 0.1, unit: "%" },
  { path: "min_median_traded_value_cr", label: "Minimum median traded value", step: 0.5, unit: "₹ Cr" },
  { path: "liquidity_lookback_sessions", label: "Liquidity look-back", step: 1, unit: "sessions" },
  { path: "holding_period_weeks_min", label: "Holding period min", step: 1, unit: "weeks" },
  { path: "holding_period_weeks_max", label: "Holding period max", step: 1, unit: "weeks" },
  { path: "score_bands.priority", label: "Priority band from", step: 1 },
  { path: "score_bands.strong", label: "Strong band from", step: 1 },
  { path: "score_bands.developing", label: "Developing band from", step: 1 },
  { path: "score_weights.trend", label: "Weight: trend", step: 1 },
  { path: "score_weights.relative_strength", label: "Weight: relative strength", step: 1 },
  { path: "score_weights.volume", label: "Weight: volume", step: 1 },
  { path: "score_weights.price_action", label: "Weight: price action", step: 1 },
  { path: "score_weights.sector", label: "Weight: sector", step: 1 },
  { path: "score_weights.momentum_indicators", label: "Weight: momentum indicators", step: 1 },
  { path: "score_weights.risk_reward", label: "Weight: risk/reward", step: 1 },
  { path: "exits.trail_atr", label: "Trailing stop", step: 0.5, unit: "× ATR" },
  { path: "exits.time_stop_sessions", label: "Time stop", step: 5, unit: "sessions" },
];

const get = (o: any, p: string) => p.split(".").reduce((a, k) => a?.[k], o);
function nest(p: string, v: unknown) { return p.split(".").reverse().reduce((acc: any, k) => ({ [k]: acc }), v as any); }
function merge(a: any, b: any): any { const o = { ...a }; for (const k of Object.keys(b)) o[k] = typeof b[k] === "object" && b[k] && !Array.isArray(b[k]) ? merge(o[k] ?? {}, b[k]) : b[k]; return o; }

export function Settings() {
  const [tok, setTok] = useState(getToken() ?? "");
  const [ver, setVer] = useState(0);
  const cur = useApi<any>("/api/v1/settings", [ver]);
  const hist = useApi<any>("/api/v1/settings/history", [ver]);
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => setDraft({}), [ver]);
  const save = async () => {
    let changes = {};
    for (const [p, v] of Object.entries(draft)) changes = merge(changes, nest(p, v));
    try {
      const r = await api<any>("/api/v1/settings", { method: "PUT", body: JSON.stringify({ changes, note: note || null }) });
      setMsg({ ok: true, text: r.saved ? `Saved ${r.changes.length} change(s). ${r.note}` : "No change." });
      setVer((x) => x + 1);
    } catch (e) {
      const err = e as ApiError;
      let text = err.message;
      try { text = JSON.parse(err.message).map((x: any) => `${x.field}: ${x.error}`).join("; "); } catch { /* plain text */ }
      setMsg({ ok: false, text });
    }
  };
  const s = cur.data?.strategy;
  return (
    <Page title="Settings" sub="Validated on save; every change is kept with a timestamp. Applies from the next end-of-day run.">
      <Card title="Dashboard access token">
        <p className="muted small">Only needed if the server sets MI_API_TOKEN. Kept in this browser tab's session storage; broker and AI keys never reach the browser.</p>
        <div className="row"><input type="password" value={tok} onChange={(e) => setTok(e.target.value)} placeholder="token" aria-label="API token" />
          <button onClick={() => { setToken(tok || null); setVer((x) => x + 1); }}>Use token</button></div>
      </Card>
      <ErrorBox error={cur.error} />
      {cur.loading && <Loading />}
      {s && (
        <Card title="Strategy configuration">
          <Notice>Execution mode: <b>paper</b> (locked; live order placement does not exist in this codebase).</Notice>
          <div className="form-grid">
            {FIELDS.map((f) => {
              const val = draft[f.path] ?? get(s, f.path);
              const def = get(cur.data.defaults, f.path);
              return (
                <label key={f.path} className={draft[f.path] !== undefined ? "changed" : ""}>
                  <span>{f.label}{f.unit ? ` (${f.unit})` : ""}</span>
                  <input type="number" step={f.step} value={val ?? ""} onChange={(e) => setDraft({ ...draft, [f.path]: Number(e.target.value) })} />
                  <small className="muted">default {String(def)}</small>
                </label>
              );
            })}
          </div>
          <div className="row">
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="note (optional)" aria-label="note" />
            <button disabled={!Object.keys(draft).length} onClick={save}>Save {Object.keys(draft).length || ""} change(s)</button>
            <button className="secondary" disabled={!Object.keys(draft).length} onClick={() => setDraft({})}>Discard</button>
          </div>
          {msg && <div className={`state ${msg.ok ? "" : "error"}`} role="status">{msg.text}</div>}
          <p className="muted small">Regime policies, strategy rules and costs are also configurable through PUT /api/v1/settings (same validation and audit).</p>
        </Card>
      )}
      {hist.data && (
        <Card title="Change history">
          <Table rows={hist.data.history} empty="No changes saved; built-in defaults apply." cols={[
            { key: "updated_at", label: "When", render: (r) => new Date(r.updated_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) },
            { key: "changes", label: "Changes", render: (r) => r.changes.map((c: any) => `${c.path}: ${String(c.from)} → ${String(c.to)}`).join("; ") || "—" },
            { key: "note", label: "Note", render: (r) => r.note ?? "—" },
          ]} />
        </Card>
      )}
      <p className="muted small">Values shown use Indian formatting, e.g. {fmt.inr(100000)}.</p>
    </Page>
  );
}
