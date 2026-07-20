"""
Tenant scoping for API views.

Django middleware cannot do this job: DRF authenticates tokens inside the
view layer (dispatch), after all middleware has run, so request.user is
anonymous when middleware executes. The correct integration point is
APIView.initial(), which runs after authentication and before the handler.

Load-bearing assumption: settings.ATOMIC_REQUESTS = True. set_config with
is_local=true scopes the variable to the current top-level transaction;
ATOMIC_REQUESTS guarantees exactly one such transaction wraps the entire
request, so the context set here covers the handler and dies at commit,
never leaking across pooled connections. set_tenant_context() asserts the
transaction exists so that disabling ATOMIC_REQUESTS fails loudly in tests
instead of silently no-opping isolation.

Membership resolution policy (MVP):
- exactly one membership: it is used implicitly.
- multiple memberships: client must send X-Tenant-ID, validated against
  membership. Guessing is not allowed.
- no membership: 403. Authentication without membership grants nothing.
"""
from django.db import connection
from rest_framework.exceptions import PermissionDenied
from rest_framework.views import APIView

from apps.accounts.models import TenantMembership


def set_tenant_context(tenant_id) -> None:
    """Bind RLS to `tenant_id` for the remainder of the current transaction."""
    if not connection.in_atomic_block:
        raise RuntimeError(
            "tenant context requires an open transaction "
            "(ATOMIC_REQUESTS must stay enabled, or wrap in transaction.atomic())"
        )
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_tenant', %s, true)", [str(tenant_id)])


class TenantScopedAPIView(APIView):
    """Base class for every authenticated, tenant-owned-data endpoint."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)  # runs authentication
        memberships = list(
            TenantMembership.objects.filter(user=request.user).select_related("tenant")
        )
        if not memberships:
            raise PermissionDenied("User has no tenant membership.")

        requested = request.headers.get("X-Tenant-ID")
        if requested:
            membership = next((m for m in memberships if str(m.tenant_id) == requested), None)
            if membership is None:
                raise PermissionDenied("Not a member of the requested tenant.")
        elif len(memberships) == 1:
            membership = memberships[0]
        else:
            raise PermissionDenied("Multiple memberships: supply the X-Tenant-ID header.")

        request.membership = membership
        set_tenant_context(membership.tenant_id)
