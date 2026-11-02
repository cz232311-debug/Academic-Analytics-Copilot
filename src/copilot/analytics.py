"""Deterministic Pandas metrics; denominator, scope and exact sums stay visible."""
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction

import pandas as pd

from copilot.models import DataError
from copilot.schemas import ClassComparison, Scope, ScoreBin, Statistics
from copilot.storage import Database

BINS = (("[0,60)", 0, 6000, False), ("[60,70)", 6000, 7000, False),
        ("[70,80)", 7000, 8000, False), ("[80,90)", 8000, 9000, False), ("[90,100]", 9000, 10000, True))


def ratio(numerator: int, denominator: int) -> Decimal | None:
    return ((Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if denominator else None)


def summarize(frame: pd.DataFrame, version: str, scope: Scope) -> Statistics:
    graded = frame.loc[frame["status"] == "graded"]
    scores = graded["score_cents"]
    failed = graded.loc[scores < 6000]
    count = len(graded)
    score_sum = int(scores.sum())
    weighted_sum = int((scores * graded["credits"]).sum())
    credits = int(graded["credits"].sum())
    passed = int((scores >= 6000).sum())
    distribution = [ScoreBin(label=label, lower_cents=low, upper_cents=high, upper_inclusive=inclusive,
                             count=int(((scores >= low) & ((scores <= high) if inclusive else (scores < high))).sum()))
                    for label, low, high, inclusive in BINS]
    return Statistics(dataset_version=version, scope=scope, enrollment_count=len(frame),
                      student_count=int(frame["student_id"].nunique()), graded_count=count,
                      missing_count=int((frame["status"] == "missing").sum()),
                      exempt_count=int((frame["status"] == "exempt").sum()), passed_count=passed,
                      failed_count=len(failed), failed_student_count=int(failed["student_id"].nunique()),
                      sum_score_cents=score_sum, weighted_sum_score_cents=weighted_sum, graded_credits=credits,
                      average_score=ratio(score_sum, count * 100), weighted_average_score=ratio(weighted_sum, credits * 100),
                      pass_rate_percent=ratio(passed * 100, count), distribution=distribution)


class AnalyticsEngine:
    def __init__(self, database: Database):
        self.database = database

    def summary(self, scope: Scope) -> Statistics:
        meta, frame = self.database.snapshot(scope)
        return summarize(frame, meta["dataset_version"], scope)

    def compare_classes(self, course_id: str, class_ids: list[str], term="2026-T1"):
        if not 2 <= len(class_ids) <= 40 or len(set(class_ids)) != len(class_ids):
            raise DataError("Choose between 2 and 40 distinct classes")
        scope = Scope(course_id=course_id, term=term)
        # One read transaction/frame gives every class the same dataset version.
        meta, frame = self.database.snapshot(scope, class_ids=class_ids)
        results = [summarize(frame.loc[frame["class_id"] == cid], meta["dataset_version"], Scope(class_id=cid, **scope.model_dump(exclude_none=True)))
                   for cid in sorted(class_ids)]
        means = {r.scope.class_id: Fraction(r.sum_score_cents, r.graded_count) for r in results if r.graded_count}
        highest = [cid for cid in sorted(means) if means[cid] == max(means.values())]
        return ClassComparison(dataset_version=meta["dataset_version"], course_id=course_id, term=term,
                               results=results, highest_mean_class_ids=highest)
