import { Link } from "react-router-dom";
import { fmt, useApi } from "../api";
import { LineChart } from "../components/charts";
import { Badge, Card, ErrorBox, Loading, Notice, Page, Stat, StockCard, Table, regimeTone } from "../components/ui";

export function CommandCentre() {
  const { data, error, loading } = useApi<any>("/api/v1/dashboard/summary");
  return (
    <Page title="Command Centre" sub={data ? `Session ${data.session_date} · data ${data.data_age_days ?? "?"} day(s) old` : undefined}>
      <ErrorBox error={error} />
      {loading && <Loading />}
      {data && (
        <>
          <Notice tone="warn">{data.note}</Notice>
          <div className="grid stats">
            <Stat label="Market regime" value={<span className={regimeTone(data.regime?.name)}>{fmt.label(data.regime?.name)}</span>} />
            <Stat label="Regime score" value={fmt.n(data.regime?.score, 0)} />
            <Stat label="New entries allowed" value={data.regime?.policy?.allow_new_entries ? `yes, up to ${data.regime.policy.max_new_entries_per_day}/day` : "no"} />
            <Stat label="Confirmed setups today" value={Object.values(data.confirmed_signals ?? {}).reduce((a: number, b: any) => a + b, 0)} />
            <Stat label="Active alerts" value={<Link to="/intraday">{data.active_alerts ?? 0}</Link>} tone={(data.active_alerts ?? 0) > 0 ? "warn" : undefined} />
          </div>
          <div className="grid two">
            <Card title="Forward test (paper accounts)">
              <div className="acct-list">
                {data.accounts.filter((a: any) => a.forward_test).map((a: any) => (
                  <div className="acct" key={a.name}>
                    <div><Link className="acct-name" to={`/portfolio?account=${a.name}`}>{a.name}</Link>
                      <div className="acct-sub muted">{fmt.label(a.profile)} · since {a.first_session ?? "next session"} · {a.open_positions} open</div></div>
                    <div className="num"><div>{fmt.inr(a.equity)}</div><div className={a.return_pct >= 0 ? "up" : "down"}>{fmt.pct(a.return_pct)}</div></div>
                  </div>
                ))}
                {!data.accounts.some((a: any) => a.forward_test) && <div className="muted">No forward-test accounts.</div>}
              </div>
            </Card>
            <Card title="Evidence so far">
              {data.evidence ? (
                <p>Locked holdout ({data.evidence.period[0]} → {data.evidence.period[1]}), {data.evidence.run}: <b className="down">{fmt.pct(data.evidence.return_pct)}</b> vs Nifty 50 {fmt.pct(data.evidence.bench_pct)}. <Link to="/backtests">Backtesting Lab</Link></p>
              ) : <p className="muted">No holdout evaluation recorded.</p>}
              {data.drawdown_alerts?.length > 0 && <p className="warn">Drawdown alerts: {data.drawdown_alerts.map((a: any) => `${a.account} ${a.date} ${a.level_pct}%`).join(", ")}</p>}
            </Card>
          </div>
          <h2 className="section">Highest scores <Link to="/top" className="small">all top 20 →</Link></h2>
          <div className="cards">{data.top.map((s: any) => <StockCard key={s.symbol} s={s} />)}</div>
        </>
      )}
    </Page>
  );
}

export function MarketOverview() {
  const { data, error, loading } = useApi<any>("/api/v1/market/overview?days=250");
  const h = data?.history ?? [];
  return (
    <Page title="Market Overview" sub="Regime classifier, breadth and the Nifty 50">
      <ErrorBox error={error} />
      {loading && <Loading />}
      {data && (
        <>
          <div className="grid stats">
            <Stat label="Regime (confirmed)" value={<span className={regimeTone(data.latest.regime)}>{fmt.label(data.latest.regime)}</span>} />
            <Stat label="Raw today" value={fmt.label(data.latest.raw_regime)} />
            <Stat label="Score (−100…100)" value={fmt.n(data.latest.score, 0)} />
            <Stat label="Risk multiplier" value={fmt.n(data.latest.policy?.risk_multiplier, 2)} />
          </div>
          <div className="grid two">
            <Card title="Regime score">
              <LineChart zeroLine series={[{ name: "score", data: h.map((x: any) => ({ time: x.time, value: x.score })) }]} />
            </Card>
            <Card title="Breadth: % of eligible stocks above their averages">
              <LineChart series={[{ name: "> 50-day", data: h.map((x: any) => ({ time: x.time, value: x.breadth50 })) },
                                  { name: "> 200-day", data: h.map((x: any) => ({ time: x.time, value: x.breadth200 })) }]} />
            </Card>
            <Card title="Nifty 50 (NSE official close)">
              <LineChart series={[{ name: "Nifty 50", data: data.nifty }]} />
            </Card>
            <Card title="Components today">
              <Table rows={Object.entries(data.latest.components ?? {}).map(([k, v]: any) => ({ k, ...v }))} cols={[
                { key: "k", label: "Component", render: (r) => fmt.label(r.k) },
                { key: "points", label: "Points (−2…2)", num: true, render: (r) => fmt.n(r.points, 1) },
                { key: "weight", label: "Weight", num: true },
              ]} />
              <p className="muted small">VIX {fmt.n(data.latest.inputs?.vix)} · median 63-session return {fmt.pct(data.latest.inputs?.median_ret_63_pct)}</p>
            </Card>
          </div>
        </>
      )}
    </Page>
  );
}

export function SectorRotation() {
  const { data, error, loading } = useApi<any>("/api/v1/sectors?history=60");
  const top = (data?.sectors ?? []).filter((s: any) => s.rank).slice(0, 5);
  return (
    <Page title="Sector Rotation" sub="Equal-weighted composites of each sector's eligible stocks (1 = strongest)">
      <ErrorBox error={error} />
      {loading && <Loading />}
      {data && (
        <>
          <Card title="Rank history, current top five (lower = stronger)">
            <LineChart series={top.map((s: any) => ({ name: s.sector.slice(0, 18), data: (data.rank_history[s.sector] ?? []).map((x: any) => ({ time: x.time, value: x.rank })) }))} />
          </Card>
          <Table rows={data.sectors} cols={[
            { key: "rank", label: "#", num: true },
            { key: "sector", label: "Sector" },
            { key: "rank_change", label: "Δ20", num: true, render: (r) => r.rank_change == null ? "—" : <span className={r.rank_change > 0 ? "up" : r.rank_change < 0 ? "down" : ""}>{r.rank_change > 0 ? "+" : ""}{r.rank_change}</span> },
            { key: "constituents", label: "Stocks", num: true },
            { key: "excess_63", label: "vs Nifty 63d", num: true, render: (r) => fmt.pct(r.excess_63 == null ? null : r.excess_63 * 100, 1) },
            { key: "breadth50_pct", label: ">50d %", num: true, render: (r) => fmt.n(r.breadth50_pct, 0) },
            { key: "new_52w_highs", label: "New highs", num: true },
            { key: "flags", label: "Flags", render: (r) => r.flags.map((f: string) => <Badge key={f} tone={f === "thin" ? "" : "good"}>{fmt.label(f)}</Badge>) },
          ]} />
        </>
      )}
    </Page>
  );
}

export function Phase({ title, phase, what }: { title: string; phase: number; what: string }) {
  return (
    <Page title={title}>
      <Notice>Not built yet: this page arrives in Phase {phase}. {what} Nothing on this page is simulated or placeholder data.</Notice>
    </Page>
  );
}
