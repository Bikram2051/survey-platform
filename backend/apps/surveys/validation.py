"""
Survey schema + answer validation.

The schema is versioned JSON, semantically aligned with XForm/ODK concepts
(question ids, types, required, choices, relevance) so an ODK/KoBo import
path stays open. MVP supports:

  types: text | integer | select_one | select_multiple
  relevance: {"field": "<question_id>", "eq": <value>}   (single condition)

Labels are translation maps, e.g. {"en": "...", "ne": "..."}. Every label
must at least contain the survey's default_language; respondent-facing
rendering uses default_language regardless of the author's UI language
(architecture decision: localization must not leak from admin to field).

Answer validation is deliberately strict: answering a question whose
relevance condition is false is an error, not silently dropped. Field
clients (like ODK) clear irrelevant answers client-side; the server
rejecting them keeps the contract unambiguous and makes client bugs loud.
"""
from __future__ import annotations

ALLOWED_TYPES = {"text", "integer", "select_one", "select_multiple"}


def validate_schema(schema: dict) -> list[str]:
    """Return a list of human-readable errors. Empty list = valid."""
    errors: list[str] = []
    if not isinstance(schema, dict):
        return ["schema must be an object"]

    default_language = schema.get("default_language")
    languages = schema.get("languages")
    if not isinstance(languages, list) or not languages:
        errors.append("schema.languages must be a non-empty list")
        languages = []
    if not default_language:
        errors.append("schema.default_language is required")
    elif languages and default_language not in languages:
        errors.append("schema.default_language must be one of schema.languages")

    questions = schema.get("questions")
    if not isinstance(questions, list) or not questions:
        errors.append("schema.questions must be a non-empty list")
        return errors

    seen_ids: set[str] = set()
    for i, q in enumerate(questions):
        where = f"questions[{i}]"
        if not isinstance(q, dict):
            errors.append(f"{where} must be an object")
            continue
        qid = q.get("id")
        if not qid or not isinstance(qid, str):
            errors.append(f"{where}.id is required")
            continue
        if qid in seen_ids:
            errors.append(f"duplicate question id: {qid}")
        seen_ids.add(qid)

        qtype = q.get("type")
        if qtype not in ALLOWED_TYPES:
            errors.append(f"{qid}: unknown type {qtype!r}")

        label = q.get("label")
        if not isinstance(label, dict) or (default_language and default_language not in label):
            errors.append(f"{qid}: label must include default_language '{default_language}'")

        if qtype in ("select_one", "select_multiple"):
            choices = q.get("choices")
            if not isinstance(choices, list) or not choices:
                errors.append(f"{qid}: choices required for {qtype}")
            else:
                cvals = set()
                for c in choices:
                    cv = c.get("value") if isinstance(c, dict) else None
                    if cv is None:
                        errors.append(f"{qid}: every choice needs a value")
                        continue
                    if cv in cvals:
                        errors.append(f"{qid}: duplicate choice value {cv!r}")
                    cvals.add(cv)
                    clabel = c.get("label")
                    if not isinstance(clabel, dict) or (
                        default_language and default_language not in clabel
                    ):
                        errors.append(
                            f"{qid}: choice {cv!r} label must include '{default_language}'"
                        )

        relevant = q.get("relevant")
        if relevant is not None:
            if (
                not isinstance(relevant, dict)
                or "field" not in relevant
                or "eq" not in relevant
            ):
                errors.append(f'{qid}: relevant must be {{"field": ..., "eq": ...}}')
            elif relevant["field"] not in seen_ids:
                # forward references would make relevance evaluation ambiguous
                errors.append(f"{qid}: relevant.field must reference an EARLIER question")

    return errors


def _is_relevant(question: dict, answers: dict) -> bool:
    cond = question.get("relevant")
    if cond is None:
        return True
    return answers.get(cond["field"]) == cond["eq"]


def validate_answers(schema: dict, answers: dict) -> list[str]:
    """Validate a submission's answers against a (published) schema."""
    errors: list[str] = []
    if not isinstance(answers, dict):
        return ["answers must be an object"]

    questions = {q["id"]: q for q in schema["questions"]}

    unknown = set(answers) - set(questions)
    for qid in sorted(unknown):
        errors.append(f"{qid}: unknown question")

    for qid, q in questions.items():
        relevant = _is_relevant(q, answers)
        value = answers.get(qid)

        if not relevant:
            if value is not None:
                errors.append(f"{qid}: answered but not relevant")
            continue

        if value is None:
            if q.get("required"):
                errors.append(f"{qid}: required")
            continue

        qtype = q["type"]
        if qtype == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append(f"{qid}: must be an integer")
        elif qtype == "text":
            if not isinstance(value, str):
                errors.append(f"{qid}: must be a string")
        elif qtype == "select_one":
            valid = {c["value"] for c in q["choices"]}
            if value not in valid:
                errors.append(f"{qid}: {value!r} is not a valid choice")
        elif qtype == "select_multiple":
            valid = {c["value"] for c in q["choices"]}
            if not isinstance(value, list) or not set(value) <= valid or not value:
                errors.append(f"{qid}: must be a non-empty list of valid choices")

    return errors
