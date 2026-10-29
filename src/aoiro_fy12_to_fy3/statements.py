"""決算書の数値（様式に依存しない部分）。"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Account
from .ledger import TermResult

Row = tuple[str, int]


@dataclass(frozen=True)
class Financials:
    result: TermResult
    assets: list[tuple[Account, int]]
    liabilities: list[tuple[Account, int]]
    retained_opening: int
    retained_closing: int
    # 任意積立金の補助科目別の期末残高（正式様式の表示名, 金額）
    reserve_rows: list[Row]
    reserve_total: int
    # 繰越正味財産と任意積立金の間の振替（正式様式の表示名, 金額。積立が正）
    reserve_transfer_rows: list[Row]
    revenue: list[tuple[Account, int]]
    cost: list[tuple[Account, int]]
    expense: list[tuple[Account, int]]
    net_income: int

    @property
    def asset_total(self) -> int:
        return sum(v for _, v in self.assets)

    @property
    def liability_total(self) -> int:
        return sum(v for _, v in self.liabilities)

    @property
    def net_assets_subtotal(self) -> int:
        """正味財産計 ＝ 繰越正味財産 ＋ 積立金合計。"""
        return self.retained_closing + self.reserve_total

    @property
    def capital_total(self) -> int:
        """資本の部合計 ＝ 正味財産計 ＋ 当期正味財産増減額。"""
        return self.net_assets_subtotal + self.net_income

    @property
    def liabilities_and_capital_total(self) -> int:
        return self.liability_total + self.capital_total

    @property
    def reserve_transfer_total(self) -> int:
        """当期積立金振替額（貸借対照表上は繰越正味財産の期中増減。積立なら負）。"""
        return self.retained_closing - self.retained_opening

    @property
    def revenue_total(self) -> int:
        return sum(v for _, v in self.revenue)

    @property
    def cost_total(self) -> int:
        return sum(v for _, v in self.cost)

    @property
    def expense_total(self) -> int:
        return sum(v for _, v in self.expense)

    def official_revenue_rows(self) -> list[Row]:
        """正式様式の収入の行。breakdown_by_sub の科目は補助科目別に出す。"""
        rows: list[Row] = []
        r = self.result
        by_sub = _sub_amounts(r, [a for a, _ in self.revenue if a.breakdown_by_sub])
        for account in r.chart.by_category("revenue"):
            if account.breakdown_by_sub:
                amounts = by_sub.get(account.code, {})
                unknown = set(amounts) - set(account.subs)
                if unknown and any(amounts[s] for s in unknown):
                    raise ValueError(
                        f"科目{account.code}に勘定科目設定にない補助コードがあります: {sorted(unknown, key=str)}"
                    )
                for sub in account.subs.values():
                    amount = amounts.get(sub.code, 0)
                    if amount or sub.always_show:
                        rows.append((sub.label, amount))
            else:
                amount = r.closing_balance(account.code)
                if amount or account.always_show:
                    rows.append((account.name, amount))
        return rows

    def official_rows(self, category: str) -> list[Row]:
        r = self.result
        return [
            (a.name, r.closing_balance(a.code))
            for a in r.chart.by_category(category)
            if r.closing_balance(a.code) or a.always_show
        ]


def _sub_amounts(result: TermResult, accounts: list[Account]) -> dict[int, dict[int | None, int]]:
    """収益科目の補助科目別の当期発生額（貸方 − 借方）。"""
    codes = {a.code for a in accounts}
    out: dict[int, dict[int | None, int]] = {}
    for line in result.lines:
        for side, sign in ((line.credit, 1), (line.debit, -1)):
            if side.code in codes:
                d = out.setdefault(side.code, {})
                d[side.sub] = d.get(side.sub, 0) + sign * side.amount
    return out


def build_financials(result: TermResult) -> Financials:
    chart = result.chart
    retained, reserve = chart.retained, chart.reserve

    def nonzero(category: str) -> list[tuple[Account, int]]:
        return [
            (a, result.closing_balance(a.code))
            for a in chart.by_category(category)
            if result.closing_balance(a.code)
        ]

    def assets_like(category: str) -> list[tuple[Account, int]]:
        return [
            (a, result.closing_balance(a.code))
            for a in chart.by_category(category)
            if result.closing_balance(a.code) or a.always_show
        ]

    other_net_assets = [
        a for a in chart.by_category("net_assets") if a.code not in (retained.code, reserve.code)
    ]
    stray = [a.code for a in other_net_assets if result.closing.get(a.code)]
    if stray:
        raise ValueError(f"繰越正味財産・任意積立金以外の正味財産科目に残高があります: {stray}")

    reserve_rows = []
    if any(code == reserve.code for code, _ in result.closing_subs):
        for sub in reserve.subs.values():
            amount = result.closing_sub_balance(reserve.code, sub.code)
            if amount or sub.always_show:
                reserve_rows.append((sub.label, amount))
        unknown = [
            s
            for (c, s), v in result.closing_subs.items()
            if c == reserve.code and v and s not in reserve.subs
        ]
        if unknown:
            raise ValueError(f"任意積立金に勘定科目設定にない補助コードの残高があります: {unknown}")
    elif result.closing_balance(reserve.code):
        reserve_rows.append((reserve.name, result.closing_balance(reserve.code)))

    transfers = result.reserve_transfers()
    transfer_rows = [
        (reserve.subs[s].label if s in reserve.subs else reserve.name, v)
        for s, v in sorted(transfers.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))
        if v
    ]

    return Financials(
        result=result,
        assets=assets_like("asset"),
        liabilities=assets_like("liability"),
        retained_opening=result.opening_balance(retained.code),
        retained_closing=result.closing_balance(retained.code),
        reserve_rows=reserve_rows,
        reserve_total=result.closing_balance(reserve.code),
        reserve_transfer_rows=transfer_rows,
        revenue=nonzero("revenue"),
        cost=nonzero("cost"),
        expense=nonzero("expense"),
        net_income=result.net_income,
    )
