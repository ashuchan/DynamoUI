"""
QueryTranslator — converts QueryPlan into a SQLAlchemy Core select() statement.

Rules:
- All SQL is built via SQLAlchemy Core parameterised builders.
- No string concatenation in query construction.
- New filter operators: add a lambda to FILTER_OPS only.
"""
from __future__ import annotations

import structlog
import sqlalchemy as sa
from sqlalchemy import Select

from backend.adapters.base import FilterClause, QueryPlan
from backend.skill_registry.models.skill import EntitySkill

log = structlog.get_logger(__name__)


class QueryTranslator:
    """
    Translates a QueryPlan into an executable SQLAlchemy select statement.
    """

    # Locked operator set — add new ops here only, never via string SQL
    FILTER_OPS = {
        "eq":      lambda c, v: c == v,
        "ne":      lambda c, v: c != v,
        "gt":      lambda c, v: c > v,
        "gte":     lambda c, v: c >= v,
        "lt":      lambda c, v: c < v,
        "lte":     lambda c, v: c <= v,
        "in":      lambda c, v: c.in_(v),
        "like":    lambda c, v: c.ilike(f"%{v}%"),
        "is_null": lambda c, v: c.is_(None) if v else c.isnot(None),
    }

    def __init__(self, table_builder) -> None:
        self._table_builder = table_builder

    def build_select(
        self,
        skill: EntitySkill,
        plan: QueryPlan,
    ) -> tuple[Select, Select]:
        """
        Build (data_query, count_query) for the given plan.
        Returns two separate queries: one for data rows, one for total count.
        """
        table = self._table_builder.build(skill)

        # Column selection
        if plan.select_fields:
            cols = [table.c[f] for f in plan.select_fields if f in table.c]
        else:
            cols = [table]

        stmt = sa.select(*cols)
        count_stmt = sa.select(sa.func.count()).select_from(table)

        # Filters
        for f in plan.filters:
            stmt, count_stmt = self._apply_filter(table, stmt, count_stmt, f, skill)

        # Sorting
        for sort in plan.sort:
            if sort.field not in table.c:
                log.warning(
                    "query_translator.unknown_sort_field",
                    field=sort.field,
                    entity=skill.entity,
                )
                continue
            col = table.c[sort.field]
            stmt = stmt.order_by(col.asc() if sort.dir == "asc" else col.desc())

        # Pagination
        offset = (plan.page - 1) * plan.page_size
        stmt = stmt.limit(plan.page_size).offset(offset)

        log.debug(
            "query_translator.built",
            entity=skill.entity,
            filters=len(plan.filters),
            sort=len(plan.sort),
            page=plan.page,
            page_size=plan.page_size,
        )
        return stmt, count_stmt

    def _apply_filter(
        self,
        table: sa.Table,
        stmt: Select,
        count_stmt: Select,
        f: FilterClause,
        skill: EntitySkill,
    ) -> tuple[Select, Select]:
        if f.field not in table.c:
            log.warning(
                "query_translator.unknown_filter_field",
                field=f.field,
                entity=skill.entity,
            )
            return stmt, count_stmt

        op_fn = self.FILTER_OPS.get(f.op)
        if op_fn is None:
            log.warning("query_translator.unknown_filter_op", op=f.op)
            return stmt, count_stmt

        col = table.c[f.field]
        condition = op_fn(col, f.value)
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
        return stmt, count_stmt
