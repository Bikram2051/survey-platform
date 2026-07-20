"""
API integration tests: the full MVP lifecycle, cross-tenant behavior, and
the estimation honesty policy. Weighted expectations are hand-computed:

  10 clean CAPI submissions on q_age x q_vote:
    young: 6 (vote a:5, b:1)     old: 4 (vote a:1, b:3)
  Unweighted p(a) = 6/10 = 0.6
  Rake q_age to census margins young .3 / old .7 (total weight preserved=10):
    each young weight 3/6 = .5, each old 7/4 = 1.75
    weighted a = 5(.5) + 1(1.75) = 4.25  ->  p(a) = 0.425
  An 11th CAPI submission with empty answers is excluded from calibration
  (missing margin var) and counted missing_answer for estimates, changing
  neither number.
"""
import uuid

import pytest
from django.contrib.auth.models import User
from django.db.utils import DatabaseError
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.submissions.models import Submission
from apps.surveys.models import SurveyVersion
from apps.tenants.context import tenant_context
from apps.tenants.models import Tenant

SCHEMA = {
    "default_language": "ne",
    "languages": ["en", "ne"],
    "questions": [
        {"id": "q_age", "type": "select_one", "label": {"en": "Age", "ne": "उमेर"},
         "choices": [{"value": "young", "label": {"en": "18-29", "ne": "१८-२९"}},
                      {"value": "old", "label": {"en": "30+", "ne": "३०+"}}]},
        {"id": "q_vote", "type": "select_one", "label": {"en": "Vote", "ne": "मत"},
         "choices": [{"value": "a", "label": {"en": "A", "ne": "क"}},
                      {"value": "b", "label": {"en": "B", "ne": "ख"}}]},
    ],
}


def make_client(username: str, tenant: Tenant, role="author"):
    from apps.accounts.models import TenantMembership

    user = User.objects.create_user(username=username, password="x")
    TenantMembership.objects.create(user=user, tenant=tenant, role=role)
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


@pytest.fixture
def env(db):
    ta = Tenant.objects.create(name="NGO Alpha")
    tb = Tenant.objects.create(name="Business Beta")
    return {
        "ta": ta, "tb": tb,
        "a": make_client("alice", ta),
        "b": make_client("bob", tb),
        "anon": APIClient(),
    }


def publish_survey(client) -> tuple[str, str]:
    sid = client.post("/api/surveys/", {"title": "Perception wave 1"}, format="json").data["id"]
    v = client.post(f"/api/surveys/{sid}/versions/", {"schema": SCHEMA}, format="json")
    assert v.status_code == 201, v.data
    vid = v.data["id"]
    assert client.post(f"/api/versions/{vid}/publish").status_code == 200
    return sid, vid


# ---------- auth + scoping ----------

def test_unauthenticated_and_membershipless_denied(env, db):
    assert APIClient().get("/api/surveys/").status_code == 401
    user = User.objects.create_user(username="nobody", password="x")
    t = Token.objects.create(user=user)
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Token {t.key}")
    assert c.get("/api/surveys/").status_code == 403  # authenticated but no membership


def test_api_isolation_between_tenants(env):
    env["a"].post("/api/surveys/", {"title": "A survey"}, format="json")
    r_b = env["b"].get("/api/surveys/")
    assert r_b.data == []
    r_a = env["a"].get("/api/surveys/")
    assert [s["title"] for s in r_a.data] == ["A survey"]


# ---------- versioning + immutability ----------

def test_invalid_schema_rejected(env):
    sid = env["a"].post("/api/surveys/", {"title": "X"}, format="json").data["id"]
    r = env["a"].post(f"/api/surveys/{sid}/versions/", {"schema": {"questions": []}}, format="json")
    assert r.status_code == 400
    assert r.data["errors"]


def test_publish_freezes_version_at_the_database(env):
    _, vid = publish_survey(env["a"])
    # second publish is a 409, not a trigger error
    assert env["a"].post(f"/api/versions/{vid}/publish").status_code == 409
    # direct ORM mutation of a published version: the DATABASE refuses
    with tenant_context(env["ta"].id):
        v = SurveyVersion.objects.get(pk=vid)
        v.schema = {"tampered": True}
        with pytest.raises(DatabaseError):
            v.save(update_fields=["schema"])


def test_cross_tenant_version_publish_404s(env):
    _, vid = publish_survey(env["a"])
    sid_b = env["b"].post("/api/surveys/", {"title": "B"}, format="json").data["id"]
    vb = env["b"].post(f"/api/surveys/{sid_b}/versions/", {"schema": SCHEMA}, format="json").data["id"]
    # B cannot even see A's version, RLS turns it into a 404
    assert env["b"].post(f"/api/versions/{vid}/publish").status_code == 404
    assert env["a"].post(f"/api/versions/{vb}/publish").status_code == 404


# ---------- sync ----------

def sync_item(vid, answers, **extra):
    return {"id": str(uuid.uuid4()), "survey_version": vid, "answers": answers,
            "paradata": {"started_at": "2026-07-04T09:00:00", "ended_at": "2026-07-04T09:07:30"},
            **extra}


