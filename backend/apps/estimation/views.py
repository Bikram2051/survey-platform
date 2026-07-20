"""
Calibration + estimates API.

Honesty policy, enforced in code, not in documentation:
- Self-selected (public CAWI) responses are EXCLUDED from estimates by
  default. Including them requires include_self_selected=1 and the
  response carries non_representative_warning=True.
- Every estimate response states whether it is weighted, which weights it
  used, and the SE method. There is no code path that returns a bare
  percentage stripped of its epistemic status.
"""
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response

from apps.submissions.models import Submission
from apps.surveys.models import Survey, SurveyVersion
from apps.tenants.scoping import TenantScopedAPIView

from .engine import RakingError, rake, weighted_proportions
from .models import CalibrationScheme, CalibrationWeight


def _resolve_version(survey: Survey, version_id: str | None) -> SurveyVersion:
    qs = survey.versions.filter(status=SurveyVersion.Status.PUBLISHED)
    if version_id:
        v = qs.filter(pk=version_id).first()
    else:
        v = qs.order_by("-version").first()
    if v is None:
        raise Http404("no published version")
    return v


class CalibrateView(TenantScopedAPIView):
    """
    POST {"name": ..., "margins": {"q_x": {"cat": share, ...}, ...},
          "version": optional version id}
    Rakes CAPI submissions of that version to the margins; stores a scheme.
    Submissions missing an answer to any margin variable cannot be placed
    in a raking cell and are excluded (counted in diagnostics), never
    silently weighted.
    """

    def post(self, request, survey_id):
        try:
            survey = Survey.objects.get(pk=survey_id)
        except Survey.DoesNotExist:
            raise Http404
        body = request.data or {}
        name, margins = body.get("name"), body.get("margins")
        if not name or not isinstance(margins, dict) or not margins:
            return Response(
                {"errors": ["'name' and non-empty 'margins' are required"]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        version = _resolve_version(survey, body.get("version"))
        if CalibrationScheme.objects.filter(survey_version=version, name=name).exists():
            return Response({"errors": ["scheme name already exists for this version"]},
                            status=status.HTTP_409_CONFLICT)

        subs = list(
            Submission.objects.filter(survey_version=version, self_selected=False)
            .values("id", "answers", "design_weight")
        )
        rows, start_w, included, excluded = [], [], [], 0
        for s in subs:
            cats = {}
            ok = True
            for var in margins:
                val = s["answers"].get(var)
                if val is None:
                    ok = False
                    break
                cats[var] = val
            if not ok:
                excluded += 1
                continue
            rows.append(cats)
            start_w.append(s["design_weight"] or 1.0)
            included.append(s["id"])

        try:
            weights, diagnostics = rake(rows, margins, start_w)
        except RakingError as exc:
            return Response({"errors": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)

        diagnostics["excluded_missing_margin_answers"] = excluded
        scheme = CalibrationScheme.objects.create(
            tenant=request.membership.tenant,
            survey=survey,
            survey_version=version,
            name=name,
            margins=margins,
            diagnostics=diagnostics,
        )
        CalibrationWeight.objects.bulk_create(
            CalibrationWeight(
                scheme=scheme,
                submission_id=sid,
                tenant=request.membership.tenant,
                weight=w,
            )
            for sid, w in zip(included, weights)
        )
        return Response(
            {"scheme": str(scheme.id), "name": name, "diagnostics": diagnostics},
            status=status.HTTP_201_CREATED,
        )


class EstimatesView(TenantScopedAPIView):
    """
    GET ?question=q_x [&scheme=name] [&version=id] [&include_self_selected=1]
    """

    def get(self, request, survey_id):
        try:
            survey = Survey.objects.get(pk=survey_id)
        except Survey.DoesNotExist:
            raise Http404
        question = request.query_params.get("question")
        if not question:
            return Response({"errors": ["'question' is required"]}, status=status.HTTP_400_BAD_REQUEST)
        version = _resolve_version(survey, request.query_params.get("version"))

        qmeta = next((q for q in version.schema["questions"] if q["id"] == question), None)
        if qmeta is None:
            return Response({"errors": ["unknown question"]}, status=status.HTTP_400_BAD_REQUEST)
        if qmeta["type"] != "select_one":
            return Response(
                {"errors": ["MVP estimates support select_one questions"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        include_ss = request.query_params.get("include_self_selected") == "1"
        subs = Submission.objects.filter(survey_version=version)
        if not include_ss:
            subs = subs.filter(self_selected=False)
        subs = list(subs.values("id", "answers", "design_weight", "self_selected"))

        scheme_name = request.query_params.get("scheme")
        weight_map: dict = {}
        if scheme_name:
            scheme = CalibrationScheme.objects.filter(
                survey_version=version, name=scheme_name
            ).first()
            if scheme is None:
                return Response({"errors": ["unknown scheme"]}, status=status.HTTP_400_BAD_REQUEST)
            weight_map = dict(scheme.weights.values_list("submission_id", "weight"))

        values, weights = [], []
        missing_answer = 0
        no_scheme_weight = 0
        any_design_weight = False
        for s in subs:
            v = s["answers"].get(question)
            if v is None:
                missing_answer += 1
                continue
            if scheme_name:
                w = weight_map.get(s["id"])
                if w is None:
                    # not calibrated (self-selected, or excluded at calibration):
                    # excluding beats silently assigning a fake weight
                    no_scheme_weight += 1
                    continue
            else:
                w = s["design_weight"] or 1.0
                any_design_weight = any_design_weight or s["design_weight"] is not None
            values.append(v)
            weights.append(w)

        result = weighted_proportions(values, weights)
        result["question"] = question
        result["version"] = str(version.id)
        result["flags"] = {
            "weighted": bool(scheme_name) or any_design_weight,
            "weight_source": scheme_name or ("design_weight" if any_design_weight else "none"),
            "includes_self_selected": include_ss,
            "non_representative_warning": include_ss,
            "missing_answer_n": missing_answer,
            "not_in_scheme_n": no_scheme_weight,
        }
        return Response(result)
