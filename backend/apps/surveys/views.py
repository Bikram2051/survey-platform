"""
Survey authoring API.

Notice what is ABSENT: no .filter(tenant=...) anywhere. Tenant scoping is
the database's job (RLS + TenantScopedAPIView); if these queries ever leak
across tenants, the isolation test suite, not this file, is where the bug
is. Application code states intent; the database enforces the boundary.
"""
import secrets

from django.http import Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from apps.tenants.scoping import TenantScopedAPIView

from .models import PublicLink, Survey, SurveyVersion
from .validation import validate_schema


def _version_payload(v: SurveyVersion) -> dict:
    return {
        "id": str(v.id),
        "survey": str(v.survey_id),
        "version": v.version,
        "status": v.status,
        "schema": v.schema,
        "published_at": v.published_at.isoformat() if v.published_at else None,
    }


class SurveyListCreateView(TenantScopedAPIView):
    def get(self, request):
        surveys = Survey.objects.order_by("created_at")
        return Response(
            [{"id": str(s.id), "title": s.title, "status": s.status} for s in surveys]
        )

    def post(self, request):
        title = (request.data or {}).get("title", "").strip()
        if not title:
            return Response({"errors": ["title is required"]}, status=status.HTTP_400_BAD_REQUEST)
        survey = Survey.objects.create(tenant=request.membership.tenant, title=title)
        return Response({"id": str(survey.id), "title": survey.title}, status=status.HTTP_201_CREATED)


class SurveyVersionCreateView(TenantScopedAPIView):
    """POST a draft schema. Drafts are mutable only by replacement (new draft)."""

    def post(self, request, survey_id):
        try:
            survey = Survey.objects.get(pk=survey_id)  # RLS: 404s cross-tenant
        except Survey.DoesNotExist:
            raise Http404
        schema = (request.data or {}).get("schema")
        errors = validate_schema(schema)
        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        latest = survey.versions.order_by("-version").first()
        version = SurveyVersion.objects.create(
            tenant=survey.tenant,
            survey=survey,
            version=(latest.version + 1) if latest else 1,
            schema=schema,
        )
        return Response(_version_payload(version), status=status.HTTP_201_CREATED)


class PublishVersionView(TenantScopedAPIView):
    def post(self, request, version_id):
        try:
            v = SurveyVersion.objects.get(pk=version_id)
        except SurveyVersion.DoesNotExist:
            raise Http404
        if v.status == SurveyVersion.Status.PUBLISHED:
            return Response({"errors": ["already published"]}, status=status.HTTP_409_CONFLICT)
        # Last permitted UPDATE on this row: after this the DB trigger freezes it.
        v.status = SurveyVersion.Status.PUBLISHED
        v.published_at = timezone.now()
        v.save(update_fields=["status", "published_at"])
        return Response(_version_payload(v))


class PublicLinkCreateView(TenantScopedAPIView):
    def post(self, request, version_id):
        try:
            v = SurveyVersion.objects.get(pk=version_id)
        except SurveyVersion.DoesNotExist:
            raise Http404
        if v.status != SurveyVersion.Status.PUBLISHED:
            return Response(
                {"errors": ["only published versions can have public links"]},
                status=status.HTTP_409_CONFLICT,
            )
        link = PublicLink.objects.create(
            token=secrets.token_urlsafe(12),
            tenant=request.membership.tenant,
            survey_version=v.id,
        )
        return Response(
            {"token": link.token, "url": f"/api/public/r/{link.token}/"},
            status=status.HTTP_201_CREATED,
        )
