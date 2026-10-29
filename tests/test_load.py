import datetime as dt

import pytest

from aoiro_fy12_to_fy3.load import JournalError, read_journal, to_real_date


@pytest.mark.parametrize(
    ("soft", "expected", "closing"),
    [
        ("2017/04/01", dt.date(2017, 4, 1), False),
        ("2017/12/31", dt.date(2017, 12, 31), False),
        ("2017/01/01", dt.date(2018, 1, 1), False),
        ("2017/03/31", dt.date(2018, 3, 31), False),
        ("2017/13", dt.date(2018, 3, 31), True),
    ],
)
def test_to_real_date(soft, expected, closing):
    assert to_real_date(soft, 2017) == (expected, closing)


@pytest.mark.parametrize("soft", ["2018/05/01", "2017/13/31", "2017/05", "2017-05-01"])
def test_to_real_date_rejects(soft):
    with pytest.raises(JournalError):
        to_real_date(soft, 2017)


def test_read_journal(org_dir):
    lines = read_journal(org_dir / "仕訳帳" / "仕訳日記帳-2017.csv", 2017)
    assert len(lines) == 12
    first = lines[0]
    assert first.debit.code == 106 and first.debit.name == "普通預金"
    assert first.debit.sub == 1 and first.debit.sub_name == "架空銀行"
    assert first.credit.code == 500 and first.amount == 30000
    assert first.sub_memo == "会員Ａ"
    # 補助コード 0 は補助なし
    assert lines[1].debit.sub is None


def test_read_journal_wrong_year(org_dir):
    with pytest.raises(JournalError, match="2018年度のデータに2017年"):
        read_journal(org_dir / "仕訳帳" / "仕訳日記帳-2017.csv", 2018)
