"""主体授信等级推断与变更日志。"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.config import (
    CREDIT_LEVEL_ORDER,
    CREDIT_LEVELS,
    DEFAULT_TARGET_ENTITIES,
    RISK_TO_CREDIT,
)
from app.database.models import CreditUpdate, EntityRisk, TargetEntity


def seed_default_entities(db: Session) -> int:
    """写入默认监控主体，已存在则跳过。返回新增数量。"""
    created = 0
    for item in DEFAULT_TARGET_ENTITIES:
        exists = db.query(TargetEntity).filter(TargetEntity.name == item["name"]).first()
        if exists:
            continue
        db.add(
            TargetEntity(
                name=item["name"],
                display_name=item.get("display_name"),
                aliases=item.get("aliases"),
                industry=item.get("industry"),
                region=item.get("region"),
                monitor_status="active",
                credit_level="正常",
            )
        )
        created += 1
    # 历史重复主体：独立「GLP」与「普洛斯 GLP」合并，停用多余条目
    _dedupe_glp_entity(db)
    db.commit()
    return created


def _dedupe_glp_entity(db: Session) -> None:
    """将独立 GLP 主体的风险/授信记录归并到「普洛斯」，并停用 GLP。"""
    glp = db.query(TargetEntity).filter(TargetEntity.name == "GLP").first()
    if not glp:
        return
    prologis = db.query(TargetEntity).filter(TargetEntity.name == "普洛斯").first()
    if not prologis:
        glp.name = "普洛斯"
        glp.display_name = "普洛斯 GLP"
        glp.aliases = "GLP,Global Logistic Properties,普洛斯"
        glp.industry = glp.industry or "物流地产"
        glp.region = glp.region or "亚太 / 全球"
        return

    if glp.id == prologis.id:
        return

    db.query(EntityRisk).filter(EntityRisk.entity_id == glp.id).update(
        {EntityRisk.entity_id: prologis.id}, synchronize_session=False
    )
    db.query(CreditUpdate).filter(CreditUpdate.entity_id == glp.id).update(
        {CreditUpdate.entity_id: prologis.id}, synchronize_session=False
    )
    glp.monitor_status = "inactive"


def resolve_entity(
    db: Session,
    *,
    name_hint: Optional[str] = None,
    related_company: Optional[str] = None,
    create_if_missing: bool = True,
) -> Optional[TargetEntity]:
    """按名称/别名匹配主体；必要时新建。"""
    candidates = [c for c in (name_hint, related_company) if c and str(c).strip()]
    if not candidates:
        return None

    entities = db.query(TargetEntity).all()
    for hint in candidates:
        hint_l = hint.strip().lower()
        for ent in entities:
            names = [ent.name, ent.display_name or ""]
            if ent.aliases:
                names.extend(a.strip() for a in ent.aliases.split(",") if a.strip())
            for n in names:
                if n and (hint_l == n.lower() or hint_l in n.lower() or n.lower() in hint_l):
                    return ent

    if not create_if_missing:
        return None

    primary = candidates[0].strip()
    # 归一常见复合名
    if "godiva" in primary.lower() or "歌帝梵" in primary:
        primary = "Godiva"
    elif "glp" in primary.lower() or "普洛斯" in primary:
        primary = "普洛斯"

    existing = db.query(TargetEntity).filter(TargetEntity.name == primary).first()
    if existing:
        return existing

    ent = TargetEntity(
        name=primary,
        display_name=primary,
        monitor_status="active",
        credit_level="正常",
    )
    db.add(ent)
    db.flush()
    return ent


def credit_from_risk_level(risk_level: str) -> str:
    level = RISK_TO_CREDIT.get(risk_level, "关注")
    return level if level in CREDIT_LEVELS else "关注"


def suggested_credit_for_entity(db: Session, entity_id: int) -> str:
    """按该主体全部风险事件中的最高事件等级推导授信建议。"""
    risks = db.query(EntityRisk).filter(EntityRisk.entity_id == entity_id).all()
    if not risks:
        return "正常"
    best = "正常"
    best_rank = CREDIT_LEVEL_ORDER[best]
    for r in risks:
        cand = credit_from_risk_level(r.risk_level)
        rank = CREDIT_LEVEL_ORDER.get(cand, 0)
        if rank > best_rank:
            best = cand
            best_rank = rank
    return best


def apply_credit_update(
    db: Session,
    entity: TargetEntity,
    *,
    new_level: Optional[str] = None,
    reason: str = "",
    trigger_risk_id: Optional[int] = None,
    force: bool = False,
) -> Optional[CreditUpdate]:
    """
    更新主体授信等级并写变更日志。
    默认仅在等级上升时更新；force=True 时按建议等级同步（含下调）。
    """
    target = new_level or suggested_credit_for_entity(db, entity.id)
    if target not in CREDIT_LEVELS:
        target = "关注"

    prev = entity.credit_level or "正常"
    prev_rank = CREDIT_LEVEL_ORDER.get(prev, 1)
    new_rank = CREDIT_LEVEL_ORDER.get(target, 1)

    if not force and new_rank <= prev_rank and target == prev:
        return None
    if not force and new_rank < prev_rank:
        # 默认不自动下调
        return None
    if target == prev:
        return None

    entity.credit_level = target
    log = CreditUpdate(
        entity_id=entity.id,
        previous_level=prev,
        new_level=target,
        reason=reason or f"依据风险事件自动调整：{prev} → {target}",
        trigger_risk_id=trigger_risk_id,
    )
    db.add(log)
    db.flush()
    return log


def refresh_entity_credit(
    db: Session,
    entity: TargetEntity,
    *,
    trigger_risk: Optional[EntityRisk] = None,
    reason: str = "",
) -> Optional[CreditUpdate]:
    suggested = suggested_credit_for_entity(db, entity.id)
    return apply_credit_update(
        db,
        entity,
        new_level=suggested,
        reason=reason
        or (
            f"风险事件「{trigger_risk.title}」触发授信复核"
            if trigger_risk
            else "风险事件集合变更后复核授信等级"
        ),
        trigger_risk_id=trigger_risk.id if trigger_risk else None,
    )
