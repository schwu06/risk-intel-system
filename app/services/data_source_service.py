"""统一权威数据源管理（全站共用，不分模块）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.database.models import IndustryDataSource, ModuleDataSource
from app.services.data_source_parser import fetch_url_text, parse_uploaded_file, truncate_text

logger = logging.getLogger(__name__)

UPLOAD_ROOT = Path("data/uploads/modules")
INDUSTRY_UPLOAD_ROOT = Path("data/uploads/industry")
# 统一数据源标记（兼容旧按模块上传的记录：列表与读取时一并纳入）
GLOBAL_SOURCE_CODE = "ALL"


def ensure_upload_dirs() -> None:
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    INDUSTRY_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)


def list_all_sources(db: Session) -> list[ModuleDataSource]:
    """列出全部启用的统一数据源（含历史按模块上传的记录）。"""
    return (
        db.query(ModuleDataSource)
        .filter(ModuleDataSource.is_active.is_(True))
        .order_by(ModuleDataSource.priority.desc(), ModuleDataSource.created_at.desc())
        .all()
    )


def get_source_by_id(db: Session, source_id: int) -> Optional[ModuleDataSource]:
    return db.query(ModuleDataSource).filter(ModuleDataSource.id == source_id).first()


def list_module_sources(db: Session, module_code: str | None = None) -> list[ModuleDataSource]:
    """兼容旧接口：现统一返回全站数据源。"""
    return list_all_sources(db)


def get_shared_authoritative_text(db: Session, max_chars: int = 80_000) -> str:
    sources = list_all_sources(db)
    blocks: list[str] = []
    used = 0
    for src in sources:
        text = (src.extracted_text or "").strip()
        if not text:
            continue
        header = f"【权威数据源: {src.name}】"
        chunk = f"{header}\n{text}"
        if used + len(chunk) > max_chars:
            remaining = max_chars - used
            if remaining <= 200:
                break
            chunk = chunk[:remaining] + "\n...[截断]"
        blocks.append(chunk)
        used += len(chunk)
    return "\n\n".join(blocks)


def get_module_authoritative_text(db: Session, module_code: str, max_chars: int = 80_000) -> str:
    """任意模块流水线均读取同一套统一数据源。"""
    return get_shared_authoritative_text(db, max_chars=max_chars)


def save_module_file_source(
    db: Session,
    module_code: str | None,
    name: str,
    filename: str,
    file_bytes: bytes,
    priority: int = 0,
) -> ModuleDataSource:
    ensure_upload_dirs()
    code = GLOBAL_SOURCE_CODE
    dest_dir = UPLOAD_ROOT / code
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    dest = dest_dir / safe_name
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        counter = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1
    dest.write_bytes(file_bytes)
    extracted = truncate_text(parse_uploaded_file(dest))
    row = ModuleDataSource(
        module_code=code,
        name=name or safe_name,
        source_type="file",
        file_path=str(dest),
        original_filename=safe_name,
        extracted_text=extracted,
        priority=priority,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def save_module_url_source(
    db: Session,
    module_code: str | None,
    name: str,
    url: str,
    priority: int = 0,
) -> ModuleDataSource:
    text = truncate_text(fetch_url_text(url))
    row = ModuleDataSource(
        module_code=GLOBAL_SOURCE_CODE,
        name=name or url,
        source_type="url",
        url=url,
        extracted_text=text,
        priority=priority,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_module_source(db: Session, source_id: int) -> bool:
    row = db.query(ModuleDataSource).filter(ModuleDataSource.id == source_id).first()
    if not row:
        return False
    if row.file_path:
        path = Path(row.file_path)
        if path.is_file():
            path.unlink(missing_ok=True)
    db.delete(row)
    db.commit()
    return True


def list_industry_sources(db: Session, industry_name: str) -> list[IndustryDataSource]:
    return (
        db.query(IndustryDataSource)
        .filter(
            IndustryDataSource.industry_name == industry_name,
            IndustryDataSource.is_active.is_(True),
        )
        .order_by(IndustryDataSource.created_at.desc())
        .all()
    )


def get_industry_authoritative_text(db: Session, industry_name: str, max_chars: int = 100_000) -> str:
    """研报优先使用统一数据源，并合并该行业历史专用源（若有）。"""
    shared = get_shared_authoritative_text(db, max_chars=max_chars // 2)
    sources = list_industry_sources(db, industry_name)
    blocks: list[str] = []
    used = len(shared)
    for src in sources:
        text = (src.extracted_text or "").strip()
        if not text:
            continue
        header = f"【行业数据源: {src.name}】"
        chunk = f"{header}\n{text}"
        if used + len(chunk) > max_chars:
            remaining = max_chars - used
            if remaining <= 200:
                break
            chunk = chunk[:remaining] + "\n...[截断]"
        blocks.append(chunk)
        used += len(chunk)
    industry_part = "\n\n".join(blocks)
    if shared and industry_part:
        return f"{shared}\n\n{industry_part}"
    return shared or industry_part


def save_industry_file_source(
    db: Session,
    industry_name: str,
    name: str,
    filename: str,
    file_bytes: bytes,
) -> IndustryDataSource:
    ensure_upload_dirs()
    dest_dir = INDUSTRY_UPLOAD_ROOT / _safe_dirname(industry_name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    dest = dest_dir / safe_name
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        counter = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1
    dest.write_bytes(file_bytes)
    extracted = truncate_text(parse_uploaded_file(dest))
    row = IndustryDataSource(
        industry_name=industry_name,
        name=name or safe_name,
        source_type="file",
        file_path=str(dest),
        original_filename=safe_name,
        extracted_text=extracted,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def save_industry_url_source(
    db: Session,
    industry_name: str,
    name: str,
    url: str,
) -> IndustryDataSource:
    text = truncate_text(fetch_url_text(url))
    row = IndustryDataSource(
        industry_name=industry_name,
        name=name or url,
        source_type="url",
        url=url,
        extracted_text=text,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_industry_source(db: Session, source_id: int) -> bool:
    row = db.query(IndustryDataSource).filter(IndustryDataSource.id == source_id).first()
    if not row:
        return False
    if row.file_path:
        Path(row.file_path).unlink(missing_ok=True)
    db.delete(row)
    db.commit()
    return True


def _safe_dirname(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.strip())
    return cleaned[:64] or "default"
