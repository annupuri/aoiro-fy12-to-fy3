"""総勘定元帳（PDF）の出力。

ソフトの元帳PDFの体裁にならい、1仕訳を2段（上段: 相手科目・取引摘要・金額、下段: 補助摘要）で表示する。
科目ごとにページを分け、月ごとに月計、決算整理の後に総合計、各ページの末尾に頁合計を出す。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from pathlib import Path

from reportlab.lib.colors import Color, black
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from .ledger import TermResult

FONT_NAME = "LedgerJP"
FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/ipaexfont-gothic/ipaexg.ttf",
    "/usr/share/fonts/truetype/ipaexfont-gothic/ipaexg.ttf",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/mnt/c/Windows/Fonts/BIZ-UDGothicR.ttc",
    "C:/Windows/Fonts/BIZ-UDGothicR.ttc",
    "/mnt/c/Windows/Fonts/msgothic.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
)

PAGE_W, PAGE_H = A4
MARGIN_X = 28
# 列: 伝票№, 日付, 相手科目／補助, 摘要, 借方金額, 貸方金額, 残高
COL_WIDTHS = (34, 32, 128, 150, 64, 64, 67)
TABLE_TOP = PAGE_H - 104
HEADER_H = 26
ROW_H = 24
ROWS_PER_PAGE = 27
SHADE = Color(0.88, 0.96, 0.98)
GRID = Color(0.2, 0.35, 0.8)


class FontNotFoundError(FileNotFoundError):
    pass


def find_font(preferred: Path | None = None) -> Path:
    if preferred is not None:
        if not preferred.exists():
            raise FontNotFoundError(f"設定ファイルで指定したフォントがありません: {preferred}")
        return preferred
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    raise FontNotFoundError(
        "日本語フォントが見つかりません。`sudo apt install fonts-ipaexfont-gothic` で入れるか、"
        "org.yaml の font: にフォントファイルのパスを書いてください。"
    )


def register_font(path: Path) -> None:
    if FONT_NAME in pdfmetrics.getRegisteredFontNames():
        return
    if path.suffix.lower() == ".ttc":
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(path), subfontIndex=0))
    else:
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(path)))


@dataclass(frozen=True)
class Row:
    kind: str  # "opening" | "entry" | "subtotal" | "total"
    voucher: str = ""
    date: dt.date | None = None
    counter: str = ""
    memo: str = ""
    sub_memo: str = ""
    debit: int | None = None
    credit: int | None = None
    balance: int | None = None
    show_balance: bool = False


def _month_label(date: dt.date, closing: bool) -> str:
    return "決算整理合計" if closing else f"{date.month}月度合計"


def account_rows(result: TermResult, code: int) -> list[Row]:
    """1科目分の元帳の行を作る。"""
    chart = result.chart
    account = chart[code]
    sign = 1 if account.debit_normal else -1
    balance = result.opening_balance(code)
    rows = [Row("opening", memo="繰越残高", balance=balance, show_balance=True)]

    def counter_text(side) -> str:
        name = chart[side.code].name if side.code in chart else side.name
        return f"{name} /{side.sub_name}" if side.sub_name else name

    group_key = None
    group: list[Row] = []
    total_dr = total_cr = total_n = 0

    def close_group() -> None:
        if not group:
            return
        dr = sum(r.debit or 0 for r in group)
        cr = sum(r.credit or 0 for r in group)
        rows.append(Row("subtotal", memo=group_key[1], sub_memo=f"{len(group)}件", debit=dr, credit=cr))
        group.clear()

    for line in result.lines:
        is_dr, is_cr = line.debit.code == code, line.credit.code == code
        if not (is_dr or is_cr):
            continue
        key = (line.closing, line.date.year, line.date.month)
        label = _month_label(line.date, line.closing)
        if group_key is not None and key != group_key[0]:
            close_group()
        group_key = (key, label)
        dr = line.amount if is_dr else 0
        cr = line.amount if is_cr else 0
        balance += sign * (dr - cr)
        row = Row(
            "entry",
            voucher=str(line.voucher),
            date=line.date,
            counter=counter_text(line.credit if is_dr else line.debit),
            memo=line.memo,
            sub_memo=line.sub_memo,
            debit=dr or None,
            credit=cr or None,
            balance=balance,
        )
        rows.append(row)
        group.append(row)
        total_dr, total_cr, total_n = total_dr + dr, total_cr + cr, total_n + 1
    close_group()
    rows.append(Row("total", memo="総　合　計", sub_memo=f"{total_n}件", debit=total_dr, credit=total_cr))

    # 残高は同じ日付の最後の行にだけ出す（ソフトの元帳と同じ）
    for i, row in enumerate(rows):
        if row.kind == "entry":
            nxt = rows[i + 1]
            if nxt.kind != "entry" or nxt.date != row.date:
                rows[i] = replace(row, show_balance=True)
    return rows


def paginate(rows: list[Row], per_page: int = ROWS_PER_PAGE) -> list[list[Row]]:
    pages = [rows[i : i + per_page] for i in range(0, len(rows), per_page)] or [[]]
    for page in pages:
        # ページの最後の仕訳行には残高を出す
        for i in range(len(page) - 1, -1, -1):
            if page[i].kind == "entry":
                page[i] = replace(page[i], show_balance=True)
                break
    return pages


def _fit(c: Canvas, text: str, x: float, y: float, width: float, size: float, align: str = "left") -> None:
    """幅に収まるように文字を縮め、それでも入らなければ末尾を省略する。"""
    if not text:
        return
    fs = size
    while fs > 5.0 and pdfmetrics.stringWidth(text, FONT_NAME, fs) > width:
        fs -= 0.25
    while pdfmetrics.stringWidth(text, FONT_NAME, fs) > width and len(text) > 1:
        text = text[:-2] + "…"
    c.setFont(FONT_NAME, fs)
    if align == "right":
        c.drawRightString(x + width, y, text)
    elif align == "center":
        c.drawCentredString(x + width / 2, y, text)
    else:
        c.drawString(x, y, text)


def _num(value: int | None) -> str:
    if value is None:
        return ""
    return f"{value:,}" if value >= 0 else f"△{-value:,}"


class LedgerWriter:
    def __init__(self, path: Path, org_name: str, result: TermResult, created: dt.date):
        self.c = Canvas(str(path), pagesize=A4)
        self.c.setTitle("総勘定元帳")
        self.org_name = org_name
        self.result = result
        self.created = created
        self.page_no = 0
        xs = [MARGIN_X]
        for w in COL_WIDTHS:
            xs.append(xs[-1] + w)
        self.xs = xs

    def _header(self, code: int) -> None:
        c, t = self.c, self.result.term
        account = self.result.chart[code]
        c.setFillColor(black)
        _fit(c, self.org_name, MARGIN_X, PAGE_H - 40, 250, 10)
        _fit(c, f"{self.page_no} 頁", PAGE_W - MARGIN_X - 60, PAGE_H - 40, 60, 9, "right")
        title = "総 勘 定 元 帳"
        c.setFont(FONT_NAME, 16)
        tw = pdfmetrics.stringWidth(title, FONT_NAME, 16)
        tx = PAGE_W / 2 - tw / 2 - 40
        c.drawString(tx, PAGE_H - 62, title)
        c.setLineWidth(1)
        c.line(tx - 8, PAGE_H - 66, tx + tw + 8, PAGE_H - 66)
        _fit(c, f"( {account.code}:{account.name} )", tx + tw + 14, PAGE_H - 62, 180, 12)
        period = f"({t.start.year}年4月1日～{t.end.year}年3月31日)"
        _fit(c, period, MARGIN_X, PAGE_H - 80, PAGE_W - 2 * MARGIN_X, 9, "center")
        _fit(c, "（税込）", PAGE_W - MARGIN_X - 60, PAGE_H - 80, 60, 9, "right")
        created = f"作成日 {self.created.year}年{self.created.month}月{self.created.day}日"
        _fit(c, created, PAGE_W - MARGIN_X - 160, PAGE_H - 94, 160, 8, "right")

        top = TABLE_TOP
        c.setFillColor(SHADE)
        c.rect(self.xs[0], top - HEADER_H, self.xs[-1] - self.xs[0], HEADER_H, stroke=0, fill=1)
        c.setFillColor(black)
        titles = ("伝票№", "日 付", "相 手 科 目 ／ 補 助", "摘　　　要", "借 方 金 額", "貸 方 金 額", "残　　高")
        for i, text in enumerate(titles):
            _fit(c, text, self.xs[i] + 2, top - 16, COL_WIDTHS[i] - 4, 8, "center")

    def _row(self, row: Row, y_top: float, prev_date: dt.date | None) -> None:
        c, xs = self.c, self.xs
        if row.kind in ("subtotal", "total"):
            c.setFillColor(SHADE)
            c.rect(xs[0], y_top - ROW_H, xs[-1] - xs[0], ROW_H, stroke=0, fill=1)
            c.setFillColor(black)
        y1, y2 = y_top - 10, y_top - 20
        if row.kind == "entry":
            _fit(c, row.voucher, xs[0] + 2, y1, COL_WIDTHS[0] - 5, 8, "right")
            date = "〃" if row.date == prev_date else f"{row.date.month}/{row.date.day:>2}"
            _fit(c, date, xs[1] + 2, y1, COL_WIDTHS[1] - 5, 8, "right")
            _fit(c, row.counter, xs[2] + 3, y1, COL_WIDTHS[2] - 6, 8)
        _fit(c, row.memo, xs[3] + 3, y1, COL_WIDTHS[3] - 6, 8)
        _fit(c, row.sub_memo, xs[3] + 3 + (20 if row.kind != "entry" else 0), y2, COL_WIDTHS[3] - 26, 7.5)
        _fit(c, _num(row.debit), xs[4] + 2, y1, COL_WIDTHS[4] - 5, 8, "right")
        _fit(c, _num(row.credit), xs[5] + 2, y1, COL_WIDTHS[5] - 5, 8, "right")
        if row.show_balance and row.balance is not None:
            _fit(c, _num(row.balance), xs[6] + 2, y1, COL_WIDTHS[6] - 5, 8, "right")

    def _grid(self, bottom: float) -> None:
        c, xs = self.c, self.xs
        c.setStrokeColor(GRID)
        c.setLineWidth(0.5)
        c.rect(xs[0], bottom, xs[-1] - xs[0], TABLE_TOP - bottom, stroke=1, fill=0)
        for x in xs[1:-1]:
            c.line(x, bottom, x, TABLE_TOP)
        c.setStrokeColor(black)

    def page(self, code: int, rows: list[Row]) -> None:
        self.page_no += 1
        self._header(code)
        c = self.c
        y = TABLE_TOP - HEADER_H
        c.setStrokeColor(GRID)
        c.setLineWidth(0.5)
        prev_date = None
        for row in rows:
            self._row(row, y, prev_date)
            prev_date = row.date if row.kind == "entry" else None
            y -= ROW_H
            c.setStrokeColor(GRID)
            c.line(self.xs[0], y, self.xs[-1], y)
        # 頁合計
        entries = [r for r in rows if r.kind == "entry"]
        page_total = Row(
            "subtotal",
            memo="【頁　合　計】",
            debit=sum(r.debit or 0 for r in entries),
            credit=sum(r.credit or 0 for r in entries),
        )
        self._row(page_total, y, None)
        y -= ROW_H
        c.setStrokeColor(GRID)
        c.line(self.xs[0], TABLE_TOP - HEADER_H, self.xs[-1], TABLE_TOP - HEADER_H)
        self._grid(y)
        c.showPage()

    def save(self) -> None:
        self.c.save()


def write_ledger_pdf(
    path: Path,
    org_name: str,
    result: TermResult,
    font: Path | None = None,
    created: dt.date | None = None,
) -> int:
    """総勘定元帳PDFを書き、ページ数を返す。"""
    register_font(find_font(font))
    writer = LedgerWriter(path, org_name, result, created or dt.date.today())
    for code in result.accounts_in_use():
        for rows in paginate(account_rows(result, code)):
            writer.page(code, rows)
    writer.save()
    return writer.page_no
