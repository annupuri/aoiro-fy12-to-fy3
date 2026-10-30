"""コマンドライン。

  python -m aoiro_fy12_to_fy3 build  --config <org.yaml> --year 2017 --out <出力先>
  python -m aoiro_fy12_to_fy3 build  --config <org.yaml> --all --out <出力先>
  python -m aoiro_fy12_to_fy3 verify --config <org.yaml> --all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import checks
from .config import ConfigError, OrgConfig, load_config
from .ledger import LedgerError, TermResult, compute_terms
from .load import JournalError
from .report_financials import write_official, write_software
from .report_ledger_pdf import FontNotFoundError, write_ledger_pdf
from .statements import build_financials


def _years(config: OrgConfig, args: argparse.Namespace) -> list[int]:
    if args.all:
        last = config.first_year
        while config.journal_path(last + 1).exists():
            last += 1
        return list(range(config.first_year, last + 1))
    if not args.year:
        raise SystemExit("--year か --all を指定してください")
    return sorted(set(args.year))


def _log(message: str = "") -> None:
    print(message, file=sys.stderr)


def _run_checks(results: list[TermResult], years: list[int]) -> bool:
    ok = True
    for problem in checks.check_chain(results):
        _log(f"[NG] {problem}")
        ok = False
    for result in results:
        if result.term.year not in years:
            continue
        problems = checks.check_term(result)
        for problem in problems:
            _log(f"[NG] 第{result.term.number}期: {problem}")
        ok = ok and not problems
    return ok


def _log_excluded(result: TermResult) -> None:
    for line in result.excluded:
        _log(
            f"  金額0円の行を元帳から除外: 伝票{line.voucher} {line.soft_date}"
            f"（実際の日付 {line.date:%Y/%m/%d}） {line.debit.name} / {line.credit.name} {line.memo}"
        )


def cmd_build(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    years = _years(config, args)
    results = compute_terms(config, max(years))
    if not _run_checks(results, years) and not args.force:
        _log("検証で問題が見つかったため出力しませんでした（--force で出力します）")
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for result in results:
        if result.term.year not in years:
            continue
        year = result.term.year
        fin = build_financials(result)
        _log(f"第{result.term.number}期（{year}年度）")
        _log_excluded(result)
        # 出力ファイル名の設定にはフォルダを含められる（例: 総勘定元帳/総勘定元帳(…).pdf）
        ledger = out / config.output_name("ledger_pdf", year)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        pages = write_ledger_pdf(ledger, config.name, result, config.font)
        _log(f"  {ledger.relative_to(out)}（{pages}頁）")
        for kind, writer in (("financials_xlsx", write_official), ("software_financials_xls", write_software)):
            path = out / config.output_name(kind, year)
            path.parent.mkdir(parents=True, exist_ok=True)
            writer(path, config, fin)
            _log(f"  {path.relative_to(out)}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from .verify import verify_term

    config = load_config(args.config)
    years = _years(config, args)
    results = compute_terms(config, max(years))
    ok = _run_checks(results, years)
    for result in results:
        if result.term.year not in years:
            continue
        report = verify_term(config, result)
        status = "OK" if not report.problems else "NG"
        _log(f"[{status}] 第{report.term}期（{result.term.year}年度） 照合: {', '.join(report.sources) or 'なし'}")
        for message in report.skipped:
            _log(f"  - {message}")
        for problem in report.problems:
            _log(f"  ! {problem}")
        ok = ok and not report.problems
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aoiro-fy12-to-fy3", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, help_text in (
        ("build", cmd_build, "総勘定元帳PDFと決算書(xlsx・xls)を作る"),
        ("verify", cmd_verify, "帳簿の整合性を検証し、ソフトの試算表・決算書と照合する"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--config", required=True, help="org.yaml のパス")
        group = p.add_mutually_exclusive_group()
        group.add_argument("--year", type=int, action="append", help="ソフト上の年度（複数指定可）")
        group.add_argument("--all", action="store_true", help="仕訳日記帳がある全年度")
        if name == "build":
            p.add_argument("--out", required=True, help="出力先フォルダ")
            p.add_argument("--force", action="store_true", help="検証で問題があっても出力する")
        p.set_defaults(func=func)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, JournalError, LedgerError, FontNotFoundError, FileNotFoundError) as e:
        _log(f"エラー: {e}")
        return 2
