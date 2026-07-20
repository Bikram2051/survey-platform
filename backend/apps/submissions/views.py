"""
Submission ingestion: the two channels, kept deliberately separate.

SubmissionSyncView is the CONTRACT the offline field app will be built
against: batch, idempotent, per-item outcomes. The client's rule is simple:
mark an item synced iff its status is "created" or "duplicate", retry the
whole batch otherwise. Replaying a batch is always safe because ids are
client-minted and rows are append-only. A resend of an existing id with
DIFFERENT content is still "duplicate": first write wins, immutably; a
correction is a new submission.

PublicRespondView is the hostile channel. It is unauthenticated, throttled,
server-mints the id, and stamps mode=cawi + self_selected=True at write
time so the estimation layer can enforce the honesty policy. Production
adds a WAF/CAPTCHA in front (architecture section 5); the tagging is the
part that must exist from day one, because it is what downstream
statistics branch on.
"""
from django.db import IntegrityError, transaction
from django.http import Http404
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.surveys.models import PublicLink, SurveyVersion
from apps.surveys.validation import validate_answers
from apps.tenants.scoping import TenantScopedAPIView, set_tenant_context

from .models import Submission


def _duration(paradata: dict) -> int | None:
    from datetime import datetime

    try:
        start = datetime.fromisoformat(paradata["started_at"])
        end = datetime.fromisoformat(paradata["ended_at"])
        return max(0, int((end - start).total_seconds()))
    except (KeyError, TypeError, ValueError):
        return None


class SubmissionSyncView(TenantScopedAPIView):
    def post(self, request):
        items = (request.data or {}).get("submissions")
        if not isinstance(items, list) or not items:
            return Response(
                {"errors": ["body must contain a non-empty 'submissions' list"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = []
        for item in items:
            item_id = item.get("id")
            outcome = {"id": item_id}
            try:
                version = SurveyVersion.objects.get(pk=item.get("survey_version"))
            except (SurveyVersion.DoesNotExist, Exception) as exc:
                if isinstance(exc, SurveyVersion.DoesNotExist) or "badly formed" in str(exc):
                    outcome.update(status="invalid", errors=["unknown survey_version"])
                    results.append(outcome)
                    continue
                raise
            if version.status != SurveyVersion.Status.PUBLISHED:
                outcome.update(status="invalid", errors=["survey_version is not published"])
                results.append(outcome)
                continue

            errors = validate_answers(version.schema, item.get("answers") or {})
            if errors:
                outcome.update(status="invalid", errors=errors)
                results.append(outcome)
                continue

            paradata = item.get("paradata") or {}
            try:
                with transaction.atomic():  # savepoint: one bad item must not poison the batch
                    Submission.objects.create(
                        id=item_id,
                        tenant=request.membership.tenant,
                        survey_version=version,
                        mode=Submission.Mode.CAPI,
                        self_selected=False,
                        answers=item["answers"],
                        paradata=paradata,
                        duration_seconds=_duration(paradata),
                        stratum=item.get("stratum"),
                        cluster_id=item.get("cluster_id"),
                        design_weight=item.get("design_weight"),
                        submitted_by=request.user,
                    )
                outcome["status"] = "created"
            except IntegrityError:
                outcome["status"] = "duplicate"
            except Exception:
                outcome.update(status="invalid", errors=["malformed submission id"])
            results.append(outcome)

        return Response({"results": results})


class PublicSurveyView(APIView):
    """GET: respondent-facing schema. POST: submit a self-response."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle]

    def _resolve(self, token: str):
        try:
            link = PublicLink.objects.get(token=token, is_active=True)
        except PublicLink.DoesNotExist:
            raise Http404
        # Bootstrap tenant context from the link, then read the RLS-protected
        # version through it. ATOMIC_REQUESTS guarantees the transaction.
        set_tenant_context(link.tenant_id)
        version = SurveyVersion.objects.get(pk=link.survey_version)
        return link, version

    def get(self, request, token):
        _, version = self._resolve(token)
        return Response(
            {
                "survey_version": str(version.id),
                "default_language": version.schema.get("default_language"),
                "languages": version.schema.get("languages"),
                "questions": version.schema.get("questions"),
            }
        )

    def post(self, request, token):
        link, version = self._resolve(token)
        answers = (request.data or {}).get("answers") or {}
        errors = validate_answers(version.schema, answers)
        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        sub = Submission.objects.create(
            tenant_id=link.tenant_id,
            survey_version=version,
            mode=Submission.Mode.CAWI,
            self_selected=True,
            answers=answers,
            paradata={"channel": "public_link"},
        )
        return Response({"id": str(sub.id)}, status=status.HTTP_201_CREATED)
