from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf  # type: ignore[import-not-found]

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "packages/docx-engine/tests/fixtures"
OUTPUT = ROOT / "packages/docx-editor-core/tests/pagination-corpus/baseline-lo.json"
FIXTURES = sorted(CORPUS.glob("(FAKE)-*.docx"))


def normalized_lines(page: pymupdf.Page) -> list[str]:
    return [line.strip() for line in page.get_text().splitlines() if line.strip()]


def main() -> None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise SystemExit("LibreOffice is required to regenerate the pagination baseline")
    if not FIXTURES:
        raise SystemExit("No approved synthetic DOCX fixtures were found")

    version = subprocess.run(
        [soffice, "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    baseline: dict[str, dict[str, object]] = {}

    with tempfile.TemporaryDirectory(prefix="jobos-pagination-baseline-") as temporary:
        result = subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                temporary,
                *map(str, FIXTURES),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise SystemExit(f"LibreOffice conversion failed: {result.stderr[-1000:]}")

        for fixture in FIXTURES:
            pdf_path = Path(temporary) / f"{fixture.stem}.pdf"
            if not pdf_path.is_file():
                raise SystemExit(f"LibreOffice did not produce {pdf_path.name}")
            with pymupdf.open(pdf_path) as document:
                page_lines = [normalized_lines(page) for page in document]
                baseline[fixture.stem] = {
                    "source": "LibreOffice headless from approved synthetic JobOS fixture",
                    "version": version,
                    "pages": len(document),
                    "pageStarts": [lines[0] if lines else "" for lines in page_lines],
                    "pageEnds": [lines[-1] if lines else "" for lines in page_lines],
                }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(baseline, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(baseline)} pagination baselines to {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
