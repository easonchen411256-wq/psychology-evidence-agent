"""Compatibility wrapper for the legacy literature-search command."""

from psychology_evidence_agent.run_literature_search import main

if __name__ == "__main__":
    raise SystemExit(main())
