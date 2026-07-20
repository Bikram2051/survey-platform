import uuid

from django.db import models

from apps.tenants.models import Tenant


class Survey(models.Model):
    """
    Minimal tenant-owned table. Its purpose in Milestone 0 is to be the
    proving ground for RLS isolation; the real versioned survey schema
    (immutable published versions, translations, branching logic) is
    Milestone 1 and will follow the same tenant_id + RLS pattern.

    Every future tenant-owned table MUST:
      1. carry a non-null tenant FK, and
      2. get the ENABLE + FORCE + policy treatment in its migration
         (see surveys/migrations/0002_enable_rls.py for the template).
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="surveys")
    title = models.CharField(max_length=500)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.title


class SurveyVersion(models.Model):
    """
    A versioned, immutable-once-published form definition.

    Immutability of published versions is enforced by a DATABASE trigger
    (migration 0004), not just application discipline: a published version
    is the contract every field submission is pinned to, so mutating it
    would silently corrupt already-collected data. The publish action
    itself (draft -> published) is the last permitted UPDATE.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="survey_versions")
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    schema = models.JSONField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("survey", "version")]

    def __str__(self) -> str:
        return f"{self.survey_id} v{self.version} ({self.status})"


class PublicLink(models.Model):
    """
    Public self-response entry point (token in a URL / QR code).

    Deliberately NOT RLS-protected, and explicitly exempted in the RLS
    guardrail test: an anonymous respondent has no tenant context, and this
    table is what BOOTSTRAPS one (token -> tenant_id -> set context -> read
    the RLS-protected schema, write the RLS-checked submission). It carries
    no response data; it is an address, not a record.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token = models.CharField(max_length=64, unique=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="public_links")
    survey_version = models.UUIDField()  # plain UUID, not FK: FK validation would need tenant context
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
