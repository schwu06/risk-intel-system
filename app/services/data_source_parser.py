"""数据源文件解析与文本提取。"""

from __future__ import annotations

import logging
import re
from io import BytesIO
from pathlib import Path
from typing import Optional

import httpx
from docx import Document

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".txt", ".xlsx", ".docx", ".pdf"}


def parse_uploaded_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".docx":
        return _parse_docx(path)
    if suffix == ".xlsx":
        return _parse_xlsx(path)
    if suffix == ".pdf":
        return _parse_pdf(path)
    raise ValueError(f"不支持的文件类型: {suffix}")


def _parse_docx(path: Path) -> str:
    doc = Document(str(path))
    parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _parse_xlsx(path: Path) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("缺少 openpyxl 依赖，无法解析 xlsx") from exc

    wb = load_workbook(str(path), read_only=True, data_only=True)
    lines: list[str] = []
    for sheet in wb.worksheets:
        lines.append(f"[工作表: {sheet.title}]")
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
            if cells:
                lines.append(" | ".join(cells))
    wb.close()
    return "\n".join(lines)


def _parse_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("缺少 pypdf 依赖，无法解析 pdf") from exc

    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def fetch_url_text(url: str, timeout: int = 60) -> str:
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url, headers={"User-Agent": "RiskIntelBot/1.0"})
        resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or "").lower()
        if "html" in content_type:
            return _strip_html(resp.text)
        return resp.text


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def truncate_text(text: str, max_chars: int = 120_000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[内容已截断]"


def detect_extension(filename: str) -> Optional[str]:
    ext = Path(filename).suffix.lower()
    return ext if ext in SUPPORTED_EXTENSIONS else None
