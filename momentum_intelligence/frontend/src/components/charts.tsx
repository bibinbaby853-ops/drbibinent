import { useEffect, useRef } from "react";
import { createChart, ColorType, IChartApi, LineStyle, Time } from "lightweight-charts";

function themeColors() {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string) => cs.getPropertyValue(n).trim() || undefined;
  return { text: v("--text") ?? "#111", grid: v("--grid") ?? "#e5e7eb", up: v("--up") ?? "#16a34a", down: v("--down") ?? "#dc2626",
           accent: v("--accent") ?? "#2563eb", accent2: v("--accent2") ?? "#d97706", muted: v("--muted") ?? "#6b7280" };
}

function useChart(height: number, build: (chart: IChartApi, c: ReturnType<typeof themeColors>) => void, deps: unknown[]) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const c = themeColors();
    const chart = createChart(ref.current, {
      height, autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: c.text, fontSize: 11 },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
      rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false },
      handleScroll: { vertTouchDrag: false },
    });
    build(chart, c);
    chart.timeScale().fitContent();
    return () => chart.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return ref;
}

export function CandleChart({ candles, markers = [], height = 360 }: { candles: any[]; markers?: any[]; height?: number }) {
  const ref = useChart(height, (chart, c) => {
    const s = chart.addCandlestickSeries({ upColor: c.up, downColor: c.down, wickUpColor: c.up, wickDownColor: c.down, borderVisible: false });
    s.setData(candles.map((k) => ({ time: k.time as Time, open: k.open, high: k.high, low: k.low, close: k.close })));
    const e20 = chart.addLineSeries({ color: c.accent, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    e20.setData(candles.filter((k) => k.ema20 != null).map((k) => ({ time: k.time as Time, value: k.ema20 })));
    const e50 = chart.addLineSeries({ color: c.accent2, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    e50.setData(candles.filter((k) => k.ema50 != null).map((k) => ({ time: k.time as Time, value: k.ema50 })));
    const v = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, color: c.muted });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    v.setData(candles.map((k) => ({ time: k.time as Time, value: k.volume, color: k.close >= k.open ? c.up + "66" : c.down + "66" })));
    if (markers.length) s.setMarkers(markers.map((m) => ({ time: m.time as Time, position: "belowBar", color: c.accent, shape: "arrowUp", text: m.text })));
  }, [candles, markers]);
  return <div ref={ref} className="chart" style={{ height }} aria-label="price chart" />;
}

export function LineChart({ series, height = 240, zeroLine }: { series: { name: string; data: { time: string; value: number }[]; color?: string }[]; height?: number; zeroLine?: boolean }) {
  const ref = useChart(height, (chart, c) => {
    const palette = [c.accent, c.accent2, c.up, c.down, c.muted];
    series.forEach((s, i) => {
      const l = chart.addLineSeries({ color: s.color ?? palette[i % palette.length], lineWidth: 2, priceLineVisible: false, title: s.name });
      l.setData(s.data.filter((d) => d.value != null).map((d) => ({ time: d.time as Time, value: d.value })));
      if (zeroLine && i === 0) l.createPriceLine({ price: 0, color: c.muted, lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: "" });
    });
  }, [series]);
  return <div ref={ref} className="chart" style={{ height }} />;
}
