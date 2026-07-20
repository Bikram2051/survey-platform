"""
RLS on submissions (template from surveys/0002) plus the append-only
trigger: UPDATE and DELETE always raise. Sync idempotency, the audit
posture, and the anti-fabrication story all rest on rows never changing.
Retention/erasure (architecture section 8) will be a privileged database
procedure, deliberately out of reach of the app role.
"""
from django.db import migrations

SQL = """
ALTER TABLE submissions_submission ENABLE ROW LEVEL SECURITY;
ALTER TABLE submissions_submission FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON submissions_submission
    USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);

CREATE FUNCTION forbid_submission_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'submissions are append-only (attempted % on %)', TG_OP, OLD.id;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER submission_append_only
    BEFORE UPDATE OR DELETE ON submissions_submission
    FOR EACH ROW EXECUTE FUNCTION forbid_submission_mutation();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS submission_append_only ON submissions_submission;
DROP FUNCTION IF EXISTS forbid_submission_mutation();
DROP POLICY IF EXISTS tenant_isolation ON submissions_submission;
ALTER TABLE submissions_submission NO FORCE ROW LEVEL SECURITY;
ALTER TABLE submissions_submission DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("submissions", "0001_initial")]
    operations = [migrations.RunSQL(sql=SQL, reverse_sql=REVERSE_SQL)]
