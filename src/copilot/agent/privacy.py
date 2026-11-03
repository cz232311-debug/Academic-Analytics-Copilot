"""Local English domain gate: never forward unrestricted questions or student IDs."""
import re

from copilot.agent.models import AgentError

WORDS = set("""a an the and or of for in on by to from with without at as is are was were be do does did
what which how many much who can could would will please tell show give me us get find list available
dataset overview catalog count counts total students student classes class courses course grades grade
term academic final score scores mark marks average averages mean means median overall weighted credit
credits pass passing passed fail failed failing failure failures rate rates percentage percent distribution
missing exempt records record enrolled enrollment enrollments compare comparison between against across
highest lowest best top performance summary summarize explain all each distinct number numbers data
has have this that these those it its same results result statistics statistical calculate compute report
mathematics programming statistics english physics ethics literacy databases economics chemistry biology
history geography art music design robotics networks accounting business psychology sociology literature
astronomy environmental science public speaking project management algorithms digital media entrepreneurship
research methods academic writing data""".split())
WORDS.update("synthetic about provide breakdown table enrolment enrolments percentages show comparing summaries".split())


def canonical_question(question: str, catalog) -> str:
    # Deliberately restricted rather than claiming a general-purpose PII detector.
    if not question.isascii() or re.search(r"[^A-Za-z0-9\s.,?'%/-]", question):
        raise AgentError("question_privacy_block", "Use an English aggregate question with course/class IDs; omit personal information or code", 422)
    text = re.sub(r"\bwhat'?s\b", "what is", question.lower())
    tokens = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text)
    allowed = WORDS | {word for course in catalog.courses for word in re.findall(r"[a-z]+", course.course_name.lower())}
    for token in tokens:
        if (token not in allowed and token not in {"1", "2", "3"}
                and not re.fullmatch(r"(?:g[1-3]-c[0-9]{2}|c[0-9]{3}|20[0-9]{2}-t[1-3])", token)):
            raise AgentError("question_privacy_block", "Question contains unsupported vocabulary or identifiers; use only aggregate academic queries", 422)
    if not tokens:
        raise AgentError("question_privacy_block", "An aggregate academic question is required", 422)
    return " ".join(tokens)
