import uuid

from django.db import models

from apps.submissions.models import Submission
from apps.surveys.models import Survey, SurveyVersion
from apps.tenants.models import Tenant


class CalibrationScheme(models.Model):
    """
    One raking run: the population margins used, and diagnostics
    (convergence, weight distribution). Submissions are immutable, so
    calibration weights live here, in versioned side tables, never on the
    submission row. Re-calibrating with new margins is a new scheme; old
    estimates stay reproducible.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT)
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="calibration_schemes")
    survey_version = models.ForeignKey(SurveyVersion, on_delete=models.PROTECT)
    name = models.CharField(max_length=255)
    margins = models.JSONField()
    diagnostics = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("survey_version", "name")]


class CalibrationWeight(models.Model):
    scheme = models.ForeignKey(CalibrationScheme, on_delete=models.CASCADE, related_name="weights")
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="calibration_weights")
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT)
    weight = models.FloatField()

    class Meta:
        unique_together = [("scheme", "submission")]
