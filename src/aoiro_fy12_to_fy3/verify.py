"""ソフトの出力（合計残高試算表PDF・決算書xls）とツールの計算結果の照合（CLAUDE.md §7 の 7）。

ソフトの出力は暦年表示だが、年度データ N の決算月後の残高はそのまま第(N-1995)期末の残高になる。
"""

from __future__ import annotations

import glob
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import OrgConfig
from .ledger import TermResult
from .report_financials import software_values
from .statements import build_financials

_TB_LINE = re.compile(r"^(?P<name>.+?) (-?[\d,]+) (-?[\d,]+) (-?[\d,]+) (?P<balance>-?[\d,]+) -?[\d.]+$")


def normalize(label: str) -> str:
    return re.sub(r"[\s　]", "", label)


@dataclass
class VerifyReport:
    term: int
    sources: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def parse_trial_balance_pdf(path: Path) -> dict[str, int]:
    """合計残高試算表PDFから「科目名 → 残高」を取り出す（同じ名前は最初の行を使う）。"""
    import pdfplumber

    values: dict[str, int] = {}
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").splitlines():
                m = _TB_LINE.match(line.strip())
                if m:
                    values.setdefault(normalize(m["name"]), int(m["balance"].replace(",", "")))
    return values


def parse_software_financials_xls(path: Path) -> dict[str, int]:
    """ソフト出力の決算書xlsから「項目名 → 金額」を取り出す（同じ名前は最初の行を使う）。"""
    import xlrd

    values: dict[str, int] = {}
    wb = xlrd.open_workbook(str(path))
    for sheet in wb.sheets():
        if sheet.name == "表紙":
            continue
        for r in range(sheet.nrows):
            row = sheet.row_values(r)
            # 勘定式の貸借対照表は1行に左右2つの項目がある
            label = None
            for v in row:
                if isinstance(v, str) and normalize(v):
                    label = normalize(v)
                elif isinstance(v, float) and label is not None:
                    values.setdefault(label, int(v))
                    label = None
    return values


def _find(config: OrgConfig, key: str, result: TermResult) -> Path | None:
    pattern = config.verify.get(key)
    if not pattern:
        return None
    pattern = pattern.format(year=result.term.year, term=result.term.number, name=config.name)
    matches = sorted(glob.glob(str(config.base_dir / pattern)))
    return Path(matches[-1]) if matches else None


def _compare(report: VerifyReport, source: str, ours: dict[str, int], theirs: dict[str, int]) -> None:
    for label, value in ours.items():
        key = normalize(label)
        if key not in theirs:
            if value:
                report.problems.append(f"{source}: 「{label}」が見つかりません（ツール: {value:,}）")
            continue
        if theirs[key] != value:
            report.problems.append(
                f"{source}: {label} が一致しません（ツール: {value:,} / ソフト: {theirs[key]:,}）"
            )


def verify_term(config: OrgConfig, result: TermResult) -> VerifyReport:
    report = VerifyReport(term=result.term.number)
    chart = result.chart

    tb = _find(config, "trial_balance_pdf", result)
    if tb is None:
        report.skipped.append("合計残高試算表PDFが見つからないため照合しませんでした")
    else:
        report.sources.append(tb.name)
        ours = {
            (a.software_name or a.name): result.closing_balance(a.code)
            for a in chart.accounts.values()
            if a.category != "suspense"
        }
        fin = build_financials(result)
        ours["資産の部合計"] = fin.asset_total
        ours["負債・正味財産合計"] = fin.liabilities_and_capital_total
        ours["当期正味財産増減額"] = fin.net_income
        _compare(report, "試算表", ours, parse_trial_balance_pdf(tb))

    xls = _find(config, "software_financials_xls", result)
    if xls is None:
        report.skipped.append("ソフト出力の決算書xlsが見つからないため照合しませんでした")
    else:
        report.sources.append(xls.name)
        fin = build_financials(result)
        _compare(report, "決算書xls", software_values(fin), parse_software_financials_xls(xls))
    return report
