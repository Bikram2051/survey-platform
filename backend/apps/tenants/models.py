import uuid

from django.db import models


class Tenant(models.Model):
    """
    Platform-level tenant registry.

    Deliberately NOT under row-level security: the application must be able
    to resolve "which tenant does this authenticated user belong to" BEFORE
    a tenant context exists. Everything tenant-owned (surveys, responses,
    media) is RLS-protected; this table is the platform's own record.

    `tier` mirrors the architecture decision: pooled tenants share the
    RLS-enforced database; siloed tenants (government, political parties)
    get a dedicated data plane. Milestone 0 implements the pooled tier and
    records the tier so routing logic can branch on it later without a
    schema change.
    """

    class Tier(models.TextChoices):
        POOLED = "pooled", "Pooled (shared DB, RLS-isolated)"
        SILOED = "siloed", "Siloed (dedicated data plane)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    tier = models.CharField(max_length=16, choices=Tier.choices, default=Tier.POOLED)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.tier})"
