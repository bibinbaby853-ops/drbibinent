import { ReactNode } from "react";
import { Link } from "react-router-dom";
import { fmt } from "../api";

export function Page({ title, sub, children, actions }: { title: string; sub?: ReactNode; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>{title}</h1>
          {sub && <p className="muted">{sub}</p>}
        </div>
        {actions}
      </header>
      {children}
    </section>
  );
}

export function Card({ title, children, className = "" }: { title?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={`card ${className}`}>
      {title && <h2 className="card-title">{title}</h2>}
      {children}
    </div>
  );
}

export function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: "up" | "down" | "warn" }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${tone ?? ""}`}>{value}</div>
    </div>
  );
}

export function Loading({ what = "data" }: { what?: string }) {
  return <div className="state" role="status">Loading {what}…</div>;
}

export function ErrorBox({ error }: { error: Error | null }) {
  if (!error) return null;
  const status = (error as any).status;
  return (
    <div className="state error" role="alert">
      {status === 401 ? "This API needs a token: enter it on the Settings page." : status === 404 ? `Not available yet: ${error.message}` : error.message}
    </div>
  );
}

export function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" }) {
  return <div className={`notice ${tone}`}>{children}</div>;
}

export function Badge({ children, tone }: { children: ReactNode; tone?: string }) {
  return <span className={`badge ${tone ?? ""}`}>{children}</span>;
}

export function regimeTone(r?: string | null) {
  if (!r) return "";
  return r.includes("bull") ? "up" : r.includes("bear") ? "down" : "warn";
}

export function bandTone(b?: string | null) {
  return b === "priority" ? "up" : b === "strong" ? "good" : b === "developing" ? "warn" : "";
}

export function StockCard({ s }: { s: any }) {
  const st = s.setup;
  return (
    <article className="stock-card">
      <div className="sc-head">
        <Link to={`/stock/${encodeURIComponent(s.symbol)}`} className="sc-symbol">{s.symbol}</Link>
        <span className="sc-price">{fmt.inr(s.price, 2)}</span>
      </div>
      <div className="sc-meta muted">{s.sector} · {s.data?.source ?? "NSE"}</div>
      <div className="sc-badges">
        <Badge tone={bandTone(s.band)}>Score {fmt.n(s.score, 1)}</Badge>
        <Badge>RS {fmt.n(s.rs_rank, 0)}</Badge>
        <Badge>RVOL {fmt.n(s.rvol, 2)}</Badge>
        <Badge tone={s.trend?.includes("up") ? "up" : s.trend === "downtrend" ? "down" : ""}>{s.trend ?? "trend —"}</Badge>
        {s.rs_classes?.map((c: string) => <Badge key={c} tone="good">{fmt.label(c)}</Badge>)}
      </div>
      {st ? (
        <div className="sc-setup">
          <div><b>{fmt.label(st.strategy)}</b> · {fmt.label(st.state)}</div>
          <div className="sc-levels">
            <span>Entry {fmt.n(st.entry)}</span><span>Stop {fmt.n(st.stop)}</span>
            <span>Invalid below {fmt.n(st.invalidation)}</span><span>Risk {fmt.pct(st.risk_pct)}</span>
            <span>2R target {fmt.n(st.target_2r)}</span>
          </div>
        </div>
      ) : <div className="sc-setup muted">No active setup</div>}
      {s.reasons?.length > 0 && <ul className="sc-reasons">{s.reasons.slice(0, 4).map((r: string) => <li key={r}>{r}</li>)}</ul>}
    </article>
  );
}

export function Table({ cols, rows, empty = "Nothing to show." }: { cols: { key: string; label: string; render?: (r: any) => ReactNode; num?: boolean; wrap?: boolean }[]; rows: any[]; empty?: string }) {
  if (!rows?.length) return <div className="state">{empty}</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{cols.map((c) => <th key={c.key} className={c.num ? "num" : ""}>{c.label}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{cols.map((c) => <td key={c.key} className={`${c.num ? "num" : ""} ${c.wrap ? "wrap" : ""}`}>{c.render ? c.render(r) : r[c.key] ?? "—"}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
