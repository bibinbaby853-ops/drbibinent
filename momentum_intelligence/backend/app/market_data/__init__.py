"""Market data engine (Phase 2).

* ``base``        provider protocol, frame schemas, lineage and errors
* ``http``        polite HTTP client: rate limit, retries with backoff, 429/5xx handling
* ``nse_archive`` NSE official end-of-day archives (primary daily source)
* ``yahoo``       Yahoo Finance via yfinance (unofficial, delayed; intraday and cross-checks)
* ``demo``        deterministic synthetic data, always labelled demo
* ``kite``        Zerodha Kite adapter: not available in this environment, read-only by design
* ``validation``  data-quality checks
* ``adjustments`` corporate-action factors and adjusted series
* ``calendar``    trading sessions and market hours (IST)
* ``ingest``      write validated data to PostgreSQL with lineage
"""