def test_sync_batch_per_item_outcomes_and_idempotency(env):
    _, vid = publish_survey(env["a"])
    good = sync_item(vid, {"q_age": "young", "q_vote": "a"}, stratum="p1_urban",
                     cluster_id="psu_07", design_weight=240.5)
    bad_answers = sync_item(vid, {"q_age": "child"})
    bad_version = sync_item(str(uuid.uuid4()), {"q_age": "young"})

    r = env["a"].post("/api/sync/submissions",
                      {"submissions": [good, bad_answers, bad_version]}, format="json")
    statuses = {i["id"]: i["status"] for i in r.data["results"]}
    assert statuses[good["id"]] == "created"
    assert statuses[bad_answers["id"]] == "invalid"
    assert statuses[bad_version["id"]] == "invalid"

    # full replay: safe, the created row reports duplicate, nothing new is written
    r2 = env["a"].post("/api/sync/submissions",
                       {"submissions": [good, bad_answers, bad_version]}, format="json")
    statuses2 = {i["id"]: i["status"] for i in r2.data["results"]}
    assert statuses2[good["id"]] == "duplicate"

    with tenant_context(env["ta"].id):
        sub = Submission.objects.get(pk=good["id"])
        assert (sub.mode, sub.self_selected) == ("capi", False)
        assert sub.design_weight == 240.5 and sub.stratum == "p1_urban"
        assert sub.duration_seconds == 450  # computed from paradata


# ---------- public channel ----------

def test_public_flow_tags_self_selected(env):
    _, vid = publish_survey(env["a"])
    link = env["a"].post(f"/api/versions/{vid}/links").data
    anon = env["anon"]

    schema_resp = anon.get(f"/api/public/r/{link['token']}/")
    assert schema_resp.status_code == 200
    assert schema_resp.data["default_language"] == "ne"

    ok = anon.post(f"/api/public/r/{link['token']}/",
                   {"answers": {"q_age": "old", "q_vote": "a"}}, format="json")
    assert ok.status_code == 201
    bad = anon.post(f"/api/public/r/{link['token']}/",
                    {"answers": {"q_age": "martian"}}, format="json")
    assert bad.status_code == 400
    assert anon.get("/api/public/r/not-a-real-token/").status_code == 404

    with tenant_context(env["ta"].id):
        sub = Submission.objects.get(pk=ok.data["id"])
        assert (sub.mode, sub.self_selected) == ("cawi", True)


def test_public_link_requires_published_version(env):
    sid = env["a"].post("/api/surveys/", {"title": "Draft only"}, format="json").data["id"]
    vid = env["a"].post(f"/api/surveys/{sid}/versions/", {"schema": SCHEMA}, format="json").data["id"]
    assert env["a"].post(f"/api/versions/{vid}/links").status_code == 409


# ---------- calibration + estimates (the differentiator) ----------

def seed_capi(client, vid, age, vote, n):
    items = [sync_item(vid, {"q_age": age, **({"q_vote": vote} if vote else {})}) for _ in range(n)]
    r = client.post("/api/sync/submissions", {"submissions": items}, format="json")
    assert all(i["status"] == "created" for i in r.data["results"]), r.data


def test_calibration_and_honest_estimates(env):
    sid, vid = publish_survey(env["a"])
    a = env["a"]
    seed_capi(a, vid, "young", "a", 5)
    seed_capi(a, vid, "young", "b", 1)
    seed_capi(a, vid, "old", "a", 1)
    seed_capi(a, vid, "old", "b", 3)
    # 11th: empty answers, excluded from calibration, missing for estimates
    r = a.post("/api/sync/submissions",
               {"submissions": [sync_item(vid, {})]}, format="json")
    assert r.data["results"][0]["status"] == "created"

    # unweighted baseline
    est = a.get(f"/api/surveys/{sid}/estimates?question=q_vote").data
    assert est["estimates"]["a"]["proportion"] == pytest.approx(0.6)
    assert est["flags"]["weighted"] is False
    assert est["flags"]["missing_answer_n"] == 1
    assert est["se_method"] == "kish_neff_approx"

    # calibrate to census margins
    cal = a.post(f"/api/surveys/{sid}/calibrate",
                 {"name": "census", "margins": {"q_age": {"young": 0.3, "old": 0.7}}},
                 format="json")
    assert cal.status_code == 201, cal.data
    d = cal.data["diagnostics"]
    assert d["converged"] and d["excluded_missing_margin_answers"] == 1

    est_w = a.get(f"/api/surveys/{sid}/estimates?question=q_vote&scheme=census").data
    assert est_w["estimates"]["a"]["proportion"] == pytest.approx(0.425)
    assert est_w["flags"]["weighted"] is True
    assert est_w["flags"]["weight_source"] == "census"
    # calibrated margins are recovered on the margin variable itself
    est_age = a.get(f"/api/surveys/{sid}/estimates?question=q_age&scheme=census").data
    assert est_age["estimates"]["young"]["proportion"] == pytest.approx(0.3, abs=1e-4)

    # self-selected data: excluded by default, warning when forced in
    link = a.post(f"/api/versions/{vid}/links").data
    for _ in range(3):
        env["anon"].post(f"/api/public/r/{link['token']}/",
                         {"answers": {"q_age": "young", "q_vote": "a"}}, format="json")
    est_default = a.get(f"/api/surveys/{sid}/estimates?question=q_vote").data
    assert est_default["estimates"]["a"]["proportion"] == pytest.approx(0.6)  # unchanged
    assert est_default["flags"]["includes_self_selected"] is False

    est_ss = a.get(f"/api/surveys/{sid}/estimates?question=q_vote&include_self_selected=1").data
    assert est_ss["flags"]["non_representative_warning"] is True
    assert est_ss["estimates"]["a"]["proportion"] == pytest.approx(9 / 13)  # 6a+3ss_a of 13 answered

    # forcing self-selected into a calibrated estimate cannot fake weights:
    est_ss_w = a.get(
        f"/api/surveys/{sid}/estimates?question=q_vote&scheme=census&include_self_selected=1"
    ).data
    assert est_ss_w["flags"]["not_in_scheme_n"] == 3
    assert est_ss_w["estimates"]["a"]["proportion"] == pytest.approx(0.425)
