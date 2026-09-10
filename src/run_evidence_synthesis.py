"""Compatibility wrapper for the legacy evidence-synthesis command."""

from psychology_evidence_agent.run_evidence_synthesis import main

if __name__ == "__main__":
    raise SystemExit(main())
