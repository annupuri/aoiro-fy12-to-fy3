import pytest

from aoiro_fy12_to_fy3 import checks
from aoiro_fy12_to_fy3.config import Chart, Term
from aoiro_fy12_to_fy3.ledger import LedgerError, compute_term, compute_terms
from aoiro_fy12_to_fy3.load import read_journal

from .conftest import JOURNAL_2017, write_journal


def test_first_term_balances(config):
    (r,) = compute_terms(config, 2017)
    assert r.term.number == 22
    assert r.closing_balance(104) == 47000
    assert r.closing_balance(106) == 599573
    assert r.closing_balance(306) == 0
    assert r.closing_balance(340) == 100000
    assert r.closing_balance(401) == 490000
    assert r.closing_balance(409) == 120000
    assert r.closing_sub_balance(409, 2) == 60000
    assert r.closing_sub_balance(409, 3) == 60000
    assert r.closing_balance(500) == 40005
    assert r.closing_balance(999) == 0
    assert r.net_income == 40005 - 100000 - 432 - 3000


def test_zero_amount_lines_are_excluded(config):
    (r,) = compute_terms(config, 2017)
    assert [ln.voucher for ln in r.excluded] == [3]
    assert all(ln.amount for ln in r.lines)
    assert 722 not in r.accounts_in_use()


def test_lines_sorted_by_real_date(config):
    (r,) = compute_terms(config, 2017)
    dates = [(ln.closing, ln.date) for ln in r.lines]
    assert dates == sorted(dates)
    # ソフト上 2017/02/20・2017/03/31 の仕訳は翌年の日付になり、12月の後・決算整理の前に並ぶ
    assert [ln.voucher for ln in r.lines][-4:] == [5, 10, 7, 8]


def test_carry_forward(config):
    first, second = compute_terms(config, 2018)
    # 繰越正味財産 ＝ 前期末 ＋ 前期の当期正味財産増減額
    assert second.opening_balance(401) == 490000 + first.net_income
    assert second.opening_balance(409) == 120000
    assert second.opening_balance(106) == first.closing_balance(106)
    assert second.opening_balance(500) == 0
    assert second.closing_balance(409) == 0
    assert second.closing_balance(401) == 490000 + first.net_income + 60000
    assert checks.check_chain([first, second]) == []


def test_checks_pass(config):
    for r in compute_terms(config, 2018):
        assert checks.check_term(r) == []


def test_unknown_account(org_dir, config):
    lines = read_journal(org_dir / "仕訳帳" / "仕訳日記帳-2017.csv", 2017)
    chart = Chart({k: v for k, v in config.chart.accounts.items() if k != 784}, config.chart.section_labels)
    with pytest.raises(LedgerError, match=r"\[784\]"):
        compute_term(lines, chart, Term(2017, 22), {}, {})


def test_suspense_balance_detected(org_dir, config):
    rows = [r for r in JOURNAL_2017 if not (r[0] == 6 and r[1] == 3)]
    write_journal(org_dir / "仕訳帳" / "仕訳日記帳-2017.csv", rows)
    (r,) = compute_terms(config, 2017)
    assert any("999" in p and "0ではありません" in p for p in checks.check_term(r))


def test_negative_sub_balance_detected(org_dir, config):
    rows = JOURNAL_2017 + [(11, 1, "2017/08/01", "財務取引", (409, 2), (106, 1), 70000, "使いすぎ", "")]
    write_journal(org_dir / "仕訳帳" / "仕訳日記帳-2017.csv", rows)
    (r,) = compute_terms(config, 2017)
    assert any("マイナス" in p for p in checks.check_term(r))
