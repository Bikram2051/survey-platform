"""RLS on both estimation tables. Template from surveys/0002."""
from django.db import migrations


def _rls(table: str) -> str:
    return f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON {table}
    USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
"""


def _drop(table: str) -> str:
    return f"""
DROP POLICY IF EXISTS tenant_isolation ON {table};
ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
"""


TABLES = ["estimation_calibrationscheme", "estimation_calibrationweight"]


class Migration(migrations.Migration):
    dependencies = [("estimation", "0002_initial")]
    operations = [
        migrations.RunSQL(sql=_rls(t), reverse_sql=_drop(t)) for t in TABLES
    ]
