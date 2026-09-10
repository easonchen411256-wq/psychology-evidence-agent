"""Create a conservative, local comparison matrix from evidence-card JSON files."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .domain.synthesis import EvidenceSynthesis
from .ports.persistence import RunArtifactStore

REQUIRED_CARD_FIELDS = {
    "source",
    "material_completeness",
    "study",
    "findings",
    "limitations",
    "claim_boundaries",
    "human_review_items",
}

SIGNAL_TERMS = {
    "older_adults": (
        "older adult",
        "older adults",
        "elderly",
        "older person",
        "older persons",
        "older people",
        "老年",
    ),
    "fear_of_falling": ("fear of falling", "跌倒恐惧", "害怕跌倒"),
    "psychological_intervention": (
        "psychological intervention",
        "cognitive behavioral",
        "cognitive-behavioral",
        "cbt",
        "mindfulness",
        "psychotherapy",
        "心理干预",
        "认知行为",
        "正念",
        "心理治疗",
    ),
    "heart_rate_or_hrv": ("heart rate variability", "hrv", "heart rate", "心率变异", "心率"),
    "gait": (
        "gait",
        "walking speed",
        "dual-task",
        "dual task",
        "tug",
        "gaitrite",
        "步态",
        "步行速度",
        "双任务",
        "起立行走",
    ),
}


def _as_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _join_items(value: Any, separator: str = " | ") -> str:
    if isinstance(value, list):
        return separator.join(
            str(item).replace("\n", " ").strip() for item in value if str(item).strip()
        )
    return str(value or "").replace("\n", " ").strip()


def detect_signals(card: dict[str, Any]) -> dict[str, str]:
    """Detect topical signals only; a match is not evidence of an intervention effect."""
    text = json.dumps(card, ensure_ascii=False).lower()
    return {
        name: "yes" if any(term in text for term in terms) else "no"
        for name, terms in SIGNAL_TERMS.items()
    }


def candidate_role(card: dict[str, Any], signals: dict[str, str]) -> str:
    """Never infer evidence role from terms that may occur in negative statements."""
    return "review_required"


def _normalise_card(path: Path, card: dict[str, Any]) -> dict[str, Any]:
    signals = detect_signals(card)
    return {
        "file": str(path),
        "source": _as_object(card["source"]),
        "material_completeness": str(card["material_completeness"]),
        "study": _as_object(card["study"]),
        "signals": signals,
        "evidence_role_candidate": candidate_role(card, signals),
        "findings": _as_list(card["findings"]),
        "limitations": [str(item) for item in _as_list(card["limitations"])],
        "claim_boundaries": _as_object(card["claim_boundaries"]),
        "human_review_items": [str(item) for item in _as_list(card["human_review_items"])],
    }


def build_synthesis(paths: Iterable[Path]) -> EvidenceSynthesis:
    """Load cards, retaining invalid input paths as explicit skipped-file records."""
    cards: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    path_list = list(paths)
    for path in path_list:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            skipped.append({"file": str(path), "reason": "File not found."})
            continue
        except UnicodeDecodeError:
            skipped.append({"file": str(path), "reason": "File is not UTF-8 text."})
            continue
        except json.JSONDecodeError:
            skipped.append({"file": str(path), "reason": "Invalid JSON."})
            continue
        if not isinstance(loaded, dict):
            skipped.append({"file": str(path), "reason": "Evidence card must be a JSON object."})
            continue
        missing = sorted(REQUIRED_CARD_FIELDS - loaded.keys())
        if missing:
            skipped.append({"file": str(path), "reason": f"Missing fields: {', '.join(missing)}."})
            continue
        cards.append(_normalise_card(path, loaded))

    cards.sort(
        key=lambda item: (
            item["evidence_role_candidate"],
            str(item["source"].get("title", "")).lower(),
        )
    )
    role_counts = Counter(card["evidence_role_candidate"] for card in cards)
    return EvidenceSynthesis.model_validate(
        {
            "summary": {
                "input_count": len(path_list),
                "included_count": len(cards),
                "skipped_count": len(skipped),
                "role_counts": dict(sorted(role_counts.items())),
            },
            "cards": cards,
            "skipped_files": skipped,
        }
    )


def _finding_text(findings: list[Any]) -> str:
    result: list[str] = []
    for finding in findings:
        if isinstance(finding, dict):
            text = str(finding.get("finding", "")).strip()
            strength = str(finding.get("inference_strength", "")).strip()
            if text:
                result.append(f"{text} ({strength})" if strength else text)
        elif str(finding).strip():
            result.append(str(finding).strip())
    return _join_items(result)


def _matrix_row(card: dict[str, Any]) -> dict[str, str]:
    source = _as_object(card["source"])
    study = _as_object(card["study"])
    boundaries = _as_object(card["claim_boundaries"])
    return {
        "title": str(source.get("title", "未报告")),
        "year": str(source.get("year", "未报告")),
        "evidence_role_candidate": str(card["evidence_role_candidate"]),
        "material_completeness": str(card["material_completeness"]),
        "design": str(study.get("design", "未报告")),
        "sample": str(study.get("sample", "未报告")),
        "measures": _join_items(study.get("measures", [])),
        "heart_rate_or_hrv_signal": str(card["signals"]["heart_rate_or_hrv"]),
        "gait_signal": str(card["signals"]["gait"]),
        "findings": _finding_text(_as_list(card["findings"])),
        "supported_claims": _join_items(boundaries.get("supported_claims", [])),
        "limitations": _join_items(card["limitations"]),
        "human_review_items": _join_items(card["human_review_items"]),
        "file": str(card["file"]),
    }


MATRIX_COLUMNS = [
    "title",
    "year",
    "evidence_role_candidate",
    "material_completeness",
    "design",
    "sample",
    "measures",
    "heart_rate_or_hrv_signal",
    "gait_signal",
    "findings",
    "supported_claims",
    "limitations",
    "human_review_items",
    "file",
]


def write_synthesis_outputs(
    synthesis: EvidenceSynthesis,
    output_dir: Path,
    store: RunArtifactStore | None = None,
) -> dict[str, Path]:
    """Write JSON, CSV, Markdown matrix, and a focused human review queue."""
    if store is None:
        output_dir.mkdir(parents=True, exist_ok=True)
        from .adapters.persistence.artifact_store import FileSystemArtifactStore

        store = FileSystemArtifactStore(output_dir)
    payload = synthesis.model_dump(mode="json")
    rows = [_matrix_row(card) for card in payload["cards"]]
    json_path = output_dir / "evidence_synthesis.json"
    csv_path = output_dir / "evidence_matrix.csv"
    markdown_path = output_dir / "evidence_matrix.md"
    queue_path = output_dir / "review_queue.md"
    store.save_json(json_path.name, payload)
    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=MATRIX_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    store.save_text(csv_path.name, "\ufeff" + csv_buffer.getvalue())

    visible = [
        "title",
        "year",
        "evidence_role_candidate",
        "material_completeness",
        "design",
        "sample",
        "measures",
        "findings",
        "human_review_items",
    ]
    lines = [
        "# Evidence matrix",
        "",
        "| " + " | ".join(visible) + " |",
        "| " + " | ".join("---" for _ in visible) + " |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(row[column].replace("|", "\\|").replace("\n", " ") for column in visible)
            + " |"
        )
    store.save_text(markdown_path.name, "\n".join(lines) + "\n")

    queue_lines = ["# Human review queue", ""]
    queued = [
        card
        for card in payload["cards"]
        if card["evidence_role_candidate"] == "review_required" or card["human_review_items"]
    ]
    if not queued and not payload["skipped_files"]:
        queue_lines.append(
            "No review items were generated. Candidate roles still require full-text confirmation."
        )
    for card in queued:
        title = str(_as_object(card["source"]).get("title", "未报告"))
        queue_lines.extend(
            [f"## {title}", f"- Candidate role: `{card['evidence_role_candidate']}`"]
        )
        if card["material_completeness"] != "complete":
            queue_lines.append(f"- Material completeness: `{card['material_completeness']}`")
        queue_lines.extend(f"- {item}" for item in card["human_review_items"])
        queue_lines.append("")
    if payload["skipped_files"]:
        queue_lines.append("## Skipped files")
        queue_lines.extend(
            f"- `{item['file']}`: {item['reason']}" for item in payload["skipped_files"]
        )
    store.save_text(queue_path.name, "\n".join(queue_lines) + "\n")
    return {
        "json": json_path,
        "csv": csv_path,
        "markdown": markdown_path,
        "review_queue": queue_path,
    }
