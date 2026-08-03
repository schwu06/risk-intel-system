"""python-docx 中文日报导出。"""

from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Iterable, Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from app.config import MODULE_CODES
from app.database.models import DailyRiskEntry, IndustryReport
from app.services.chart_generator import extract_chart_specs, render_chart_png


def _set_run_font(run, size_pt: int = 11, bold: bool = False, color: Optional[RGBColor] = None):
    run.font.name = "宋体"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    if color:
        run.font.color.rgb = color


def _add_heading(doc: Document, text: str, level: int = 1) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12 if level == 1 else 8)
    p.paragraph_format.space_after = Pt(6)
    run = p.add_run(text)
    sizes = {1: 18, 2: 14, 3: 12}
    _set_run_font(run, size_pt=sizes.get(level, 12), bold=True, color=RGBColor(0x1A, 0x23, 0x4A))


def _add_body(doc: Document, text: str, indent: bool = False) -> None:
    p = doc.add_paragraph()
    if indent:
        p.paragraph_format.left_indent = Pt(18)
    p.paragraph_format.line_spacing = 1.25
    run = p.add_run(text)
    _set_run_font(run, size_pt=11, color=RGBColor(0x33, 0x33, 0x33))


def _add_meta_line(doc: Document, label: str, value: str) -> None:
    p = doc.add_paragraph()
    r1 = p.add_run(f"{label}：")
    _set_run_font(r1, bold=True, color=RGBColor(0x44, 0x44, 0x44))
    r2 = p.add_run(value)
    _set_run_font(r2, color=RGBColor(0x33, 0x33, 0x33))


def build_daily_report_docx(
    entries: Iterable[DailyRiskEntry],
    report_date: date,
    institution_name: str = "风险管理部",
    module_codes: Optional[list[str]] = None,
) -> Document:
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Pt(72)
    section.bottom_margin = Pt(72)
    section.left_margin = Pt(72)
    section.right_margin = Pt(72)

    if module_codes:
        codes = [c.upper() for c in module_codes if c.upper() in MODULE_CODES]
        module_map = {c: MODULE_CODES[c] for c in codes}
    else:
        module_map = dict(MODULE_CODES)

    title_p = doc.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tr = title_p.add_run("企业风险情报日报")
    _set_run_font(tr, size_pt=22, bold=True, color=RGBColor(0x0F, 0x17, 0x2A))

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sr = sub.add_run(f"{institution_name} | 报告日期 {report_date.isoformat()}")
    _set_run_font(sr, size_pt=11, color=RGBColor(0x55, 0x55, 0x55))

    doc.add_paragraph()
    _add_body(
        doc,
        "本报告由自动化风险情报平台生成，涵盖所选业务模块的风险情报条目。"
        "条目均附来源链接，供后续人工复核。",
    )

    grouped: dict[str, list[DailyRiskEntry]] = {k: [] for k in module_map}
    footnotes: list[tuple[int, str, str]] = []
    fn_index = 1

    for entry in entries:
        if entry.module_code in module_map:
            grouped.setdefault(entry.module_code, []).append(entry)

    for module_code, module_name in module_map.items():
        mod_entries = grouped.get(module_code) or []
        _add_heading(doc, module_name, level=1)
        if not mod_entries:
            _add_body(doc, "本日无新增风险条目或未触发有效检索结果。", indent=True)
            continue

        for idx, e in enumerate(mod_entries, start=1):
            _add_heading(doc, f"{idx}. {e.title}", level=2)
            _add_meta_line(doc, "风险等级", e.risk_level)
            if e.related_company:
                _add_meta_line(doc, "关联企业", e.related_company)
            if e.risk_category:
                _add_meta_line(doc, "风险类别", e.risk_category)
            if e.target_entity:
                _add_meta_line(doc, "监控对象", e.target_entity)
            if e.country_or_region:
                _add_meta_line(doc, "国家或地区", e.country_or_region)
            _add_meta_line(doc, "核心摘要", e.summary)
            if e.impact_analysis:
                _add_meta_line(doc, "影响分析", e.impact_analysis)
            chart_specs = _entry_chart_specs(e)
            for idx_c, spec in enumerate(chart_specs[:2]):
                img_path = render_chart_png(spec, Path(f"data/exports/charts/entry_{e.id}_{idx_c}.png"))
                if img_path and img_path.is_file():
                    doc.add_picture(str(img_path), width=Inches(5.5))
            if e.source_url:
                mark = fn_index
                footnotes.append((mark, e.title, e.source_url))
                _add_meta_line(doc, "来源", f"见脚注 [{mark}]")
                fn_index += 1

    if footnotes:
        _add_heading(doc, "来源脚注", level=1)
        for mark, title, url in footnotes:
            _add_body(doc, f"[{mark}] {title} — {url}")

    footer = doc.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fr = footer.add_run("— 内部资料，未经许可不得对外传播 —")
    _set_run_font(fr, size_pt=9, color=RGBColor(0x77, 0x77, 0x77))

    return doc


