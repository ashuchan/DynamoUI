"""
QueryTranslator — converts QueryPlan into a SQLAlchemy Core select() statement.

Rules:
- All SQL is built via SQLAlchemy Core parameterised builders.
- No string concatenation in query construction.
- New filter operators: add a lambda to FILTER_OPS only.
- Field resolution always goes through _resolve_col (snake_case → db_column_name).
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
    All field-name resolution goes through _resolve_col so that snake_case
    logical names (from the skill schema) map correctly to PascalCase DB column
    names (e.g. album_id → AlbumId in Chinook).
    """

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

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

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
            cols = [
                self._resolve_col(table, f, skill)
                for f in plan.select_fields
                if self._resolve_col(table, f, skill) is not None
            ]
            if not cols:
                cols = [table]
        else:
            cols = [table]

        stmt = sa.select(*cols)
        count_stmt = sa.select(sa.func.count()).select_from(table)

        # Filters on primary table
        for f in plan.filters:
            stmt, count_stmt = self._apply_filter(table, stmt, count_stmt, f, skill)

        # Joins — resolve each side through _resolve_col so snake_case works
        joined_tables: dict[str, sa.Table] = {}
        joined_skills: dict[str, EntitySkill] = {}

        if plan.joins:
            if self._skill_registry is None:
                raise ValueError(
                    "QueryTranslator requires skill_registry to resolve joins"
                )
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
                joined_skills[join.target_entity] = target_skill

                # For multi-hop joins the source field may live on a previously
                # joined table rather than the primary table (e.g. track_id is on
                # Track, not Album, when joining Track→InvoiceLine).
                src_col = self._resolve_any(
                    join.source_field, table, skill, joined_tables, joined_skills
                )
                tgt_col = self._resolve_col(target_table, join.target_field, target_skill)

                if src_col is None:
                    log.warning(
                        "query_translator.join_source_field_not_found",
                        field=join.source_field,
                        entity=skill.entity,
                    )
                    continue
                if tgt_col is None:
                    log.warning(
                        "query_translator.join_target_field_not_found",
                        field=join.target_field,
                        entity=join.target_entity,
                    )
                    continue

                condition = src_col == tgt_col
                if join.join_type == "inner":
                    stmt = stmt.join(target_table, condition)
                    count_stmt = count_stmt.join(target_table, condition)
                else:
                    stmt = stmt.outerjoin(target_table, condition)
                    count_stmt = count_stmt.outerjoin(target_table, condition)

        # Aggregations
        aggregation_aliases: set[str] = {agg.alias for agg in plan.aggregations}

        if plan.aggregations:
            agg_cols = []
            for agg in plan.aggregations:
                col = self._resolve_any(
                    agg.field, table, skill, joined_tables, joined_skills
                )
                if agg.func == "count" and (agg.field == "*" or col is None):
                    agg_cols.append(sa.func.count().label(agg.alias))
                elif col is None:
                    log.warning(
                        "query_translator.agg_field_not_found",
                        field=agg.field,
                        entity=skill.entity,
                    )
                    continue
                elif agg.func == "count":
                    agg_cols.append(sa.func.count(col).label(agg.alias))
                elif agg.func == "sum":
                    agg_cols.append(sa.func.sum(col).label(agg.alias))
                elif agg.func == "avg":
                    agg_cols.append(sa.func.avg(col).label(agg.alias))
                elif agg.func == "min":
                    agg_cols.append(sa.func.min(col).label(agg.alias))
                elif agg.func == "max":
                    agg_cols.append(sa.func.max(col).label(agg.alias))

            if agg_cols:
                # Rebuild select from the full join chain.
                # Track which tables are in from_clause so far, so that multi-hop
                # source fields (e.g. track_id on Track for Track→InvoiceLine) can
                # be resolved against previously joined tables, not just the primary.
                from_clause = table
                agg_joined: dict[str, sa.Table] = {}
                agg_joined_skills: dict[str, EntitySkill] = {}
                for join in plan.joins:
                    target_table = joined_tables.get(join.target_entity)
                    if target_table is None:
                        continue
                    t_skill = joined_skills.get(join.target_entity)
                    src_col = self._resolve_any(
                        join.source_field, table, skill, agg_joined, agg_joined_skills
                    )
                    tgt_col = self._resolve_col(target_table, join.target_field, t_skill)
                    agg_joined[join.target_entity] = target_table
                    agg_joined_skills[join.target_entity] = t_skill
                    if src_col is None or tgt_col is None:
                        continue
                    condition = src_col == tgt_col
                    if join.join_type == "inner":
                        from_clause = from_clause.join(target_table, condition)
                    else:
                        from_clause = from_clause.outerjoin(target_table, condition)

                # Include group_by columns in SELECT so they appear in result rows
                group_select_cols = []
                for gf in plan.group_by:
                    col = self._resolve_any(
                        gf, table, skill, agg_joined, agg_joined_skills
                    )
                    if col is not None:
                        group_select_cols.append(col)

                stmt = sa.select(*group_select_cols, *agg_cols).select_from(from_clause)

                # Re-apply primary filters on the aggregation query
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
            group_cols = []
            for gf in plan.group_by:
                col = self._resolve_any(
                    gf, table, skill, joined_tables, joined_skills
                )
                if col is not None:
                    group_cols.append(col)
                else:
                    log.warning(
                        "query_translator.group_by_field_not_found",
                        field=gf,
                        entity=skill.entity,
                    )
            if group_cols:
                stmt = stmt.group_by(*group_cols)

        # Sort — supports both real columns and aggregation aliases
        for sort in plan.sort:
            if sort.field in aggregation_aliases:
                label_col = sa.literal_column(sort.field)
                stmt = stmt.order_by(
                    label_col.asc() if sort.dir == "asc" else label_col.desc()
                )
            else:
                col = self._resolve_any(
                    sort.field, table, skill, joined_tables, joined_skills
                )
                if col is None:
                    log.warning(
                        "query_translator.unknown_sort_field",
                        field=sort.field,
                        entity=skill.entity,
                    )
                    continue
                stmt = stmt.order_by(
                    col.asc() if sort.dir == "asc" else col.desc()
                )

        # Result limit (TOP N) — mutually exclusive with pagination
        if plan.result_limit is not None:
            stmt = stmt.limit(plan.result_limit)
            count_stmt = sa.select(sa.func.count()).select_from(stmt.subquery())
        else:
            offset = (plan.page - 1) * plan.page_size
            stmt = stmt.limit(plan.page_size).offset(offset)

        log.debug(
            "query_translator.built",
            entity=skill.entity,
            filters=len(plan.filters),
            sort=len(plan.sort),
            joins=len(plan.joins),
            aggregations=len(plan.aggregations),
            group_by=plan.group_by,
            result_limit=plan.result_limit,
        )
        return stmt, count_stmt

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_col(
        self, table: sa.Table, field_name: str, skill: EntitySkill | None
    ) -> sa.Column | None:
        """
        Resolve a logical snake_case field name to its SQLAlchemy column.
        Tries the name directly first (handles already-correct DB names),
        then walks skill.fields to find the db_column_name mapping.
        Returns None if the field cannot be resolved.
        """
        if not field_name:
            return None
        if field_name in table.c:
            return table.c[field_name]
        if skill is not None:
            for f in skill.fields:
                if f.name == field_name:
                    db_name = f.db_column_name or f.name
                    if db_name in table.c:
                        return table.c[db_name]
        return None

    def _resolve_any(
        self,
        field_name: str,
        primary_table: sa.Table,
        primary_skill: EntitySkill,
        joined_tables: dict[str, sa.Table],
        joined_skills: dict[str, EntitySkill],
    ) -> sa.Column | None:
        """
        Resolve a snake_case field name against the primary table first,
        then all joined tables in declaration order.
        Returns None if the field cannot be found anywhere.
        """
        if not field_name:
            return None

        col = self._resolve_col(primary_table, field_name, primary_skill)
        if col is not None:
            return col

        for entity_name, tbl in joined_tables.items():
            j_skill = joined_skills.get(entity_name)
            col = self._resolve_col(tbl, field_name, j_skill)
            if col is not None:
                return col

        return None

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
