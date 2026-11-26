import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { fmt, useApi } from "../api";
import { LineChart } from "../components/charts";
import { Badge, Card, ErrorBox, Loading, Notice, Page, Stat, Table } from "../components/ui";

function useAccount() {
  const [params, setParams] = useSearchParams();
  const accts = useApi<any>("/api/v1/accounts?curves=true");
  const names: string[] = accts.data?.accounts?.map((a: any) => a.name) ?? [];
  // no request until the account list is known (there may be no "main" account)
  const account: string | null = params.get("account") || names.find((n) => n.startsWith("fwd-")) || names[0] || null;
  useEffect(() => { if (!params.get("account") && account) setParams({ account }, { replace: true }); }, [account]);  // eslint-disable-line
  const picker = (
    <label className="picker">Account{" "}
      <select value={account ?? ""} onChange={(e) => setParams({ account: e.target.value })}>{names.map((n) => <option key={n}>{n}</option>)}</select>
    </label>
  );
  return { account, accts, picker };
}

export function PaperPortfolio() {
  const { account, accts, picker } = useAccount();
  const pf = useApi<any>(account ? `/api/v1/portfolio?account=${account}` : null, [account]);
  const s = pf.data?.snapshot;
  return (
    <Page title="Paper Portfolio" sub="Simulated fills at the next open with slippage and Indian delivery charges. No broker connection exists." actions={picker}>
      <ErrorBox error={pf.error ?? accts.error} />
      {(pf.loading || accts.loading) && <Loading />}
      {accts.data && !accts.data.accounts.length && <Notice>No paper accounts yet. Create one: <code>python -m app.portfolio.cli init --name main</code></Notice>}
      {accts.data && (
        <Card title="Equity: forward-test accounts and the selected account">
          <LineChart series={accts.data.accounts.filter((a: any) => a.curve?.length && (a.forward_test || a.name === account)).map((a: any) => ({ name: a.name, data: a.curve }))} />
          <p className="muted small">Accounts named fwd-* are the forward test started after the backtests: D0 = the brief's rules, D2/D5 = redesigns.</p>
        </Card>
      )}
      {pf.data && (
        <>
          <div className="grid stats">
            <Stat label="Equity" value={fmt.inr(s?.equity ?? pf.data.initial_capital)} />
            <Stat label="Cash" value={fmt.inr(s?.cash ?? pf.data.initial_capital)} />
            <Stat label="Realised P&L" value={fmt.inr(s?.realised_pnl ?? 0)} tone={(s?.realised_pnl ?? 0) >= 0 ? "up" : "down"} />
            <Stat label="Charges paid" value={fmt.inr(s?.charges ?? 0)} />
            <Stat label="Drawdown" value={fmt.pct(-(s?.drawdown_pct ?? 0))} tone={(s?.drawdown_pct ?? 0) > 3 ? "down" : undefined} />
            <Stat label="Books reconciled" value={s ? (s.reconciliation?.ok ? "yes" : "NO") : "—"} />
          </div>
          <h2 className="section">Open positions</h2>
          <Table rows={pf.data.positions} empty="No open positions." cols={[
            { key: "symbol", label: "Symbol" }, { key: "strategy", label: "Strategy", render: (r) => fmt.label(r.strategy) },
            { key: "qty", label: "Qty", num: true }, { key: "avg_price", label: "Avg", num: true, render: (r) => fmt.n(r.avg_price) },
            { key: "mark", label: "Last", num: true, render: (r) => fmt.n(r.mark) }, { key: "stop", label: "Stop", num: true, render: (r) => fmt.n(r.stop) },
            { key: "unrealised_pnl", label: "Unrealised", num: true, render: (r) => <span className={r.unrealised_pnl >= 0 ? "up" : "down"}>{fmt.inr(r.unrealised_pnl)}</span> },
            { key: "entry_date", label: "Since" },
          ]} />
          <h2 className="section">Pending orders (next open)</h2>
          <Table rows={pf.data.pending_orders} empty="None." cols={[
            { key: "symbol", label: "Symbol" }, { key: "side", label: "Side" }, { key: "kind", label: "Kind", render: (r) => fmt.label(r.kind) },
            { key: "qty", label: "Qty", num: true }, { key: "reference_price", label: "Ref", num: true, render: (r) => fmt.n(r.reference_price) },
          ]} />
        </>
      )}
    </Page>
  );
}

