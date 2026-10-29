import os
from pathlib import Path

import pytest

from aoiro_fy12_to_fy3.verify import _TB_LINE, VerifyReport, _compare, normalize


def test_trial_balance_line_pattern():
    m = _TB_LINE.match("普通 預金 643,430 0 0 643,430 74.05")
    assert m and normalize(m["name"]) == "普通預金" and m["balance"] == "643,430"
    m = _TB_LINE.match("当期正味財産増減額 -6,098 0 0 -6,098 -0.70")
    assert m and m["balance"] == "-6,098"
    assert _TB_LINE.match("《資産の部》") is None


def test_compare():
    report = VerifyReport(term=22)
    _compare(report, "試算表", {"普通預金": 100, "現金": 0, "寄付金": 5}, {"普通預金": 100, "寄付金": 6})
    assert len(report.problems) == 1 and "寄付金" in report.problems[0]


# 本番データでの検証。リポジトリ外の設定を環境変数で指定したときだけ実行する
#   AOIRO_CONFIG=/path/to/data/org.yaml pytest tests/test_verify.py
REAL_CONFIG = os.environ.get("AOIRO_CONFIG")


@pytest.mark.skipif(not REAL_CONFIG, reason="AOIRO_CONFIG が未設定")
def test_real_data():
    pytest.importorskip("pdfplumber")
    from aoiro_fy12_to_fy3 import checks
    from aoiro_fy12_to_fy3.config import load_config
    from aoiro_fy12_to_fy3.ledger import compute_terms
    from aoiro_fy12_to_fy3.verify import verify_term

    config = load_config(Path(REAL_CONFIG))
    last = config.first_year
    while config.journal_path(last + 1).exists():
        last += 1
    results = compute_terms(config, last)
    problems = checks.check_chain(results)
    for r in results:
        problems += [f"第{r.term.number}期: {p}" for p in checks.check_term(r)]
        problems += [f"第{r.term.number}期: {p}" for p in verify_term(config, r).problems]
    assert problems == []
