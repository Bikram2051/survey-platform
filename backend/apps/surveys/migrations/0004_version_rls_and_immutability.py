"""
RLS on surveys_surveyversion (same template + rationale as 0002) plus a
trigger enforcing published-version immutability at the database.

Why a trigger and not model code: submissions are pinned to a version id.
If a published schema could mutate, every already-collected answer set
would silently change meaning. Application discipline is one bug away from
that; the database refusing is not. UPDATE is allowed only while status is
'draft' (which is how publishing itself happens); once 'published', both
UPDATE and DELETE raise.

NOTE: surveys_publiclink intentionally gets NO RLS here. See the model
docstring; it bootstraps tenant context for anonymous respondents and is
explicitly exempted (with justification) in tests/test_rls_guardrail.py.
"""
from django.db import migrations

SQL = """
ALTER TABLE surveys_surveyversion ENABLE ROW LEVEL SECURITY;
ALTER TABLE surveys_surveyversion FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON surveys_surveyversion
    USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);

CREATE FUNCTION forbid_published_version_mutation() RETURNS trigger AS $$
BEGIN
    IF OLD.status = 'published' THEN
        RAISE EXCEPTION 'published survey versions are immutable (version %)', OLD.id;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER surveyversion_immutability
    BEFORE UPDATE OR DELETE ON surveys_surveyversion
    FOR EACH ROW EXECUTE FUNCTION forbid_published_version_mutation();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS surveyversion_immutability ON surveys_surveyversion;
DROP FUNCTION IF EXISTS forbid_published_version_mutation();
DROP POLICY IF EXISTS tenant_isolation ON surveys_surveyversion;
ALTER TABLE surveys_surveyversion NO FORCE ROW LEVEL SECURITY;
ALTER TABLE surveys_surveyversion DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("surveys", "0003_publiclink_surveyversion")]
    operations = [migrations.RunSQL(sql=SQL, reverse_sql=REVERSE_SQL)]
