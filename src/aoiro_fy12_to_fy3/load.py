"""仕訳日記帳（CSV / xls）の読み込みと、日付の読み替え。

ソフト上の年度データ N の日付は次のように読み替える（CLAUDE.md §2）。
  N/04/01〜N/12/31 → そのまま
  N/01/01〜N/03/31 → (N+1)年の同月日
  N/13（決算月）    → (N+1)/03/31。通常の仕訳の後に並べる
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

ENCODING = "cp932"


class JournalError(ValueError):
    pass


@dataclass(frozen=True)
class Side:
    code: int
    name: str
    sub: int | None
    sub_name: str
    amount: int


@dataclass(frozen=True)
class JournalLine:
    serial: int
    voucher: int
    line_no: int
    soft_date: str
    date: dt.date
    closing: bool
    kind_code: str
    kind_name: str
    debit: Side
    credit: Side
    memo: str
    sub_memo: str

    @property
    def amount(self) -> int:
        return self.debit.amount

    @property
    def sort_key(self) -> tuple:
        return (self.closing, self.date, self.voucher, self.line_no, self.serial)


_DATE_RE = re.compile(r"^(\d{4})/(\d{1,2})(?:/(\d{1,2}))?$")


def to_real_date(soft_date: str, year: int) -> tuple[dt.date, bool]:
    """ソフト上の伝票日付を実際の日付に読み替える。戻り値は (日付, 決算整理か)。"""
    m = _DATE_RE.match(soft_date.strip())
    if not m:
        raise JournalError(f"伝票日付の形式が不正です: {soft_date!r}")
    y, mo, d = int(m[1]), int(m[2]), m[3]
    if y != year:
        raise JournalError(f"{year}年度のデータに{y}年の日付があります: {soft_date!r}")
    if mo == 13:
        if d is not None:
            raise JournalError(f"決算月に日が付いています: {soft_date!r}")
        return dt.date(year + 1, 3, 31), True
    if d is None:
        raise JournalError(f"伝票日付に日がありません: {soft_date!r}")
    return dt.date(year + 1 if mo <= 3 else year, mo, int(d)), False


def _int(value: str, column: str) -> int:
    text = str(value).strip().replace(",", "")
    if text == "":
        return 0
    try:
        number = float(text)
    except ValueError:
        raise JournalError(f"{column} が数値ではありません: {value!r}") from None
    if number != int(number):
        raise JournalError(f"{column} が整数ではありません: {value!r}")
    return int(number)


def _sub(value: str) -> int | None:
    code = _int(value, "補助コード")
    return code or None


def _side(row: dict[str, str], prefix: str) -> Side:
    return Side(
        code=_int(row[f"{prefix}科目コード"], f"{prefix}科目コード"),
        # ソフトの科目名称には全角・半角の空白が入っている（例: 「普通　預金」）
        name=re.sub(r"[\s　]", "", row[f"{prefix}科目名称"]),
        sub=_sub(row[f"{prefix}補助コード"]),
        sub_name=row[f"{prefix}補助科目名称"].strip(),
        amount=_int(row[f"{prefix}金額"], f"{prefix}金額"),
    )


def parse_rows(rows: list[dict[str, str]], year: int) -> list[JournalLine]:
    lines = []
    for i, row in enumerate(rows, start=2):
        try:
            date, closing = to_real_date(row["伝票日付"], year)
            line = JournalLine(
                serial=_int(row["通番"], "通番"),
                voucher=_int(row["伝票番号"], "伝票番号"),
                line_no=_int(row["行番号"], "行番号"),
                soft_date=row["伝票日付"].strip(),
                date=date,
                closing=closing,
                kind_code=str(row["取引区分コード"]).strip(),
                kind_name=row["取引区分名称"].strip(),
                debit=_side(row, "借方"),
                credit=_side(row, "貸方"),
                memo=row["取引摘要"].strip(),
                sub_memo=row["補助摘要"].strip(),
            )
        except KeyError as e:
            raise JournalError(f"仕訳日記帳に列 {e} がありません") from None
        except JournalError as e:
            raise JournalError(f"{i}行目: {e}") from None
        if line.debit.amount != line.credit.amount:
            raise JournalError(
                f"{i}行目: 借方金額と貸方金額が一致しません"
                f"（伝票{line.voucher} {line.debit.amount:,} / {line.credit.amount:,}）"
            )
        lines.append(line)
    return lines


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding=ENCODING, newline="") as f:
        return list(csv.DictReader(f))


def _read_xls(path: Path) -> list[dict[str, str]]:
    import xlrd

    sheet = xlrd.open_workbook(str(path)).sheet_by_index(0)
    header = [str(v).strip() for v in sheet.row_values(0)]

    def text(v: object) -> str:
        if isinstance(v, float) and v == int(v):
            return str(int(v))
        return str(v)

    return [
        dict(zip(header, (text(v) for v in sheet.row_values(r))))
        for r in range(1, sheet.nrows)
    ]


def read_journal(path: str | Path, year: int) -> list[JournalLine]:
    """仕訳日記帳を読み込む。金額0円の行も含めて返す（除外は ledger 側で行う）。"""
    path = Path(path)
    if not path.exists():
        raise JournalError(f"仕訳日記帳が見つかりません: {path}")
    rows = _read_xls(path) if path.suffix.lower() == ".xls" else _read_csv(path)
    try:
        return parse_rows(rows, year)
    except JournalError as e:
        raise JournalError(f"{path.name}: {e}") from None
