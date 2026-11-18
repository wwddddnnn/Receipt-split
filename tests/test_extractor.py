from receipt_split.extractor import SYSTEM_PROMPT


def test_prompt_preserves_toronto_year_month_day_dates():
    assert "26/10/14" in SYSTEM_PROMPT
    assert "2026-10-14" in SYSTEM_PROMPT
