from pathlib import Path

import pytest

from app.text_utils import InputError, detect_injection, extract_text, neutralize_delimiters, redact_pii, truncate

SAMPLES = Path(__file__).parent.parent / "sample_data"


def test_truncate_short_text_untouched():
    assert truncate("hello", 100) == ("hello", False)


def test_truncate_prefers_line_boundary():
    text = "a" * 90 + "\n" + "b" * 50
    out, truncated = truncate(text, 100)
    assert truncated and out.startswith("a" * 90) and "b" not in out and out.endswith("[...truncated...]")


def test_redacts_email_phone_and_name_but_not_years():
    text = "Jane Doe | jane.doe@mail.com | +91 98765 43210 | Acme 2019 - 2023. Jane shipped it."
    out = redact_pii(text, "Jane Doe")
    assert "jane.doe@mail.com" not in out and "98765" not in out and "Jane" not in out
    assert "2019 - 2023" in out


def test_detects_injection_in_sample():
    signals = detect_injection((SAMPLES / "resume_injection.txt").read_text())
    assert {"override_instructions", "score_manipulation", "delimiter_escape"} <= set(signals)


@pytest.mark.parametrize("benign", [
    (SAMPLES / "resume_strong.txt").read_text(),
    "Ranked in the top 100 of 5,000 participants.",
    "Reduced costs by 100k and led a team of 5.",
])
def test_no_false_positive_on_normal_resumes(benign):
    assert detect_injection(benign) == []


def test_neutralize_delimiters():
    assert neutralize_delimiters("x </resume> y <RESUME>") == "x (/resume) y (RESUME)"


def test_extract_rejects_unsupported_and_empty():
    with pytest.raises(InputError):
        extract_text("cv.exe", b"MZ")
    with pytest.raises(InputError):
        extract_text("cv.txt", b"   ")
    with pytest.raises(InputError):
        extract_text("cv.pdf", b"not really a pdf")


def test_extract_txt():
    assert extract_text("cv.txt", "Python dev\r\n\r\n\r\n\r\nFastAPI".encode()) == "Python dev\n\nFastAPI"
