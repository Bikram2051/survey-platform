"""
Tenant isolation proof.

These tests are the acceptance criteria for Milestone 0. They exercise the
DATABASE boundary, not application filtering: if every .filter(tenant=...)
in app code were deleted, these guarantees would still hold.

Ordering note: SET LOCAL persists for the remainder of the enclosing
top-level transaction (see apps/tenants/context.py). pytest-django wraps
each test in one transaction, so within a single test, "no context"
assertions must precede any tenant_context() use. test_no_context_denies
therefore never enters a context.
"""
import pytest
from django.db import transaction
from django.db.utils import DatabaseError

from apps.surveys.models import Survey
from apps.tenants.context import tenant_context
from apps.tenants.models import Tenant


@pytest.fixture
def two_tenants(db):
    a = Tenant.objects.create(name="Tenant A (NGO)")
    b = Tenant.objects.create(name="Tenant B (Business)")
    with tenant_context(a.id):
        Survey.objects.create(tenant=a, title="A's household survey")
    with tenant_context(b.id):
        Survey.objects.create(tenant=b, title="B's customer survey")
    return a, b


@pytest.mark.django_db
def test_seeded_row_visible_inside_its_own_context(db):
    """
    Baseline sanity: a row created inside a tenant's context is visible
    inside that context. The no-context default-deny proof needs real
    transaction boundaries and lives in test_fresh_transaction_* below
    (SET LOCAL persists to end of the test's wrapping transaction, so it
    cannot be demonstrated here).
    """
    t = Tenant.objects.create(name="Lonely tenant")
    with tenant_context(t.id):
        Survey.objects.create(tenant=t, title="exists")
    with tenant_context(t.id):
        assert Survey.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_fresh_transaction_without_context_sees_nothing():
    """
    transaction=True gives real commits, so each block below runs in its own
    top-level transaction. Data committed by one tenant is invisible and the
    table is unwritable in a later transaction that sets no context.
    """
    t = Tenant.objects.create(name="Ghost tenant")
    with tenant_context(t.id):
        Survey.objects.create(tenant=t, title="committed row")

    # New top-level transaction, no context: zero rows visible.
    assert Survey.objects.count() == 0

    # And writes are rejected by WITH CHECK, regardless of app intent.
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            Survey.objects.create(tenant=t, title="should be rejected")

    # Sanity: inside the context the committed row is still there.
    with tenant_context(t.id):
        assert Survey.objects.filter(title="committed row").count() == 1


@pytest.mark.django_db
def test_tenants_see_only_their_own_rows(two_tenants):
    a, b = two_tenants

    with tenant_context(a.id):
        titles = set(Survey.objects.values_list("title", flat=True))
        assert titles == {"A's household survey"}

    with tenant_context(b.id):
        titles = set(Survey.objects.values_list("title", flat=True))
        assert titles == {"B's customer survey"}


@pytest.mark.django_db
def test_cross_tenant_insert_rejected_by_database(two_tenants):
    """
    Inside tenant A's context, inserting a row owned by tenant B must fail
    AT THE DATABASE (WITH CHECK), even though application code explicitly
    tries to do it. This is the guarantee application-level filtering can
    never give you.
    """
    a, b = two_tenants
    with tenant_context(a.id):
        with pytest.raises(DatabaseError):
            with transaction.atomic():  # savepoint to contain the abort
                Survey.objects.create(tenant=b, title="cross-tenant attack")

        # A's own view is intact after the rejected write.
        assert Survey.objects.count() == 1


@pytest.mark.django_db
def test_cross_tenant_update_and_delete_invisible(two_tenants):
    """UPDATE/DELETE against another tenant's rows silently affect zero rows."""
    a, b = two_tenants
    with tenant_context(a.id):
        assert Survey.objects.filter(title="B's customer survey").update(title="pwned") == 0
        deleted, _ = Survey.objects.filter(title="B's customer survey").delete()
        assert deleted == 0

    with tenant_context(b.id):
        assert Survey.objects.filter(title="B's customer survey").exists()


@pytest.mark.django_db
def test_rls_is_forced_despite_table_ownership():
    """
    Regression guard for the classic failure mode: app_user OWNS the tables
    (it ran the migrations), and plain ENABLE RLS exempts owners. If someone
    ever downgrades FORCE to ENABLE, isolation silently dies for the app
    role. Assert FORCE is actually on, and that the runtime role can neither
    bypass RLS nor act as superuser.
    """
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'surveys_survey'"
        )
        enabled, forced = cur.fetchone()
        assert enabled is True, "RLS not enabled on surveys_survey"
        assert forced is True, "RLS not FORCED: table owner (the app role) bypasses policies"

        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        is_super, bypasses = cur.fetchone()
        assert is_super is False, "runtime role is superuser: RLS is decorative"
        assert bypasses is False, "runtime role has BYPASSRLS: RLS is decorative"