def _entry_chart_specs(entry: DailyRiskEntry) -> list[dict]:
    import json

    if not entry.structured_json:
        return extract_chart_specs(entry.summary or "")
    try:
        data = json.loads(entry.structured_json)
        if isinstance(data, dict) and data.get("_chart_specs"):
            raw = data["_chart_specs"]
            if isinstance(raw, str):
                return json.loads(raw)
            if isinstance(raw, list):
                return raw
    except (json.JSONDecodeError, TypeError):
        pass
    return extract_chart_specs(entry.summary or "")


def build_industry_report_docx(report: IndustryReport) -> Document:
    import json

    doc = Document()
    title_p = doc.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tr = title_p.add_run(f"{report.industry_name} — 行业分析报告")
    _set_run_font(tr, size_pt=20, bold=True, color=RGBColor(0x0F, 0x17, 0x2A))

    if report.company_name:
        sub = doc.add_paragraph()
        sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
        sr = sub.add_run(f"分析对象: {report.company_name}")
        _set_run_font(sr, size_pt=11, color=RGBColor(0x55, 0x55, 0x55))

    payload = {}
    if report.report_json:
        try:
            payload = json.loads(report.report_json)
        except json.JSONDecodeError:
            payload = {}

    if payload.get("summary"):
        _add_heading(doc, "执行摘要", level=1)
        _add_body(doc, str(payload["summary"]))
    for sec in payload.get("sections") or []:
        if isinstance(sec, dict):
            _add_heading(doc, str(sec.get("heading", "")), level=2)
            _add_body(doc, str(sec.get("content", "")))
    if payload.get("risk_outlook"):
        _add_heading(doc, "风险展望", level=1)
        _add_body(doc, str(payload["risk_outlook"]))

    if report.chart_specs:
        try:
            charts = json.loads(report.chart_specs)
        except json.JSONDecodeError:
            charts = []
        for idx, item in enumerate(charts):
            opt = item.get("option") if isinstance(item, dict) else None
            if not opt:
                continue
            spec = {
                "type": (opt.get("series") or [{}])[0].get("type", "bar"),
                "title": (opt.get("title") or {}).get("text", "图表"),
                "labels": (opt.get("xAxis") or {}).get("data", []),
                "series": [{"name": s.get("name", ""), "data": s.get("data", [])} for s in opt.get("series", [])],
            }
            img_path = render_chart_png(spec, Path(f"data/exports/charts/industry_{report.id}_{idx}.png"))
            if img_path and img_path.is_file():
                doc.add_picture(str(img_path), width=Inches(5.5))

    footer = doc.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fr = footer.add_run("— 内部资料，未经许可不得对外传播 —")
    _set_run_font(fr, size_pt=9, color=RGBColor(0x77, 0x77, 0x77))
    return doc


def export_industry_report_to_path(report: IndustryReport, output_path: Path) -> Path:
    doc = build_industry_report_docx(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return output_path


def export_daily_report_to_path(
    entries: list[DailyRiskEntry],
    report_date: date,
    output_path: Path,
    institution_name: str = "风险管理部",
    module_codes: Optional[list[str]] = None,
) -> Path:
    doc = build_daily_report_docx(
        entries, report_date, institution_name, module_codes=module_codes
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return output_path


def export_daily_report_to_bytes(
    entries: list[DailyRiskEntry],
    report_date: date,
    institution_name: str = "风险管理部",
    module_codes: Optional[list[str]] = None,
) -> bytes:
    doc = build_daily_report_docx(
        entries, report_date, institution_name, module_codes=module_codes
    )
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
