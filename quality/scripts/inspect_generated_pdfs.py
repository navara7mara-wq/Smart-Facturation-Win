"""Read-only inspection and rendering of RC-1 QA-generated PDFs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "quality" / "evidence" / "generated"
RENDERED = ROOT / "quality" / "evidence" / "rendered"
OUTPUT = ROOT / "quality" / "evidence" / "pdf_inspection.json"


def main() -> None:
    RENDERED.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    expected_tokens = {
        "normal": ["QA_RC1_AUDIT_20260825_FAC_01", "95 625", "108 104"],
        "ndc": ["120/2026", "100 000", "113 050"],
    }
    for path in sorted(GENERATED.glob("*.pdf")):
        document = fitz.open(path)
        page_entries: list[dict[str, object]] = []
        all_text: list[str] = []
        for index, page in enumerate(document):
            text = page.get_text("text")
            all_text.append(text)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            rendered = RENDERED / f"{path.stem}_p{index + 1}.png"
            pixmap.save(rendered)
            page_entries.append(
                {
                    "page": index + 1,
                    "width": round(page.rect.width, 2),
                    "height": round(page.rect.height, 2),
                    "text_chars": len(text),
                    "rendered": str(rendered.relative_to(ROOT)),
                }
            )
        document.close()
        joined = "\n".join(all_text)
        scenario = "normal" if path.name.startswith("normal_") else "ndc"
        tokens = expected_tokens[scenario]
        results.append(
            {
                "file": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "pages": len(page_entries),
                "page_details": page_entries,
                "expected_token_checks": {token: token in joined for token in tokens},
                "text_preview": joined[:800],
            }
        )
    OUTPUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"files": len(results), "pages": sum(int(r["pages"]) for r in results)}, indent=2))


if __name__ == "__main__":
    main()
