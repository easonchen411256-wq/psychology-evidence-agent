"""Compatibility wrapper for the legacy manual-review report command."""

from psychology_evidence_agent.run_literature_review_report import main

if __name__ == "__main__":
    raise SystemExit(main())