export function RiskAnalytics() {
  const { account, picker } = useAccount();
  const cfg = useApi<any>("/api/v1/settings");
  const pf = useApi<any>(account ? `/api/v1/portfolio?account=${account}` : null, [account]);
  const hist = useApi<any>(account ? `/api/v1/portfolio/history?account=${account}` : null, [account]);
  const ev = useApi<any>(account ? `/api/v1/portfolio/risk-events?account=${account}&limit=2000` : null, [account]);
  const s = pf.data?.snapshot;
  const st = cfg.data?.strategy;
  const eq = s?.equity ?? pf.data?.initial_capital ?? 1;
  const counts: Record<string, Record<string, number>> = {};
  (ev.data?.events ?? []).forEach((e: any) => { counts[e.check] = counts[e.check] ?? {}; counts[e.check][e.outcome] = (counts[e.check][e.outcome] ?? 0) + 1; });
  const bar = (label: string, value: number, limit: number) => (
    <div className="bar-row" key={label}>
      <div className="bar-label">{label}</div>
      <div className="bar"><div className={`bar-fill ${value > limit ? "over" : ""}`} style={{ width: `${Math.min(100, (value / Math.max(limit, 1e-9)) * 100)}%` }} /></div>
      <div className="bar-val">{fmt.n(value, 1)}% / {fmt.n(limit, 1)}%</div>
    </div>
  );
  return (
    <Page title="Risk Analytics" sub="Exposure and risk versus the configured limits" actions={picker}>
      <ErrorBox error={pf.error ?? cfg.error} />
      {(pf.loading || cfg.loading) && <Loading />}
      {pf.data && st && (
        <div className="grid two">
          <Card title="Limits in use">
            {bar("Aggregate initial risk", ((s?.aggregate_initial_risk ?? 0) / eq) * 100, st.max_initial_aggregate_risk_pct)}
            {bar("Invested", ((s?.invested ?? 0) / eq) * 100, 100 - st.cash_reserve_pct)}
            {bar("Open positions", ((s?.open_positions ?? 0) / st.max_positions) * 100, 100)}
            {Object.entries(s?.exposure ?? {}).map(([sec, v]: any) => bar(sec, (v / eq) * 100, st.max_sector_exposure_pct))}
          </Card>
          <Card title="Drawdown from peak">
            {hist.data && <LineChart series={[{ name: "drawdown %", data: hist.data.equity_curve.map((x: any) => ({ time: x.date, value: -x.drawdown_pct })) }]} />}
            <p className="muted small">Alerts at {st.drawdown_alerts_pct.join("% and ")}%.</p>
          </Card>
          <Card title="Risk engine decisions (all proposals)" className="span2">
            <Table rows={Object.entries(counts).map(([check, o]) => ({ check, ...o }))} empty="No proposals evaluated yet." cols={[
              { key: "check", label: "Check", render: (r) => fmt.label(r.check) },
              { key: "pass", label: "Pass", num: true }, { key: "capped", label: "Capped", num: true },
              { key: "reject", label: "Rejected", num: true }, { key: "skipped", label: "Skipped", num: true },
              { key: "alert", label: "Alerts", num: true },
            ]} />
          </Card>
        </div>
      )}
    </Page>
  );
}

export function TradeJournal() {
  const { account, picker } = useAccount();
  const j = useApi<any>(account ? `/api/v1/portfolio/journal?account=${account}&limit=500` : null, [account]);
  return (
    <Page title="Trade Journal" sub="Every state change with its reason and the data session it used" actions={picker}>
      <ErrorBox error={j.error} />
      {j.loading && <Loading />}
      {j.data && <Table rows={j.data.journal} empty="No activity yet." cols={[
        { key: "date", label: "Date" }, { key: "action", label: "Action", render: (r) => <Badge>{fmt.label(r.action)}</Badge> },
        { key: "reason", label: "Reason" },
        { key: "details", label: "Detail", render: (r) => [r.details?.symbol, r.details?.qty && `${r.details.qty} sh`, r.details?.price && `@ ${fmt.n(r.details.price)}`, r.details?.pnl != null && `P&L ${fmt.inr(r.details.pnl)}`].filter(Boolean).join(" · ") || "—" },
      ]} />}
      <Notice>Journal entries are written by the paper engine; this page is read-only.</Notice>
    </Page>
  );
}

