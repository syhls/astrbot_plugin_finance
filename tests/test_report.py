import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ledger import LedgerError, make_transaction
from report import (
    build_monthly_report,
    parse_export_request,
    write_report_csv,
)


class MonthlyReportTests(unittest.TestCase):
    def test_export_request_accepts_any_argument_order(self):
        now = datetime(2026, 8, 8, 12, 0)
        self.assertEqual(
            parse_export_request("文件 2026-07", now), ("2026-07", "file")
        )
        self.assertEqual(parse_export_request("", now), ("2026-08", "image"))
        self.assertEqual(
            parse_export_request("", now, "文件"), ("2026-08", "file")
        )
        with self.assertRaises(LedgerError):
            parse_export_request("2026-13 图片", now)

    def test_month_is_grouped_by_monday_to_sunday(self):
        report = build_monthly_report(
            [
                make_transaction(
                    kind="收入",
                    amount="1000",
                    note="工资",
                    timestamp="2026-08-01 09:00",
                ),
                make_transaction(
                    kind="支出",
                    amount="20",
                    note="周一午饭",
                    timestamp="2026-08-03 12:00",
                ),
                make_transaction(
                    kind="支出",
                    amount="30",
                    note="月底消费",
                    timestamp="2026-08-31 20:00",
                ),
            ],
            "2026-08",
        )

        self.assertEqual(len(report.weeks), 6)
        self.assertEqual(report.weeks[0].start, datetime(2026, 8, 1))
        self.assertEqual(report.weeks[0].end, datetime(2026, 8, 3))
        self.assertEqual(report.weeks[1].start, datetime(2026, 8, 3))
        self.assertEqual(report.weeks[-1].records[0].note, "月底消费")
        self.assertEqual(len(report.income_records), 1)
        self.assertEqual(len(report.expense_records), 2)
        self.assertEqual(report.income_total, Decimal("1000"))
        self.assertEqual(report.expense_total, Decimal("50"))
        self.assertEqual(report.balance, Decimal("950"))
        self.assertEqual(report.average_expense, Decimal("25"))
        self.assertEqual(report.largest_expense.note, "月底消费")

    def test_csv_contains_summary_and_weekly_records(self):
        report = build_monthly_report(
            [
                make_transaction(
                    kind="支出",
                    amount="18.50",
                    note="水果",
                    timestamp="2026-08-08 18:00",
                )
            ],
            "2026-08",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = write_report_csv(report, Path(directory) / "report.csv")
            content = path.read_text(encoding="utf-8-sig")

        self.assertIn("支出笔数,1", content)
        self.assertIn("支出总额,18.50", content)
        self.assertIn("第2周", content)
        self.assertIn("水果", content)

    def test_csv_escapes_spreadsheet_formula_notes(self):
        report = build_monthly_report(
            [
                make_transaction(
                    kind="支出",
                    amount="1",
                    note="=1+1",
                    timestamp="2026-08-08 18:00",
                )
            ],
            "2026-08",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = write_report_csv(report, Path(directory) / "report.csv")
            content = path.read_text(encoding="utf-8-sig")

        self.assertIn("'=1+1", content)


if __name__ == "__main__":
    unittest.main()
