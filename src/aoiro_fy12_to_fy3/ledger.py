"""元帳・残高の計算と、期首残高の繰越。

残高は内部では「借方を正」とした符号付き整数で持ち、表示するときに科目の通常の向きに直す。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .config import BALANCE_SHEET, PROFIT_AND_LOSS, Chart, OrgConfig, Term
from .load import JournalLine, read_journal

SubKey = tuple[int, int | None]


class LedgerError(ValueError):
    pass


@dataclass
class TermResult:
    term: Term
    chart: Chart
    lines: list[JournalLine]
    # 金額0円で元帳から除外した行（CLAUDE.md §3.1）
    excluded: list[JournalLine]
    opening: dict[int, int]
    closing: dict[int, int]
    # 補助科目別の残高。期首残高を補助科目別に与えた科目だけを持つ
    opening_subs: dict[SubKey, int]
    closing_subs: dict[SubKey, int]
    debit_total: dict[int, int] = field(default_factory=dict)
    credit_total: dict[int, int] = field(default_factory=dict)

    def normal(self, code: int, signed: int) -> int:
        return signed if self.chart[code].debit_normal else -signed

    def opening_balance(self, code: int) -> int:
        return self.normal(code, self.opening.get(code, 0))

    def closing_balance(self, code: int) -> int:
        return self.normal(code, self.closing.get(code, 0))

    def closing_sub_balance(self, code: int, sub: int | None) -> int:
        return self.normal(code, self.closing_subs.get((code, sub), 0))

    def category_total(self, category: str) -> int:
        return sum(self.closing_balance(a.code) for a in self.chart.by_category(category))

    @property
    def net_income(self) -> int:
        """当期正味財産増減額 ＝ 収益 − 費用。"""
        return (
            self.category_total("revenue")
            - self.category_total("cost")
            - self.category_total("expense")
        )

    def reserve_transfers(self) -> dict[int | None, int]:
        """繰越正味財産と任意積立金の間の振替額（補助科目別。積立が正、取崩しが負）。"""
        retained, reserve = self.chart.retained.code, self.chart.reserve.code
        out: dict[int | None, int] = defaultdict(int)
        for line in self.lines:
            if line.debit.code == retained and line.credit.code == reserve:
                out[line.credit.sub] += line.amount
            elif line.debit.code == reserve and line.credit.code == retained:
                out[line.debit.sub] -= line.amount
        return dict(out)

    def accounts_in_use(self) -> list[int]:
        """元帳に出す科目（期首残高か当期の動きがある科目）。"""
        used = {c for c, v in self.opening.items() if v}
        used |= {c for c, v in self.debit_total.items() if v}
        used |= {c for c, v in self.credit_total.items() if v}
        return sorted(used)


def compute_term(
    lines: list[JournalLine],
    chart: Chart,
    term: Term,
    opening: dict[int, int],
    opening_subs: dict[SubKey, int],
) -> TermResult:
    unknown = sorted(
        {s.code for line in lines for s in (line.debit, line.credit)} - set(chart.accounts)
    )
    if unknown:
        raise LedgerError(
            f"第{term.number}期: 勘定科目設定にない科目コードがあります: {unknown}"
            "（ソフトの勘定科目一覧を見て accounts.yaml に追加してください）"
        )

    included = sorted((ln for ln in lines if ln.amount != 0), key=lambda ln: ln.sort_key)
    excluded = [ln for ln in lines if ln.amount == 0]

    closing = defaultdict(int, opening)
    closing_subs = defaultdict(int, opening_subs)
    tracked = {code for code, _ in opening_subs}
    dr: dict[int, int] = defaultdict(int)
    cr: dict[int, int] = defaultdict(int)
    for line in included:
        for side, sign, totals in ((line.debit, 1, dr), (line.credit, -1, cr)):
            closing[side.code] += sign * side.amount
            totals[side.code] += side.amount
            if side.code in tracked:
                closing_subs[(side.code, side.sub)] += sign * side.amount

    return TermResult(
        term=term,
        chart=chart,
        lines=included,
        excluded=excluded,
        opening=dict(opening),
        closing=dict(closing),
        opening_subs=dict(opening_subs),
        closing_subs=dict(closing_subs),
        debit_total=dict(dr),
        credit_total=dict(cr),
    )


def carry_forward(prev: TermResult) -> tuple[dict[int, int], dict[SubKey, int]]:
    """前期末残高から翌期首残高を作る（CLAUDE.md §5）。

    資産・負債・正味財産はそのまま繰り越し、繰越正味財産には当期正味財産増減額を加える。
    収益・費用・仮勘定（複合）は0から始める。
    """
    chart = prev.chart
    opening = {
        code: value
        for code, value in prev.closing.items()
        if chart[code].category in BALANCE_SHEET and value
    }
    retained = chart.retained.code
    # 貸方残の科目なので、増加は借方正の符号ではマイナスになる
    opening[retained] = opening.get(retained, 0) - prev.net_income
    subs = {
        key: value
        for key, value in prev.closing_subs.items()
        if chart[key[0]].category not in PROFIT_AND_LOSS
    }
    return opening, subs


def initial_opening(config: OrgConfig) -> tuple[dict[int, int], dict[SubKey, int]]:
    """設定ファイルの期首残高（通常の向きで正の数）を、借方正の符号付きに直す。"""
    chart = config.chart
    opening: dict[int, int] = defaultdict(int)
    subs: dict[SubKey, int] = {}
    for (code, sub), amount in config.opening.items():
        signed = amount if chart[code].debit_normal else -amount
        opening[code] += signed
        if sub is not None:
            subs[(code, sub)] = signed
    sub_codes = {code for code, _ in subs}
    for code in sub_codes:
        if (code, None) in config.opening:
            raise LedgerError(f"期首残高: 科目{code}は合計と補助科目別の両方で指定されています")
    return dict(opening), subs


def compute_terms(config: OrgConfig, last_year: int) -> list[TermResult]:
    """最初の期から last_year の期までを順に計算する。"""
    if last_year < config.first_year:
        raise LedgerError(f"{last_year}年度は最初の期（{config.first_year}年度）より前です")
    opening, opening_subs = initial_opening(config)
    results = []
    for year in range(config.first_year, last_year + 1):
        term = Term(year, config.term_number(year))
        lines = read_journal(config.journal_path(year), year)
        result = compute_term(lines, config.chart, term, opening, opening_subs)
        results.append(result)
        opening, opening_subs = carry_forward(result)
    return results
