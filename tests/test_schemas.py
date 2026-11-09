import pytest
from pydantic import ValidationError

from app.schemas import MatchReport
from tests.conftest import valid_report


@pytest.mark.parametrize("raw, expected", [(82, 82), ("82", 82), (82.0, 82), ("82/100", 82), ("82%", 82), (81.6, 82)])
def test_score_is_coerced(raw, expected):
    assert MatchReport.model_validate(valid_report(score=raw)).score == expected


@pytest.mark.parametrize("raw", [-1, 101, "high", None, [80]])
def test_invalid_score_rejected(raw):
    with pytest.raises(ValidationError):
        MatchReport.model_validate(valid_report(score=raw))


def test_requires_exactly_three_questions():
    with pytest.raises(ValidationError):
        MatchReport.model_validate(valid_report(interview_questions=["only one?"]))


def test_rejects_too_many_skills_and_gaps():
    six = [{"skill": f"S{i}", "evidence": "e"} for i in range(6)]
    with pytest.raises(ValidationError):
        MatchReport.model_validate(valid_report(matching_skills=six))
    with pytest.raises(ValidationError):
        MatchReport.model_validate(valid_report(gaps=["a", "b", "c", "d"]))


def test_duplicate_skills_are_collapsed():
    skills = [{"skill": "Python", "evidence": "a"}, {"skill": "python ", "evidence": "b"}]
    assert len(MatchReport.model_validate(valid_report(matching_skills=skills)).matching_skills) == 1


def test_missing_field_rejected():
    data = valid_report()
    del data["gaps"]
    with pytest.raises(ValidationError):
        MatchReport.model_validate(data)
