"""Run the deterministic evidence-card guardrails against a JSON file."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .evidence_card import validate_evidence_card


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python src/run_check.py <evidence-card.json>")
        return 2

    card_path = Path(sys.argv[1])
    try:
        card = json.loads(card_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"File not found: {card_path}")
        return 2
    except json.JSONDecodeError as error:
        print(f"Invalid JSON: {error}")
        return 2

    errors = validate_evidence_card(card)
    result = {"status": "pass" if not errors else "fail", "errors": errors}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
