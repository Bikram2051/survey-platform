from django.http import JsonResponse
from django.urls import path
from rest_framework.authtoken.views import obtain_auth_token

from apps.estimation.views import CalibrateView, EstimatesView
from apps.submissions.views import PublicSurveyView, SubmissionSyncView
from apps.surveys.views import (
    PublicLinkCreateView,
    PublishVersionView,
    SurveyListCreateView,
    SurveyVersionCreateView,
)


def healthz(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz", healthz),
    path("api/auth/token", obtain_auth_token),
    path("api/surveys/", SurveyListCreateView.as_view()),
    path("api/surveys/<uuid:survey_id>/versions/", SurveyVersionCreateView.as_view()),
    path("api/surveys/<uuid:survey_id>/calibrate", CalibrateView.as_view()),
    path("api/surveys/<uuid:survey_id>/estimates", EstimatesView.as_view()),
    path("api/versions/<uuid:version_id>/publish", PublishVersionView.as_view()),
    path("api/versions/<uuid:version_id>/links", PublicLinkCreateView.as_view()),
    path("api/sync/submissions", SubmissionSyncView.as_view()),
    path("api/public/r/<str:token>/", PublicSurveyView.as_view()),
]
