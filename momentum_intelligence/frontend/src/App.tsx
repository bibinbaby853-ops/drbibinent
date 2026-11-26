import { useEffect, useState } from "react";
import { BrowserRouter, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useApi } from "./api";
import { CommandCentre, MarketOverview, Phase, SectorRotation } from "./pages/core";
import { DailyReport, IntradayMonitoring } from "./pages/monitor";
import { BacktestingLab, PaperPortfolio, RiskAnalytics, TradeJournal } from "./pages/portfolio";
import { Scanner, StockDetail, TopOpportunities, Watchlist } from "./pages/scan";
import { Settings } from "./pages/settings";

export const NAV: { to: string; label: string }[] = [
  { to: "/", label: "Command Centre" },
  { to: "/market", label: "Market Overview" },
  { to: "/scanner", label: "All-Stock Scanner" },
  { to: "/top", label: "Top 20 Opportunities" },
  { to: "/breakouts", label: "Breakout Watchlist" },
  { to: "/pullbacks", label: "Pullback Watchlist" },
  { to: "/sectors", label: "Sector Rotation" },
  { to: "/portfolio", label: "Paper Portfolio" },
  { to: "/intraday", label: "Intraday Monitoring" },
  { to: "/risk", label: "Risk Analytics" },
  { to: "/backtests", label: "Backtesting Lab" },
  { to: "/journal", label: "Trade Journal" },
  { to: "/report", label: "AI Daily Report" },
  { to: "/settings", label: "Settings" },
];

function Shell() {
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname, loc.search]);
  const health = useApi<any>("/api/health");
  const h = health.data;
  return (
    <div className="app">
      <header className="topbar">
        <button className="menu-btn" aria-label="menu" aria-expanded={open} onClick={() => setOpen(!open)}>☰</button>
        <div className="brand">Momentum Intelligence <span className="paper-tag">PAPER</span></div>
        <div className="status muted small">{h ? `${h.status === "ok" ? "●" : "○"} data to ${h.latest_session ?? "—"}` : ""}</div>
      </header>
      <nav className={`sidebar ${open ? "open" : ""}`} aria-label="pages">
        {NAV.map((n) => <NavLink key={n.to} to={n.to} end={n.to === "/"}>{n.label}</NavLink>)}
        <div className="sidebar-foot muted small">Research and paper trading only. Not investment advice.</div>
      </nav>
      {open && <div className="scrim" onClick={() => setOpen(false)} />}
      <main className="content">
        {h?.status === "degraded" && (
          <div className="degraded" role="status">
            System degraded: {(h.problems ?? []).join("; ") || "see /api/health"}. Figures may be out of date.
          </div>
        )}
        <Routes>
          <Route path="/" element={<CommandCentre />} />
          <Route path="/market" element={<MarketOverview />} />
          <Route path="/scanner" element={<Scanner />} />
          <Route path="/top" element={<TopOpportunities />} />
          <Route path="/breakouts" element={<Watchlist strategy="breakout" title="Breakout Watchlist" />} />
          <Route path="/pullbacks" element={<Watchlist strategy="pullback" title="Pullback Watchlist" />} />
          <Route path="/sectors" element={<SectorRotation />} />
          <Route path="/portfolio" element={<PaperPortfolio />} />
          <Route path="/intraday" element={<IntradayMonitoring />} />
          <Route path="/risk" element={<RiskAnalytics />} />
          <Route path="/backtests" element={<BacktestingLab />} />
          <Route path="/journal" element={<TradeJournal />} />
          <Route path="/report" element={<DailyReport />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/stock/:symbol" element={<StockDetail />} />
          <Route path="*" element={<Phase title="Not found" phase={8} what="This address does not match a page." />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return <BrowserRouter><Shell /></BrowserRouter>;
}
