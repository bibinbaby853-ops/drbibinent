import { ReactNode, useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { Badge, Card, ErrorBox, Loading, Notice, Page, Table } from "../components/ui";

const sevTone = (s: string) => (s === "critical" ? "down" : s === "warning" ? "warn" : "");

export function IntradayMonitoring() {
  const [state, setState] = useState("active");
  const [ver, setVer] = useState(0);
  const al = useApi<any>(`/api/v1/alerts${state ? `?state=${state}` : ""}`, [ver]);
  const jobs = useApi<any>("/api/v1/jobs?limit=20", [ver]);
  const ack = async (id: number) => { await api(`/api/v1/alerts/${id}/ack`, { method: "POST" }); setVer((v) => v + 1); };
  const lr = al.data?.last_run;
  return (
    <Page title="Intraday Monitoring" sub={al.data?.session_date ? `Session ${al.data.session_date}` : "Every 15 minutes, 09:30 to 15:45 IST, on completed candles"}
      actions={<button className="secondary" onClick={() => setVer((v) => v + 1)}>Refresh</button>}>
      <Notice>{al.data?.note ?? "Alerts use completed candles only."} Alerts never place or change orders; paper exits are booked by the end-of-day run.</Notice>
      <div className="filters">
        {lr && <span className="muted small">Last pass {lr.key} · {lr.status}{lr.details?.symbols_watched != null ? ` · ${lr.details.symbols_watched} symbols watched, last completed bar ${String(lr.details.last_completed_bar_start ?? "—").slice(11, 16)}` : ""}</span>}
      </div>
      <ErrorBox error={al.error} />
      {al.loading && <Loading what="alerts" />}
      {al.data?.counts && (
        <div className="sc-badges" style={{ marginBottom: ".75rem" }}>
          {["active", "resolved", "acknowledged", "expired"].map((k) => (
            <button key={k} className={state === k ? "" : "secondary"} onClick={() => setState(k)}>{k} {al.data.counts[k] ?? 0}</button>
          ))}
          <button className={state === "" ? "" : "secondary"} onClick={() => setState("")}>all</button>
        </div>
      )}
      {al.data && (al.data.alerts.length ? (
        <div className="alert-list">
          {al.data.alerts.map((a: any) => (
            <div className="alert-row" key={a.id}>
              <Badge tone={sevTone(a.severity)}>{a.severity}</Badge>
              <div className="alert-body">
                <div><Link to={a.symbol.includes(" ") ? "/market" : `/stock/${encodeURIComponent(a.symbol)}`}><b>{a.symbol}</b></Link>
                  <span className="muted small"> · {a.rule.replace(/_/g, " ")}{a.account ? ` · ${a.account}` : ""} · {a.state}
                  · {a.bar_end ? new Date(a.bar_end).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" }) : "—"} IST</span></div>
                <div className="small">{a.message}</div>
              </div>
              {(a.state === "active" || a.state === "resolved") && <button className="secondary" onClick={() => ack(a.id)} aria-label={`acknowledge ${a.symbol}`}>Ack</button>}
            </div>
          ))}
        </div>
      ) : <div className="state">No {state || ""} alerts for this session.</div>)}
      <h2 className="section">Scheduler runs</h2>
      <Table rows={jobs.data?.runs ?? []} empty="No scheduled jobs have run yet. Start the scheduler: python -m app.scheduler.service" cols={[
        { key: "job", label: "Job" }, { key: "key", label: "Key" },
        { key: "status", label: "Status", render: (r) => <Badge tone={r.status === "success" ? "up" : r.status === "failed" ? "down" : ""}>{r.status}</Badge> },
        { key: "finished_at", label: "Finished (IST)", render: (r) => (r.finished_at ? new Date(r.finished_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : "—") },
        { key: "error", label: "Error", wrap: true, render: (r) => r.error ?? "" },
      ]} />
    </Page>
  );
}

// Minimal, safe Markdown rendering for the generated reports (headings, bullets, bold, quotes); no HTML injection.
function md(text: string): ReactNode[] {
  const inline = (s: string) => s.split(/(\*\*[^*]+\*\*)/g).map((p, i) => (p.startsWith("**") ? <b key={i}>{p.slice(2, -2)}</b> : p));
  const out: ReactNode[] = [];
  let list: ReactNode[] = [];
  const flush = () => { if (list.length) { out.push(<ul key={out.length}>{list}</ul>); list = []; } };
  text.split("\n").forEach((line, i) => {
    if (/^\s*- /.test(line)) { list.push(<li key={i} className={line.startsWith("  ") ? "sub" : ""}>{inline(line.replace(/^\s*- /, ""))}</li>); return; }
    flush();
    if (line.startsWith("# ")) out.push(<h2 key={i}>{line.slice(2)}</h2>);
    else if (line.startsWith("## ")) out.push(<h3 key={i}>{line.slice(3)}</h3>);
    else if (line.startsWith("> ")) out.push(<div key={i} className="notice warn">{inline(line.slice(2))}</div>);
    else if (line.trim()) out.push(<p key={i}>{inline(line)}</p>);
  });
  flush();
  return out;
}

export function DailyReport() {
  const [kind, setKind] = useState("");
  const r = useApi<any>(`/api/v1/reports/latest${kind ? `?kind=${kind}` : ""}`, [kind]);
  const list = useApi<any>("/api/v1/reports?limit=10");
  return (
    <Page title="AI Daily Report" sub="The report is built from stored data; an optional Claude commentary appears above it only when every number in it checks out."
      actions={<label className="picker">Report <select value={kind} onChange={(e) => setKind(e.target.value)}>
        <option value="">Latest</option><option value="post_market">Post-market</option><option value="pre_market">Pre-market</option></select></label>}>
      <ErrorBox error={r.error} />
      {r.loading && <Loading what="report" />}
      {r.data?.ai && (
        r.data.ai.text ? (
          <Card title={`AI commentary · ${r.data.ai.model ?? "Claude"}`}>
            <Notice>Written by Claude from this report's stored data only. All {r.data.ai.grounding?.numbers_checked ?? 0} numbers in it were checked against that data; a commentary with any unsupported number is never shown.</Notice>
            <div className="report">{md(r.data.ai.text)}</div>
          </Card>
        ) : (
          <Notice>{!r.data.ai.enabled ? "AI commentary is off (optional; set MI_AI_ENABLED and MI_ANTHROPIC_API_KEY on the server). The report below is complete without it."
            : r.data.ai.status ? `AI commentary not shown: ${r.data.ai.status}${r.data.ai.detail ? ` (${r.data.ai.detail})` : ""}. The report below is unaffected.`
            : "No AI commentary for this report yet."}</Notice>
        )
      )}
      {r.data && (
        <Card>
          <div className="muted small">Generated {new Date(r.data.generated_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST · data as of {r.data.data_as_of}</div>
          <div className="report">{md(r.data.markdown)}</div>
        </Card>
      )}
      {list.data?.reports?.length > 0 && <p className="muted small">Recent: {list.data.reports.map((x: any) => `${x.kind.replace("_", "-")} ${x.session_date}`).join(" · ")}</p>}
    </Page>
  );
}