export function BacktestingLab() {
  const list = useApi<any>("/api/v1/backtests");
  const [id, setId] = useState<number | null>(null);
  const runs = list.data?.runs ?? [];
  const sel = id ?? runs.find((r: any) => r.kind === "holdout")?.id ?? runs[0]?.id ?? null;
  const run = useApi<any>(sel ? `/api/v1/backtests/${sel}` : null, [sel]);
  const r = run.data;
  const m = r?.metrics ?? {};
  return (
    <Page title="Backtesting Lab" sub="Historical simulations on point-in-time data. Not expected returns.">
      <ErrorBox error={list.error ?? run.error} />
      {list.loading && <Loading />}
      <Notice tone="warn">Result so far: no configuration tested shows an out-of-sample edge. The redesign chosen by a pre-registered rule made +18.5% in the design period and −10.6% on the locked holdout (Nifty 50 −0.8%).</Notice>
      <Table rows={runs} cols={[
        { key: "id", label: "#", num: true, render: (x) => <button className="link" onClick={() => setId(x.id)}>{x.id}</button> },
        { key: "kind", label: "Kind", render: (x) => <Badge tone={x.kind === "holdout" ? "warn" : ""}>{fmt.label(x.kind)}</Badge> },
        { key: "name", label: "Name" }, { key: "start", label: "From" }, { key: "end", label: "To" },
        { key: "ret", label: "Return", num: true, render: (x) => fmt.pct(x.metrics.total_return_pct) },
        { key: "sh", label: "Sharpe", num: true, render: (x) => fmt.n(x.metrics.sharpe_rf0) },
        { key: "nifty", label: "Nifty", num: true, render: (x) => fmt.pct(x.metrics.bench_total_return_pct) },
      ]} />
      {r && (
        <>
          <h2 className="section">#{r.id} {r.name} · {r.start} → {r.end}</h2>
          {r.equity_curve?.length > 0 && (
            <>
              <div className="grid stats">
                <Stat label="Return" value={fmt.pct(m.total_return_pct)} tone={m.total_return_pct >= 0 ? "up" : "down"} />
                <Stat label="CAGR" value={fmt.pct(m.cagr_pct)} /><Stat label="Sharpe (rf 0)" value={fmt.n(m.sharpe_rf0)} />
                <Stat label="Max drawdown" value={fmt.pct(-m.max_drawdown_pct)} /><Stat label="Trades" value={m.trades} />
                <Stat label="Expectancy" value={`${fmt.n(m.expectancy_r)} R`} /><Stat label="Win rate" value={`${fmt.n(m.win_rate_pct, 1)}%`} />
                <Stat label="Exposure" value={`${fmt.n(m.exposure_pct, 1)}%`} /><Stat label="Charges" value={fmt.inr(m.charges_total)} />
                <Stat label="Nifty 50" value={fmt.pct(m.bench_total_return_pct)} />
              </div>
              <Card title="Equity">
                <LineChart series={[{ name: "equity", data: r.equity_curve.map((x: any) => ({ time: x[0], value: x[1] })) }]} />
              </Card>
            </>
          )}
          {r.details?.variants && <Table rows={Object.entries(r.details.variants).map(([k, v]: any) => ({ k, ...v }))} cols={[
            { key: "k", label: "Variant", render: (x) => fmt.label(x.k) }, { key: "total_return_pct", label: "Return", num: true, render: (x) => fmt.pct(x.total_return_pct) },
            { key: "sharpe_rf0", label: "Sharpe", num: true, render: (x) => fmt.n(x.sharpe_rf0) }, { key: "max_drawdown_pct", label: "Max DD", num: true, render: (x) => fmt.pct(x.max_drawdown_pct) },
            { key: "trades", label: "Trades", num: true }, { key: "expectancy_r", label: "Exp. R", num: true, render: (x) => fmt.n(x.expectancy_r, 3) },
          ]} />}
          {r.details?.results && <Table rows={Object.entries(r.details.results).map(([k, v]: any) => ({ k, ...v }))} cols={[
            { key: "k", label: "Design", render: (x) => <>{fmt.label(x.k)}{r.details.chosen === x.k && <Badge tone="warn">chosen</Badge>}</> },
            { key: "rationale", label: "Why" },
            { key: "full", label: "Design period", num: true, render: (x) => fmt.pct(x.full.total_return_pct) },
            { key: "h1", label: "1st half", num: true, render: (x) => fmt.pct(x.h1.total_return_pct) },
            { key: "h2", label: "2nd half", num: true, render: (x) => fmt.pct(x.h2.total_return_pct) },
            { key: "sh", label: "Sharpe", num: true, render: (x) => fmt.n(x.full.sharpe_rf0) },
          ]} />}
          {r.details?.windows && <Table rows={r.details.windows} cols={[
            { key: "oos", label: "Out-of-sample window", render: (w) => `${w.oos[0]} → ${w.oos[1]}` }, { key: "chosen", label: "Chosen in-sample" },
          ]} />}
          <p className="muted small">Run new backtests from the server: <code>python -m app.backtesting.cli all</code>. The holdout is spent; further changes are judged by the forward paper accounts.</p>
        </>
      )}
    </Page>
  );
}
