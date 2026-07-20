import uuid

from django.conf import settings
from django.db import models

from apps.surveys.models import SurveyVersion
from apps.tenants.models import Tenant


class Submission(models.Model):
    """
    One completed questionnaire. APPEND-ONLY: a database trigger (migration
    0002) rejects all UPDATE and DELETE. Corrections are new submissions;
    retention/erasure will later run through a privileged procedure, not
    through app-role DML. This is what makes offline sync trivially
    idempotent: rows never change, so replaying a batch is always safe.

    The primary key is CLIENT-generated for field (CAPI) submissions: the
    device mints the UUID while offline, and the id doubles as the
    idempotency key across sync retries. Public (CAWI) submissions get a
    server-minted id.

    Design metadata (stratum, cluster_id, design_weight) is captured AT
    COLLECTION TIME per the architecture: the weighting engine consumes it;
    it is never reconstructed after the fact.
    """

    class Mode(models.TextChoices):
        CAPI = "capi", "Field interview (agent)"
        CAWI = "cawi", "Online self-response"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="submissions")
    survey_version = models.ForeignKey(
        SurveyVersion, on_delete=models.PROTECT, related_name="submissions"
    )
    mode = models.CharField(max_length=8, choices=Mode.choices)
    # Architecture rule: self-selected data may never silently masquerade
    # as a population estimate. The flag is set at WRITE time and the
    # estimates endpoint branches on it.
    self_selected = models.BooleanField(default=False)
    answers = models.JSONField()
    paradata = models.JSONField(default=dict, blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)
    stratum = models.CharField(max_length=128, null=True, blank=True)
    cluster_id = models.CharField(max_length=128, null=True, blank=True)
    design_weight = models.FloatField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["survey_version", "mode"])]
