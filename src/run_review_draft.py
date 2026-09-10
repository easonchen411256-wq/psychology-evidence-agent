"""Compatibility wrapper for the legacy review-draft command."""

from psychology_evidence_agent.run_review_draft import main

if __name__ == "__main__":
    raise SystemExit(main())
