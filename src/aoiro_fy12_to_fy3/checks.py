"""帳簿の内部整合性の検証（CLAUDE.md §7 の 1〜6 と補助科目の残高）。

ソフトの出力との照合（§7 の 7）は verify.py で行う。
"""

from __future__ import annotations

from .ledger import TermResult, carry_forward
from .statements import build_financials


def check_term(result: TermResult) -> list[str]:
    """1期分の検証。問題がなければ空のリストを返す。"""
    problems: list[str] = []
    chart = result.chart

    # 1. 全仕訳で借方合計 ＝ 貸方合計
    unbalanced = [ln for ln in result.lines if ln.debit.amount != ln.credit.amount]
    for ln in unbalanced:
        problems.append(f"伝票{ln.voucher}: 借方{ln.debit.amount:,} ≠ 貸方{ln.credit.amount:,}")
    dr, cr = sum(result.debit_total.values()), sum(result.credit_total.values())
    if dr != cr:
        problems.append(f"仕訳の借方合計{dr:,} ≠ 貸方合計{cr:,}")

    # 2. 期末の試算表で借方 ＝ 貸方
    if sum(result.closing.values()) != 0:
        problems.append(f"期末試算表の貸借が一致しません（差額{sum(result.closing.values()):,}）")

    # 3, 4. 貸借対照表
    fin = build_financials(result)
    if fin.asset_total != fin.liabilities_and_capital_total:
        problems.append(
            f"資産合計{fin.asset_total:,} ≠ 負債・資本合計{fin.liabilities_and_capital_total:,}"
        )
    reserve_rows_total = sum(v for _, v in fin.reserve_rows)
    if reserve_rows_total != fin.reserve_total:
        problems.append(f"積立金の内訳の合計{reserve_rows_total:,} ≠ 積立金合計{fin.reserve_total:,}")
    if fin.net_assets_subtotal != fin.retained_closing + reserve_rows_total:
        problems.append("正味財産計が繰越正味財産と積立金合計の和になっていません")
    transfers = sum(v for _, v in fin.reserve_transfer_rows)
    if -transfers != fin.reserve_transfer_total:
        problems.append(
            f"繰越正味財産の期中増減{fin.reserve_transfer_total:,}が"
            f"任意積立金との振替額{-transfers:,}と一致しません（積立金以外との仕訳があります）"
        )

    # 6. 複合（仮勘定）の期末残高 ＝ 0
    for account in chart.by_category("suspense"):
        balance = result.closing_balance(account.code)
        if balance:
            problems.append(f"{account.code} {account.name} の期末残高が0ではありません: {balance:,}")

    # 補助科目別に管理している科目の残高がマイナスになっていない
    for (code, sub), _ in sorted(result.closing_subs.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        balance = result.closing_sub_balance(code, sub)
        if balance < 0:
            name = chart[code].sub_name(sub) or f"補助{sub}"
            problems.append(f"{code} {chart[code].name}／{name} の期末残高がマイナスです: {balance:,}")
    return problems


def check_chain(results: list[TermResult]) -> list[str]:
    """5. 前期末残高 → 当期首残高のつながり。"""
    problems = []
    for prev, cur in zip(results, results[1:]):
        expected, expected_subs = carry_forward(prev)
        codes = set(expected) | set(cur.opening)
        for code in sorted(codes):
            if expected.get(code, 0) != cur.opening.get(code, 0):
                problems.append(
                    f"第{cur.term.number}期首の{code}が前期末からの繰越と一致しません"
                    f"（{cur.normal(code, cur.opening.get(code, 0)):,} / "
                    f"期待値{cur.normal(code, expected.get(code, 0)):,}）"
                )
        if expected_subs != cur.opening_subs:
            problems.append(f"第{cur.term.number}期首の補助科目別残高が前期末からの繰越と一致しません")
    return problems
