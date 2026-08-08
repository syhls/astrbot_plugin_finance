import json
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ledger import (
    LedgerError,
    LedgerStore,
    make_transaction,
    parse_manual_record,
    parse_period,
    parse_removal_scope,
)


class LedgerTests(unittest.TestCase):
    def test_manual_record_parsing(self):
        item = parse_manual_record("支出 ￥25.50 午饭")
        self.assertEqual(item.kind, "expense")
        self.assertEqual(item.amount, Decimal("25.50"))
        self.assertEqual(item.note, "午饭")
        self.assertRegex(item.timestamp, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_invalid_amount_is_rejected(self):
        with self.assertRaises(LedgerError):
            parse_manual_record("收入 0 工资")
        with self.assertRaises(LedgerError):
            make_transaction(kind="expense", amount="1.234", note="错误金额")

    def test_store_isolated_accounts_and_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            store = LedgerStore(path)
            store.add_many(
                "qq:alice",
                [
                    make_transaction(
                        kind="收入",
                        amount="100",
                        note="工资",
                        timestamp="2026-08-01 09:00",
                    ),
                    make_transaction(
                        kind="支出",
                        amount="20.50",
                        note="午饭",
                        timestamp="2026-08-02 12:30",
                    ),
                ],
            )
            store.add_many(
                "qq:bob",
                [
                    make_transaction(
                        kind="支出",
                        amount="999",
                        note="Bob 的账",
                        timestamp="2026-08-02 12:30",
                    )
                ],
            )

            records = store.list_records("qq:alice")
            self.assertEqual(len(records), 2)
            self.assertEqual(
                sum((x.amount for x in records), Decimal("0")), Decimal("120.50")
            )
            self.assertEqual(store.list_records("qq:bob")[0].note, "Bob 的账")

    def test_period_is_left_closed_right_open(self):
        now = datetime(2026, 8, 8, 14, 30)
        start, end, label = parse_period("本月", now)
        self.assertEqual(start, datetime(2026, 8, 1, 0, 0))
        self.assertEqual(end, datetime(2026, 9, 1, 0, 0))
        self.assertEqual(label, "2026-08")

    def test_llm_period(self):
        start, end, label = parse_period(
            "",
            datetime(2026, 8, 8),
            {"start": "2026-08-01 00:00", "end": "2026-08-08 00:00", "label": "上周"},
        )
        self.assertEqual(start, datetime(2026, 8, 1))
        self.assertEqual(end, datetime(2026, 8, 8))
        self.assertEqual(label, "上周")

    def test_remove_last(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LedgerStore(Path(directory) / "ledger.json")
            first = make_transaction(kind="收入", amount="1", note="一")
            second = make_transaction(kind="支出", amount="2", note="二")
            store.add_many("user", [first, second])
            self.assertEqual(store.remove_last("user").id, second.id)
            self.assertEqual(store.list_records("user"), [first])
            restored = store.restore_last_batch("user")
            self.assertEqual(restored.records, (second,))
            self.assertEqual(len(store.list_records("user")), 2)
            self.assertEqual(store.remove_last("missing"), None)

    def test_removal_scopes(self):
        now = datetime(2026, 8, 8, 14, 30)
        scope, start, end, confirmed = parse_removal_scope("当周", now)
        self.assertEqual(scope, "当周")
        self.assertEqual(start, datetime(2026, 8, 3))
        self.assertEqual(end, datetime(2026, 8, 10))
        self.assertFalse(confirmed)
        self.assertEqual(
            parse_removal_scope("全部 确认", now), ("全部", None, None, True)
        )

    def test_remove_range_and_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LedgerStore(Path(directory) / "ledger.json")
            records = [
                make_transaction(
                    kind="支出",
                    amount="10",
                    note="本周一",
                    timestamp="2026-08-03 12:00",
                ),
                make_transaction(
                    kind="收入",
                    amount="20",
                    note="本周二",
                    timestamp="2026-08-04 12:00",
                ),
                make_transaction(
                    kind="支出",
                    amount="30",
                    note="上个月",
                    timestamp="2026-07-01 12:00",
                ),
            ]
            store.add_many("user", records)
            batch = store.remove_range(
                "user",
                start=datetime(2026, 8, 3),
                end=datetime(2026, 8, 10),
                scope="当周",
            )

            self.assertEqual(len(batch.records), 2)
            self.assertEqual(store.list_records("user"), [records[2]])
            restored = store.restore_last_batch("user")
            self.assertEqual(restored.scope, "当周")
            self.assertEqual(len(store.list_records("user")), 3)

    def test_only_latest_twenty_deletion_batches_are_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            store = LedgerStore(path)
            for index in range(21):
                store.add_many(
                    "user",
                    [
                        make_transaction(
                            kind="支出", amount="1", note=f"记录 {index}"
                        )
                    ],
                )
                store.remove_last("user")

            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(value["trash"]["user"]), 20)

    def test_json_keeps_decimal_as_string(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            LedgerStore(path).add_many(
                "user", [make_transaction(kind="支出", amount="0.10", note="测试")]
            )
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["accounts"]["user"][0]["amount"], "0.10")


if __name__ == "__main__":
    unittest.main()
