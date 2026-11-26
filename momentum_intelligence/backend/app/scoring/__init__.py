"""Phase 4: relative strength, sector rotation, market regime and the 100-point momentum score.

* ``panel``             wide price/eligibility panels loaded once for a date range
* ``relative_strength`` excess returns vs Nifty 50 / Nifty 500 / sector, combined rank, classes
* ``sectors``           equal-weighted sector composites and rotation metrics
* ``regime``            five-state market regime from explicit component scores
* ``score``             100-point score with every sub-check recorded
* ``pipeline``          runs the above for a range of sessions and stores the results
"""
