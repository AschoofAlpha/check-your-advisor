"""Per-paper PDF download / validation reports."""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from pathlib import Path

HEADERS = [
    "pmid",
    "title",
    "pub_date",
    "doi",
    "pmc_id",
    "pdf_status",
    "pdf_source",
    "validation_reason",
    "pdf_path",
    "file_size_bytes",
    "suspect_path",
]


_SOURCE_RE = re.compile(r"\(([^)]+)\)\s*$")


def _extract_source(pdf_status: str) -> str:
    """'已下载(Europe PMC)' → 'Europe PMC'; '已存在' → ''."""
    if not pdf_status:
        return ""
    match = _SOURCE_RE.search(pdf_status)
    return match.group(1) if match else ""


def _find_pdf(pdf_dir: Path, pmid: str) -> Path | None:
    for cand in pdf_dir.glob(f"{pmid}_*.pdf"):
        if "suspect" in cand.parts:
            continue
        return cand
    return None


def _find_suspect(pdf_dir: Path, pmid: str) -> Path | None:
    suspect_dir = pdf_dir / "suspect"
    if not suspect_dir.is_dir():
        return None
    cands = sorted(suspect_dir.glob(f"{pmid}_*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def write_pdf_validation_report(
    papers: Iterable[dict],
    csv_path: str,
    pdf_dir: str | None = None,
) -> str:
    """
    Write per-paper download outcome:
      pmid, title, doi, pdf_status, source, validation_reason, file_size, suspect_path.

    Reads what engine.DownloadEngine already populated on each paper dict
    (pdf_status, pdf_validation) plus the on-disk file layout under pdf_dir.
    """
    csv_p = Path(csv_path)
    csv_p.parent.mkdir(parents=True, exist_ok=True)
    pdf_root = Path(pdf_dir) if pdf_dir else None

    with csv_p.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for paper in papers:
            pmid = str(paper.get("pmid", ""))
            pdf_status = paper.get("pdf_status", "") or ""
            pdf_path = ""
            file_size: int | str = ""
            suspect_path = ""
            if pdf_root and pmid:
                main = _find_pdf(pdf_root, pmid)
                if main is not None:
                    pdf_path = str(main)
                    try:
                        file_size = main.stat().st_size
                    except OSError:
                        pass
                suspect = _find_suspect(pdf_root, pmid)
                if suspect is not None:
                    suspect_path = str(suspect)
            writer.writerow({
                "pmid": pmid,
                "title": (paper.get("title") or "")[:200],
                "pub_date": paper.get("pub_date", ""),
                "doi": paper.get("doi", ""),
                "pmc_id": paper.get("pmc_id", ""),
                "pdf_status": pdf_status,
                "pdf_source": _extract_source(pdf_status),
                "validation_reason": paper.get("pdf_validation", ""),
                "pdf_path": pdf_path,
                "file_size_bytes": file_size,
                "suspect_path": suspect_path,
            })
    return str(csv_p)
