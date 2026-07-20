"""
Row-level security on surveys_survey. TEMPLATE for every tenant-owned table.

Why FORCE and not just ENABLE (the classic silent failure):
  ENABLE ROW LEVEL SECURITY does NOT apply policies to the table's OWNER.
  Django migrations run as app_user, so app_user owns every table, so with
  ENABLE alone the app would bypass its own isolation and all tests would
  pass vacuously while production leaked. FORCE ROW LEVEL SECURITY applies
  policies to the owner as well. Superusers and BYPASSRLS roles still
  bypass, which is why settings.py insists the runtime role is neither.

Policy semantics (default-deny):
  current_setting('app.current_tenant', true) returns NULL when the
  variable is unset ('true' = missing_ok). NULL = anything is NULL, and a
  NULL policy predicate admits no rows (USING) and rejects all writes
  (WITH CHECK). No tenant context -> the table is empty and read-only.
  Wrong tenant context -> other tenants' rows do not exist.

Why NULLIF(..., '') and not a bare cast (found by the test suite):
  Postgres quirk: after a transaction that SET LOCAL a custom GUC ends,
  the variable does not revert to "unset" on that session; it reverts to
  the EMPTY STRING. A bare ::uuid cast then raises `invalid input syntax
  for type uuid: ""` on every policy evaluation, which on pooled
  connections manifests as intermittent DataErrors for queries that
  should simply see zero rows. NULLIF maps '' back to NULL, restoring
  clean default-deny on both fresh and reused sessions.

The policy applies to ALL commands (SELECT/INSERT/UPDATE/DELETE):
  USING filters what is visible; WITH CHECK rejects writes whose rows do
  not match the current tenant. Cross-tenant inserts fail at the database
  no matter what application code does.
"""
from django.db import migrations

TABLE = "surveys_survey"

ENABLE_SQL = f"""
ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON {TABLE}
    USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
"""

DISABLE_SQL = f"""
DROP POLICY IF EXISTS tenant_isolation ON {TABLE};
ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("surveys", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=ENABLE_SQL, reverse_sql=DISABLE_SQL),
    ]
