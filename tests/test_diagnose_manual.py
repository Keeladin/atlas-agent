from __future__ import annotations

from pathlib import Path

import pymupdf

from scripts.diagnose_manual import diagnose_manual


def _born_digital(path: Path) -> None:
    doc = pymupdf.open(); page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "HYDRAULIC BRAKE SERVICE MANUAL", fontsize=14)
    page.insert_text((72, 135), "Service interval is 900 operating hours.", fontsize=12)
    page.insert_text((72, 160), "Accumulator precharge is 95 bar.", fontsize=12)
    doc.save(path); doc.close()


def _scanned(path: Path) -> None:
    source = pymupdf.open(); page = source.new_page(width=595, height=842)
    page.insert_text((72, 120), "SCANNED BRAKE MANUAL", fontsize=20)
    page.insert_text((72, 160), "Relief valve opens at 120 bar.", fontsize=15)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
    source.close()
    doc = pymupdf.open(); target = doc.new_page(width=595, height=842)
    target.insert_image(target.rect, stream=pix.tobytes("png"))
    doc.save(path); doc.close()

def _fake_ocr_provider(path: Path) -> str:
    script = path / "fake_ocr.py"
    script.write_text(
        '#!/usr/bin/env python3\n'
        'import json,sys\n'
        'sys.stdin.buffer.read()\n'
        'print(json.dumps({"text":"--- page 1 ---\\nSCANNED BRAKE MANUAL\\nRelief valve opens at 120 bar.",'
        '"media_type":"text/plain","metadata":{"pages":1,"mean_confidence":0.99},'
        '"provider_version":"fake-ocr@1"}))\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return str(script)


def test_control_born_digital_reports_text_segmentation_and_retrieval(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLAS_REPRESENTATION_PROVIDER_COMMAND", raising=False)
    pdf = tmp_path / "manual.pdf"; _born_digital(pdf)
    questions = tmp_path / "questions.txt"
    questions.write_text("What is the service interval?\t900 operating hours\n", encoding="utf-8")
    report = diagnose_manual(pdf, questions_path=questions, scratch_root=tmp_path / "scratch")
    assert report["text_extraction"]["status"] == "succeeded"
    assert report["text_extraction"]["scanned_pages"] == []
    assert report["segmentation"]["pdf_text"]["passages"] >= 1
    assert report["retrieval"]["questions"][0]["answer_present"] is True

def test_scanned_pdf_reports_ocr_recovery_and_unmet_visual_layout(tmp_path, monkeypatch):
    pdf = tmp_path / "scan.pdf"; _scanned(pdf)
    monkeypatch.setenv("ATLAS_REPRESENTATION_PROVIDER_COMMAND", _fake_ocr_provider(tmp_path))
    monkeypatch.setenv("ATLAS_REPRESENTATION_PROVIDER_NEEDS", "ocr")
    report = diagnose_manual(pdf, scratch_root=tmp_path / "scratch")
    assert report["text_extraction"]["scanned_pages"] == [1]
    assert report["ocr"]["status"] == "succeeded"
    assert report["ocr"]["pages_recovered"] == [1]
    assert report["coverage_verdict"]["layout"]["unreachable_fraction"] == 1.0
    assert report["coverage_verdict"]["visual"]["unreachable_fraction"] == 1.0


def test_provider_absent_degrades_without_failing(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLAS_REPRESENTATION_PROVIDER_COMMAND", raising=False)
    pdf = tmp_path / "scan.pdf"; _scanned(pdf)
    report = diagnose_manual(pdf, scratch_root=tmp_path / "scratch")
    assert report["ocr"]["status"] == "unavailable"
    assert "not configured" in report["ocr"]["reason"]


def test_diagnostic_writes_only_to_scratch(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLAS_REPRESENTATION_PROVIDER_COMMAND", raising=False)
    pdf = tmp_path / "manual.pdf"; _born_digital(pdf)
    protected = tmp_path / "production"; protected.mkdir(); marker = protected / "marker.txt"
    marker.write_text("unchanged", encoding="utf-8")
    report = diagnose_manual(pdf, scratch_root=tmp_path / "scratch")
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert Path(report["scratch_root"]) == (tmp_path / "scratch").resolve()
    assert report["production_touched"] is False
