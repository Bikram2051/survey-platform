from django.conf import settings
from django.db import models

from apps.tenants.models import Tenant


class TenantMembership(models.Model):
    """
    Links a platform user to a tenant with a role. Roles are RECORDED now
    and ENFORCED per-endpoint post-MVP; membership itself is the MVP
    authorization boundary (no membership = no tenant context = no data).

    Deliberately not RLS-protected: membership resolution is what CREATES
    the tenant context, so it must be readable before one exists.
    """

    class Role(models.TextChoices):
        ADMIN = "admin", "Org admin"
        AUTHOR = "author", "Survey author"
        SUPERVISOR = "supervisor", "Field supervisor"
        ENUMERATOR = "enumerator", "Enumerator"
        ANALYST = "analyst", "Analyst"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=16, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "tenant")]

    def __str__(self) -> str:
        return f"{self.user} @ {self.tenant} ({self.role})"
