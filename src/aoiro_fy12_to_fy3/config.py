"""設定ファイル（org.yaml と accounts.yaml）の読み込み。

団体名・補助科目名・期首残高などの団体固有の値はすべてここから読む。
本番用の設定はリポジトリ外に置き、リポジトリには config/*.example.yaml だけを置く。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 科目の区分。借方残（資産・費用・仮勘定）か貸方残かは区分で決まる。
CATEGORIES = ("asset", "liability", "net_assets", "revenue", "cost", "expense", "suspense")
DEBIT_NORMAL = frozenset({"asset", "cost", "expense", "suspense"})
BALANCE_SHEET = frozenset({"asset", "liability", "net_assets"})
PROFIT_AND_LOSS = frozenset({"revenue", "cost", "expense"})

# 正味財産のうち特別な扱いをする科目の役割
ROLE_RETAINED = "retained"  # 繰越正味財産。翌期首に当期正味財産増減額を加える
ROLE_RESERVE = "reserve"  # 任意積立金。決算書では補助科目別に表示する

DEFAULT_SECTION_LABELS = {
    "revenue": "収入",
    "cost": "援助費",
    "expense": "一般管理費",
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SubAccount:
    code: int
    name: str
    # 過去の正式様式の決算書に出す名称（例: 補助「その他」→「その他収入」）
    label: str
    always_show: bool = False


@dataclass(frozen=True)
class Account:
    code: int
    name: str
    category: str
    role: str | None = None
    subs: dict[int, SubAccount] = field(default_factory=dict)
    # ソフトの試算表・決算書での表記（空白を除いて比較する）。省略時は name
    software_name: str | None = None
    # 金額が0でも過去の正式様式の決算書に行を出す
    always_show: bool = False
    # 正式様式の損益計算書で補助科目別に内訳を出す
    breakdown_by_sub: bool = False

    @property
    def debit_normal(self) -> bool:
        return self.category in DEBIT_NORMAL

    def sub_name(self, sub: int | None) -> str:
        if sub is None:
            return ""
        s = self.subs.get(sub)
        return s.name if s else ""


@dataclass(frozen=True)
class Chart:
    """勘定科目体系。コード順に並べて持つ。"""

    accounts: dict[int, Account]
    section_labels: dict[str, str]

    def __getitem__(self, code: int) -> Account:
        return self.accounts[code]

    def __contains__(self, code: int) -> bool:
        return code in self.accounts

    def by_category(self, category: str) -> list[Account]:
        return [a for a in self.accounts.values() if a.category == category]

    def _by_role(self, role: str) -> Account:
        found = [a for a in self.accounts.values() if a.role == role]
        if len(found) != 1:
            raise ConfigError(f"role: {role} の科目がちょうど1つ必要です（{len(found)}個）")
        return found[0]

    @property
    def retained(self) -> Account:
        return self._by_role(ROLE_RETAINED)

    @property
    def reserve(self) -> Account:
        return self._by_role(ROLE_RESERVE)


@dataclass(frozen=True)
class OrgConfig:
    base_dir: Path
    name: str
    address: tuple[str, ...]
    # 期番号 = 年度 - term_offset
    term_offset: int
    first_year: int
    # 最初の期の期首残高。キーは (科目コード, 補助コード or None)、値は科目の通常の向きで正の数
    opening: dict[tuple[int, int | None], int]
    journal: str
    outputs: dict[str, str]
    verify: dict[str, str]
    font: Path | None
    chart: Chart

    def term_number(self, year: int) -> int:
        return year - self.term_offset

    def journal_path(self, year: int) -> Path:
        return self.base_dir / self.journal.format(year=year)

    def output_name(self, kind: str, year: int) -> str:
        return self.outputs[kind].format(name=self.name, term=self.term_number(year), year=year)


@dataclass(frozen=True)
class Term:
    """1期分の会計期間。ソフト上の年度データ N = N年4月1日〜(N+1)年3月31日。"""

    year: int
    number: int

    @property
    def start(self) -> dt.date:
        return dt.date(self.year, 4, 1)

    @property
    def end(self) -> dt.date:
        return dt.date(self.year + 1, 3, 31)


DEFAULT_OUTPUTS = {
    "ledger_pdf": "総勘定元帳/総勘定元帳({name}_第{term}期).pdf",
    "financials_xlsx": "決算書/決算書({name}_第{term}期).xlsx",
    "software_financials_xls": "決算書/決算書({name}_第{term}期).xls",
}


def _int_key(value: object, where: str) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ConfigError(f"{where}: 数値のコードが必要です: {value!r}") from None


def load_chart(path: Path) -> Chart:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    accounts: dict[int, Account] = {}
    for raw in data.get("accounts", []):
        code = _int_key(raw.get("code"), f"{path}: code")
        category = raw.get("category")
        if category not in CATEGORIES:
            raise ConfigError(f"{path}: 科目{code}の category が不正です: {category!r}")
        subs = {}
        for sub_code, sub in (raw.get("subs") or {}).items():
            sc = _int_key(sub_code, f"{path}: 科目{code}の補助コード")
            if isinstance(sub, str):
                sub = {"name": sub}
            subs[sc] = SubAccount(
                code=sc,
                name=sub["name"],
                label=sub.get("label", sub["name"]),
                always_show=bool(sub.get("always_show", False)),
            )
        if code in accounts:
            raise ConfigError(f"{path}: 科目コード{code}が重複しています")
        accounts[code] = Account(
            code=code,
            name=raw["name"],
            category=category,
            role=raw.get("role"),
            subs=subs,
            software_name=raw.get("software_name"),
            always_show=bool(raw.get("always_show", False)),
            breakdown_by_sub=bool(raw.get("breakdown_by_sub", False)),
        )
    labels = {**DEFAULT_SECTION_LABELS, **(data.get("section_labels") or {})}
    return Chart(dict(sorted(accounts.items())), labels)


def load_config(path: str | Path) -> OrgConfig:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    base = path.parent
    chart = load_chart(base / data.get("accounts", "accounts.yaml"))

    opening: dict[tuple[int, int | None], int] = {}
    for code_raw, value in (data.get("opening_balances") or {}).items():
        code = _int_key(code_raw, f"{path}: opening_balances")
        if code not in chart:
            raise ConfigError(f"{path}: 期首残高の科目{code}が勘定科目設定にありません")
        if isinstance(value, dict):
            for sub_raw, amount in value.items():
                opening[(code, _int_key(sub_raw, f"{path}: 科目{code}の補助"))] = int(amount)
        else:
            opening[(code, None)] = int(value)

    font = data.get("font")
    return OrgConfig(
        base_dir=base,
        name=data["name"],
        address=tuple(data.get("address") or ()),
        term_offset=int(data.get("term_offset", 1995)),
        first_year=int(data["first_year"]),
        opening=opening,
        journal=data.get("journal", "仕訳帳/仕訳日記帳-{year}.csv"),
        outputs={**DEFAULT_OUTPUTS, **(data.get("outputs") or {})},
        verify=dict(data.get("verify") or {}),
        font=(base / font) if font else None,
        chart=chart,
    )
