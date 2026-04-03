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

from backend.adapters.base import FilterClause, JoinClause, AggregationClause, QueryPlan
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

    def __init__(
        self,
        table_builder,
        skill_registry: "SkillRegistry | None" = None,
    ) -> None:
        self._table_builder = table_builder
        self._skill_registry = skill_registry

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

        # Joins
        joined_tables: dict[str, sa.Table] = {}
        if plan.joins:
            if self._skill_registry is None:
                raise ValueError("QueryTranslator requires skill_registry to resolve joins")
            for join in plan.joins:
                target_skill = self._skill_registry.entity_by_name.get(join.target_entity)
                if target_skill is None:
                    log.warning(
                        "query_translator.unknown_join_entity",
                        entity=join.target_entity,
                    )
                    continue
                target_table = self._table_builder.build(target_skill)
                joined_tables[join.target_entity] = target_table
                condition = table.c[join.source_field] == target_table.c[join.target_field]
                if join.join_type == "inner":
                    stmt = stmt.join(target_table, condition)
                else:
                    stmt = stmt.outerjoin(target_table, condition)

        # Aggregations
        if plan.aggregations:
            def resolve_col_multi(field_name: str) -> sa.Column | None:
                col = self._resolve_col(table, field_name, skill)
                if col is not None:
                    return col
                for tbl in joined_tables.values():
                    if field_name in tbl.c:
                        return tbl.c[field_name]
                return None

            agg_cols = []
            for agg in plan.aggregations:
                if agg.func == "count" and agg.field == "*":
                    agg_cols.append(sa.func.count().label(agg.alias))
                elif agg.func == "count":
                    col = resolve_col_multi(agg.field)
                    if col is not None:
                        agg_cols.append(sa.func.count(col).label(agg.alias))
                elif agg.func == "sum":
                    col = resolve_col_multi(agg.field)
                    if col is not None:
                        agg_cols.append(sa.func.sum(col).label(agg.alias))
                elif agg.func == "avg":
                    col = resolve_col_multi(agg.field)
                    if col is not None:
                        agg_cols.append(sa.func.avg(col).label(agg.alias))
                elif agg.func == "min":
                    col = resolve_col_multi(agg.field)
                    if col is not None:
                        agg_cols.append(sa.func.min(col).label(agg.alias))
                elif agg.func == "max":
                    col = resolve_col_multi(agg.field)
                    if col is not None:
                        agg_cols.append(sa.func.max(col).label(agg.alias))
            if agg_cols:
                stmt = sa.select(*agg_cols).select_from(stmt.froms[0])
                # Re-apply joins to the new select
                for join in plan.joins:
                    target_table = joined_tables.get(join.target_entity)
                    if target_table is None:
                        continue
                    condition = table.c[join.source_field] == target_table.c[join.target_field]
                    if join.join_type == "inner":
                        stmt = stmt.join(target_table, condition)
                    else:
                        stmt = stmt.outerjoin(target_table, condition)
                # Re-apply filters to aggregation stmt
                for f in plan.filters:
                    col = self._resolve_col(table, f.field, skill)
                    if col is None:
                        continue
                    op_fn = self.FILTER_OPS.get(f.op)
                    if op_fn is None:
                        continue
                    stmt = stmt.where(op_fn(col, f.value))

        # Group by
        if plan.group_by:
            def resolve_col_multi_gb(field_name: str) -> sa.Column | None:
                col = self._resolve_col(table, field_name, skill)
                if col is not None:
                    return col
                for tbl in joined_tables.values():
                    if field_name in tbl.c:
                        return tbl.c[field_name]
                return None

            group_cols = []
            for gf in plan.group_by:
                col = resolve_col_multi_gb(gf)
                if col is not None:
                    group_cols.append(col)
            if group_cols:
                stmt = stmt.group_by(*group_cols)

        # Sorting
        for sort in plan.sort:
            col = self._resolve_col(table, sort.field, skill)
            if col is None:
                log.warning(
                    "query_translator.unknown_sort_field",
                    field=sort.field,
                    entity=skill.entity,
                )
                continue
            stmt = stmt.order_by(col.asc() if sort.dir == "asc" else col.desc())

        # Result limit (TOP N) — mutually exclusive with pagination
        if plan.result_limit is not None:
            stmt = stmt.limit(plan.result_limit)
            count_stmt = sa.select(sa.func.count()).select_from(stmt.subquery())
        else:
            # Pagination
            offset = (plan.page - 1) * plan.page_size
            stmt = stmt.limit(plan.page_size).offset(offset)

        log.debug(
            "query_translator.built",
            entity=skill.entity,
            filters=len(plan.filters),
            sort=len(plan.sort),
            joins=len(plan.joins),
            aggregations=len(plan.aggregations),
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
        col = self._resolve_col(table, f.field, skill)
        if col is None:
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

        condition = op_fn(col, f.value)
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
        return stmt, count_stmt

    def _resolve_col(
        self, table: sa.Table, field_name: str, skill: EntitySkill
    ) -> sa.Column | None:
        """Resolve a logical field name or db_column_name to its SQLAlchemy column."""
        if field_name in table.c:
            return table.c[field_name]
        for f in skill.fields:
            if f.name == field_name:
                db_name = f.db_column_name or f.name
                if db_name in table.c:
                    return table.c[db_name]
        return None
