import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fmt, useApi } from "../api";
import { CandleChart } from "../components/charts";
import { Badge, Card, ErrorBox, Loading, Notice, Page, StockCard, Table, bandTone } from "../components/ui";

export function Scanner() {
  const [sector, setSector] = useState("");
  const [minScore, setMinScore] = useState(0);
  const [all, setAll] = useState(false);
  const [sort, setSort] = useState("score");
  const q = `/api/v1/scanner?limit=3000&min_score=${minScore}&sort=${sort}${sector ? `&sector=${encodeURIComponent(sector)}` : ""}${all ? "&include_ineligible=true" : ""}`;
  const { data, error, loading } = useApi<any>(q);
  return (
    <Page title="All-Stock Scanner" sub={data ? `${data.count} stocks · ${data.data_as_of} · ${data.source}` : undefined}>
      <div className="filters">
        <label>Sector <select value={sector} onChange={(e) => setSector(e.target.value)}><option value="">All</option>{data?.sectors?.map((s: string) => <option key={s}>{s}</option>)}</select></label>
        <label>Min score <input type="number" min={0} max={100} value={minScore} onChange={(e) => setMinScore(Number(e.target.value) || 0)} /></label>
        <label>Sort <select value={sort} onChange={(e) => setSort(e.target.value)}><option value="score">Score</option><option value="rs">RS</option><option value="rvol">RVOL</option><option value="symbol">Symbol</option></select></label>
        <label className="check"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> include ineligible</label>
      </div>
      <ErrorBox error={error} />
      {loading && <Loading what="scanner" />}
      {data && <Table rows={data.stocks} cols={[
        { key: "symbol", label: "Symbol", render: (r) => <Link to={`/stock/${encodeURIComponent(r.symbol)}`}>{r.symbol}</Link> },
        { key: "price", label: "Price", num: true, render: (r) => fmt.n(r.price) },
        { key: "score", label: "Score", num: true, render: (r) => <Badge tone={bandTone(r.band)}>{fmt.n(r.score, 1)}</Badge> },
        { key: "rs_rank", label: "RS", num: true, render: (r) => fmt.n(r.rs_rank, 0) },
        { key: "rvol", label: "RVOL", num: true, render: (r) => fmt.n(r.rvol) },
        { key: "trend", label: "Trend" },
        { key: "setup", label: "Setup", render: (r) => r.setup ? `${fmt.label(r.setup.strategy)} (${fmt.label(r.setup.state)})` : "—" },
        { key: "sector", label: "Sector" },
        ...(all ? [{ key: "exclusions", label: "Excluded by", render: (r: any) => (r.exclusions ?? []).map(fmt.label).join(", ") || "—" }] : []),
      ]} />}
    </Page>
  );
}

export function TopOpportunities() {
  const { data, error, loading } = useApi<any>("/api/v1/scanner?limit=20");
  return (
    <Page title="Top 20 Opportunities" sub="Highest 100-point scores among eligible stocks. A score is research output, not a recommendation.">
      <ErrorBox error={error} />
      {loading && <Loading />}
      {data && <><Notice tone="warn">{data.note}</Notice><div className="cards">{data.stocks.map((s: any) => <StockCard key={s.symbol} s={s} />)}</div></>}
    </Page>
  );
}

export function Watchlist({ strategy, title }: { strategy: string; title: string }) {
  const { data, error, loading } = useApi<any>(`/api/v1/scanner?strategy=${strategy}&limit=500`);
  const groups = useMemo(() => {
    const g: Record<string, any[]> = { confirmed: [], potential_setup: [], early_warning: [] };
    (data?.stocks ?? []).forEach((s: any) => s.setup && g[s.setup.state]?.push(s));
    return g;
  }, [data]);
  return (
    <Page title={title} sub="Signals from completed daily bars. Confirmed = entry proposal for the next open, subject to the regime policy and risk engine.">
      <ErrorBox error={error} />
      {loading && <Loading />}
      {data && Object.entries(groups).map(([state, list]) => (
        <div key={state}>
          <h2 className="section">{fmt.label(state)} <span className="muted">({list.length})</span></h2>
          {list.length ? <div className="cards">{list.map((s) => <StockCard key={s.symbol} s={s} />)}</div> : <div className="state">None today.</div>}
        </div>
      ))}
    </Page>
  );
}

export function StockDetail() {
  const { symbol = "" } = useParams();
  const c = useApi<any>(`/api/v1/stocks/${encodeURIComponent(symbol)}/candles?days=250`);
  const sc = useApi<any>(`/api/v1/stocks/${encodeURIComponent(symbol)}/score`);
  const sg = useApi<any>(`/api/v1/stocks/${encodeURIComponent(symbol)}/signals?limit=10`);
  return (
    <Page title={symbol} sub={c.data ? `${c.data.sector ?? "Unclassified"} · ${c.data.prices} · ${c.data.source}` : undefined}>
      <ErrorBox error={c.error} />
      {c.loading && <Loading what="chart" />}
      {c.data && (
        <Card title="Daily candles · EMA 20 (blue) · EMA 50 (amber) · arrows = confirmed setups">
          <CandleChart candles={c.data.candles} markers={c.data.signals.map((s: any) => ({ time: s.time, text: s.strategy[0].toUpperCase() }))} />
        </Card>
      )}
      <div className="grid two">
        <Card title={sc.data ? `Score ${fmt.n(sc.data.total, 1)} (${sc.data.band}) · ${sc.data.session_date}` : "Score breakdown"}>
          {sc.error && <div className="state">No detailed score breakdown for this stock (only stored for recent sessions of eligible stocks).</div>}
          {sc.data && <Table rows={sc.data.breakdown} cols={[
            { key: "status", label: "", render: (r) => <Badge tone={r.status === "pass" ? "up" : r.status === "fail" ? "down" : ""}>{r.status}</Badge> },
            { key: "points", label: "Pts", num: true, render: (r) => `${fmt.n(r.points, 1)}/${fmt.n(r.max, 1)}` },
            { key: "rule", label: "Rule", wrap: true, render: (r) => <><span className="muted small">{fmt.label(r.group)} · </span>{r.rule}</> },
          ]} />}
        </Card>
        <Card title="Signal history">
          {sg.data && <Table rows={sg.data.signals} empty="No signals." cols={[
            { key: "opened", label: "Opened" }, { key: "strategy", label: "Strategy", render: (r) => fmt.label(r.strategy) },
            { key: "state", label: "State", render: (r) => fmt.label(r.state) },
            { key: "entry", label: "Entry", num: true, render: (r) => fmt.n(r.entry) }, { key: "stop", label: "Stop", num: true, render: (r) => fmt.n(r.stop) },
          ]} />}
        </Card>
      </div>
    </Page>
  );
}
