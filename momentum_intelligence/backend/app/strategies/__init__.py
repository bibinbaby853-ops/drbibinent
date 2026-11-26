"""Strategy engine (Phase 5): Breakout, Pullback, Continuation; signal state machine; de-duplication.

* ``rules``    vectorised, causal conditions per strategy -> per-session state candidates and levels
* ``machine``  one active signal per stock, state transitions, expiry, cooldown
* ``pipeline`` runs a date range, stores signals and events, and hands confirmed signals to the paper portfolio
"""
