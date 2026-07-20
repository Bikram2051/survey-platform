"""
RLS guardrail.

Introspects the live schema: every table carrying a tenant_id column MUST
have row-level security ENABLED and FORCED, unless it appears in the
exemption list below WITH a written justification. Adding a tenant-owned
model and forgetting its RLS migration fails CI here, which is the entire
point: isolation must not depend on anyone remembering.
"""
import pytest
from django.db import connection

# Every exemption must say WHY. An exemption without a reason is a bug.
EXEMPT = {
    # Bootstrap tables: they are what CREATES tenant context, so they must
    # be readable before one exists. Neither holds respondent data.
    "accounts_tenantmembership": "membership lookup happens pre-context (scoping.py)",
    "surveys_publiclink": "anonymous respondents bootstrap context from the link token",
}


@pytest.mark.django_db
def test_every_tenant_id_table_is_rls_forced_or_exempt():
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT c.table_name, pc.relrowsecurity, pc.relforcerowsecurity
            FROM information_schema.columns c
            JOIN pg_class pc ON pc.relname = c.table_name
            JOIN pg_namespace n ON n.oid = pc.relnamespace AND n.nspname = 'public'
            WHERE c.column_name = 'tenant_id'
              AND c.table_schema = 'public'
              AND pc.relkind = 'r'
            ORDER BY c.table_name
            """
        )
        rows = cur.fetchall()

    assert rows, "introspection found no tenant_id tables: query is broken"
    problems = []
    for table, enabled, forced in rows:
        if table in EXEMPT:
            continue
        if not (enabled and forced):
            problems.append(f"{table}: rls_enabled={enabled}, rls_forced={forced}")
    assert not problems, (
        "Tables with tenant_id lacking FORCED row-level security "
        f"(add the RLS migration or an explicit justified exemption): {problems}"
    )


@pytest.mark.django_db
def test_exemption_list_is_not_stale():
    """An exemption for a table that no longer exists means the list rotted."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        existing = {r[0] for r in cur.fetchall()}
    stale = set(EXEMPT) - existing
    assert not stale, f"exempted tables that do not exist: {sorted(stale)}"
