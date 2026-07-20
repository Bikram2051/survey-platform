"""
Tenant execution context.

This is the ONLY sanctioned way to run tenant-scoped queries. RLS policies
on tenant-owned tables read the Postgres session variable
`app.current_tenant`; if it is unset, `current_setting('app.current_tenant',
true)` returns NULL, every USING/WITH CHECK comparison evaluates to NULL,
and the database returns zero rows and rejects all writes. Default-deny.

Semantics to understand before touching this:

1. We use set_config(..., is_local => true), the parameterized equivalent of
   SET LOCAL. The setting lives for the remainder of the CURRENT top-level
   transaction, then vanishes. It cannot leak across pooled connections.

2. "Top-level" is the operative word. SET LOCAL is NOT savepoint-scoped:
   if you are already inside a transaction (ATOMIC_REQUESTS, or a test
   wrapped in one), entering tenant_context() sets the variable for the
   whole outer transaction, and EXITING the context manager does NOT unset
   it. Entering a second tenant_context() overwrites it. This is fine for
   the intended usage (one tenant per request/job, set once), but it means
   "no context" checks in tests must run BEFORE any context is entered.

3. transaction.atomic() here guarantees a transaction exists, so the call
   works identically under ATOMIC_REQUESTS, in Celery tasks, and in tests.
"""
from contextlib import contextmanager
from uuid import UUID

from django.db import connection, transaction


@contextmanager
def tenant_context(tenant_id: UUID | str):
    """Run the enclosed block with RLS scoped to `tenant_id`."""
    tenant_id = str(tenant_id)  # raises early on garbage input via UUID use upstream
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.current_tenant', %s, true)", [tenant_id]
            )
        yield
