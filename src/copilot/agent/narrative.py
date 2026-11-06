"""Validate model-authored sentences against scoped, evidence-backed clauses."""
import re

from copilot.agent.models import Evidence, FinalSelection


# A sentence uses one scope. Literal text is limited to grammatical connectors.
SENTENCE = re.compile(
    r"(?:For|In|Looking at) \{\{(?P<scope>r[1-6](?:\.C[0-9]{3})?)\.scope\}\}, "
    r"(?P<clauses>\{\{(?P=scope)\.[a-z_]+\}\}"
    r"(?:(?: and |, and |, while )\{\{(?P=scope)\.[a-z_]+\}\}){0,2})\."
)
CLAUSE = re.compile(r"\{\{r[1-6](?:\.C[0-9]{3})?\.([a-z_]+)\}\}")

PROMPT = """
For a completed answer, return JSON with status, evidence_ids, narrative (an array of sentences), and optional guidance.
Write the English analysis using the narrative_slots listed in tool results. Each sentence starts with For, In,
or Looking at, followed by one scope token, a comma, and one to three clause tokens from that SAME scope.
Join clauses using ' and ', ', and ', or ', while '. End with a period. Copy token names exactly.
Example: {"status":"completed","evidence_ids":["r1"],"narrative":["Looking at {{r1.scope}}, {{r1.mean}}, while {{r1.pass_rate}}.","For {{r1.scope}}, {{r1.largest_band}}."],"guidance":["review_score_bands"]}
Choose and order the sentences to answer the question. Use only available slots and cite all evidence used.
Use per-class scopes for class statistics and the comparison scope for leaders. Never add literal numbers,
extra factual text, or causal claims. Optional guidance codes are review_score_bands, check_sample_sizes,
and review_missing_records. The server validates them and presents them separately from the facts.
For clarification or unsupported requests use empty evidence_ids and omit narrative and guidance.
"""


class InvalidNarrative(ValueError):
    """The model's prose or evidence scope could not be verified."""


def _statistics(data: dict) -> dict[str, str]:
    scope = data["scope"]
    parts = []
    for key, label in (("course_id", "course"), ("class_id", "class"), ("grade_level", "grade")):
        if scope.get(key) is not None:
            parts.append(f"{label} {scope[key]}")
    parts.append(f"term {scope['term']}")
    slots = {"scope": " in ".join(parts),
             "missing": f"{data['missing_count']} enrollment records were missing scores"}
    if not data["graded_count"]:
        slots["no_scores"] = "no graded scores were available"
        return slots
    slots.update(
        mean=f"the average score was {data['average_score']} points",
        weighted_mean=f"the credit-weighted average was {data['weighted_average_score']} points",
        pass_rate=f"{data['pass_rate_percent']}% of graded enrollments passed ({data['passed_count']} of {data['graded_count']})",
        sample=f"{data['graded_count']} enrollment records had grades",
        distribution="the score distribution was " + ", ".join(f"{b['label']}: {b['count']}" for b in data["distribution"]),
    )
    twice_passed = 2 * data["passed_count"]
    if twice_passed > data["graded_count"]:
        slots["pass_pattern"] = "more than half of graded enrollments passed"
    elif twice_passed == data["graded_count"]:
        slots["pass_pattern"] = "half of graded enrollments passed"
    else:
        slots["pass_pattern"] = "fewer than half of graded enrollments passed"
    largest = max(b["count"] for b in data["distribution"])
    bands = [b["label"] for b in data["distribution"] if b["count"] == largest]
    if len(bands) == 1:
        slots["largest_band"] = f"the most populated score band was {bands[0]} with {largest} graded records"
    else:
        slots["largest_band"] = f"{', '.join(bands)} tied for the most populated score bands with {largest} graded records each"
    return slots


def slots_for(evidence: Evidence) -> dict[str, dict[str, str]]:
    identifier, data = evidence.evidence_id, evidence.data
    if evidence.tool == "get_score_summary":
        return {identifier: _statistics(data)}
    if evidence.tool == "compare_classes":
        classes = [row["scope"]["class_id"] for row in data["results"]]
        leaders = data["highest_mean_class_ids"]
        summary = {"scope": f"the comparison of {', '.join(classes)} in course {data['course_id']} during term {data['term']}"}
        if len(leaders) == 1:
            summary["leaders"] = f"{leaders[0]} had the highest unrounded mean among the selected classes"
        elif leaders:
            summary["leaders"] = f"{', '.join(leaders)} tied for the highest unrounded mean among the selected classes"
        else:
            summary["no_scores"] = "no graded scores were available in the selected classes"
        groups = {identifier: summary}
        for class_id, row in zip(classes, data["results"]):
            groups[f"{identifier}.{class_id}"] = _statistics(row)
        return groups
    if evidence.tool == "get_dataset_overview":
        counts = data["counts"]
        return {identifier: {"scope": "the active synthetic dataset",
                             "students": f"the student population was {counts['students']}",
                             "classes": f"the dataset contained {counts['classes']} classes",
                             "courses": f"the catalog contained {counts['courses']} courses",
                             "enrollments": f"the dataset contained {counts['enrollments']} enrollments"}}
    catalog = {"scope": "the selected catalog scope",
               "courses": f"the catalog listed {len(data['courses'])} courses",
               "classes": f"the catalog listed {len(data['classes'])} classes"}
    if data["courses"]:
        catalog["course_list"] = "the available courses were " + ", ".join(
            f"{row['course_id']} ({row['course_name']})" for row in data["courses"]
        )
    if data["classes"]:
        catalog["class_list"] = "the available classes were " + ", ".join(row["class_id"] for row in data["classes"])
    return {identifier: catalog}


def available_tokens(evidence: Evidence) -> list[str]:
    return [f"{{{{{scope}.{key}}}}}" for scope, values in slots_for(evidence).items() for key in values]


def render_narrative(selection: FinalSelection, selected: list[Evidence]) -> tuple[str, list[str]]:
    groups = {scope: values for item in selected for scope, values in slots_for(item).items()}
    used, sentences = set(), []
    for sentence in selection.narrative:
        match = SENTENCE.fullmatch(sentence)
        if not match or match["scope"] not in groups:
            raise InvalidNarrative
        scope = match["scope"]
        slots = groups[scope]
        keys = CLAUSE.findall(match["clauses"])
        if len(keys) != len(set(keys)) or any(key not in slots or key == "scope" for key in keys):
            raise InvalidNarrative
        identifier = scope.split(".")[0]
        used.add(identifier)
        rendered = sentence.replace(f"{{{{{scope}.scope}}}}", slots["scope"])
        rendered = CLAUSE.sub(lambda token: slots[token[1]], rendered)
        sentences.append(f"{rendered} [{identifier}]")
    if used != set(selection.evidence_ids):
        raise InvalidNarrative

    guidance = []
    if len(selection.guidance) != len(set(selection.guidance)):
        raise InvalidNarrative
    for code in selection.guidance:
        if code == "review_score_bands" and any("distribution" in slots for slots in groups.values()):
            guidance.append("Consider reviewing the lower score bands before choosing follow-up topics.")
        elif code == "check_sample_sizes" and any(item.tool == "compare_classes" for item in selected):
            guidance.append("Review graded sample sizes before drawing conclusions about differences between classes.")
        elif code == "review_missing_records" and any("missing" in slots for slots in groups.values()):
            guidance.append("Check missing-score records before interpreting the graded results.")
        else:
            raise InvalidNarrative
    return " ".join(sentences), guidance
