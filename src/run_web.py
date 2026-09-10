"""Compatibility wrapper for the legacy web command."""

from psychology_evidence_agent.run_web import main

if __name__ == "__main__":
    raise SystemExit(main())
