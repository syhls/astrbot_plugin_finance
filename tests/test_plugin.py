import asyncio
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class _Logger:
    def warning(self, *_args, **_kwargs):
        pass


class _Filter:
    EventMessageType = types.SimpleNamespace(ALL="all")

    @staticmethod
    def command(*_args, **_kwargs):
        return lambda function: function

    @staticmethod
    def event_message_type(*_args, **_kwargs):
        return lambda function: function


class _Star:
    def __init__(self, context):
        self.context = context

    async def html_render(self, *_args, **_kwargs):
        return "https://example.test/monthly-report.jpg"


class _File:
    def __init__(self, name, file="", url=""):
        self.name = name
        self.file = file
        self.url = url


def _load_plugin_module():
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.AstrBotConfig = dict
    api.logger = _Logger()
    api_event = types.ModuleType("astrbot.api.event")
    api_event.AstrMessageEvent = object
    api_event.filter = _Filter
    api_components = types.ModuleType("astrbot.api.message_components")
    api_components.File = _File
    api_star = types.ModuleType("astrbot.api.star")
    api_star.Context = object
    api_star.Star = _Star
    core = types.ModuleType("astrbot.core")
    utils = types.ModuleType("astrbot.core.utils")
    paths = types.ModuleType("astrbot.core.utils.astrbot_path")
    paths.get_astrbot_data_path = tempfile.gettempdir

    modules = {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": api_event,
        "astrbot.api.message_components": api_components,
        "astrbot.api.star": api_star,
        "astrbot.core": core,
        "astrbot.core.utils": utils,
        "astrbot.core.utils.astrbot_path": paths,
    }
    sys.modules.update(modules)

    package = types.ModuleType("finance_plugin_test")
    package.__path__ = [str(ROOT)]
    sys.modules["finance_plugin_test"] = package
    spec = importlib.util.spec_from_file_location(
        "finance_plugin_test.main", ROOT / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PLUGIN = _load_plugin_module()


class _Context:
    def __init__(self, decision=None):
        self.decision = decision

    async def get_current_chat_provider_id(self, **_kwargs):
        return "test-provider"

    async def llm_generate(self, **_kwargs):
        return types.SimpleNamespace(
            completion_text=json.dumps(self.decision, ensure_ascii=False)
        )


class _Event:
    def __init__(self, message):
        self.message_str = message
        self.unified_msg_origin = "test:friend:alice"
        self.stopped = False

    def get_platform_id(self):
        return "test-bot"

    def get_sender_id(self):
        return "alice"

    def get_platform_name(self):
        return "aiocqhttp"

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return text

    def image_result(self, image):
        return ("image", image)

    def chain_result(self, chain):
        return ("chain", chain)


async def _collect(generator):
    return [item async for item in generator]


class PluginTests(unittest.TestCase):
    def _plugin(self, directory, decision=None):
        plugin = PLUGIN.FinancePlugin(_Context(decision), {"auto_analyze": True})
        plugin.data_dir = Path(directory)
        plugin.ledger = PLUGIN.LedgerStore(Path(directory) / "ledger.json")
        return plugin

    def test_manual_command_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = self._plugin(directory)
            event = _Event("/记账 支出 25 午饭")
            replies = asyncio.run(_collect(plugin.add_record(event)))

            self.assertTrue(event.stopped)
            self.assertIn("已记账", replies[0])
            records = plugin.ledger.list_records("test-bot:alice")
            self.assertEqual(records[0].note, "午饭")

    def test_llm_record_decision_end_to_end(self):
        decision = {
            "should_record": True,
            "should_list": False,
            "transactions": [
                {
                    "timestamp": "2026-08-08 12:30",
                    "kind": "expense",
                    "amount": "18.50",
                    "note": "水果",
                }
            ],
            "period": {"start": "", "end": "", "label": ""},
        }
        with tempfile.TemporaryDirectory() as directory:
            plugin = self._plugin(directory, decision)
            event = _Event("刚才买水果花了 18.5 元")
            replies = asyncio.run(_collect(plugin.analyze_message(event)))

            self.assertTrue(event.stopped)
            self.assertIn("已记录 1 笔", replies[0])
            records = plugin.ledger.list_records("test-bot:alice")
            self.assertEqual(records[0].timestamp, "2026-08-08 12:30")

    def test_llm_ignore_does_not_intercept(self):
        decision = {
            "should_record": False,
            "should_list": False,
            "transactions": [],
            "period": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            plugin = self._plugin(directory, decision)
            event = _Event("你好")
            replies = asyncio.run(_collect(plugin.analyze_message(event)))

            self.assertFalse(event.stopped)
            self.assertEqual(replies, [])

    def test_llm_list_decision_uses_requested_period(self):
        decision = {
            "should_record": False,
            "should_list": True,
            "transactions": [],
            "period": {
                "start": "2026-08-01 00:00",
                "end": "2026-09-01 00:00",
                "label": "2026 年 8 月",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            plugin = self._plugin(directory, decision)
            plugin.ledger.add_many(
                "test-bot:alice",
                [
                    PLUGIN.make_transaction(
                        kind="expense",
                        amount="8",
                        note="八月账目",
                        timestamp="2026-08-08 08:00",
                    ),
                    PLUGIN.make_transaction(
                        kind="expense",
                        amount="7",
                        note="七月账目",
                        timestamp="2026-07-07 07:00",
                    ),
                ],
            )
            event = _Event("列出八月的记账目录")
            replies = asyncio.run(_collect(plugin.analyze_message(event)))

            self.assertTrue(event.stopped)
            self.assertIn("八月账目", replies[0])
            self.assertNotIn("七月账目", replies[0])
            self.assertIn("支出合计：¥8.00", replies[0])

    def test_llonebot_month_image_export(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = self._plugin(directory)
            plugin.ledger.add_many(
                "test-bot:alice",
                [
                    PLUGIN.make_transaction(
                        kind="expense",
                        amount="28.50",
                        note="午饭",
                        timestamp="2026-08-08 12:00",
                    )
                ],
            )
            event = _Event("/导出账单 2026-08 图片")
            replies = asyncio.run(_collect(plugin.export_month(event)))

            self.assertTrue(event.stopped)
            self.assertIn("支出：1 笔，共 ¥28.50", replies[0])
            self.assertEqual(replies[1][0], "image")

    def test_llonebot_month_csv_export(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = self._plugin(directory)
            event = _Event("/导出账单 2026-08 文件")
            replies = asyncio.run(_collect(plugin.export_month(event)))

            self.assertEqual(replies[1][0], "chain")
            file_component = replies[1][1][0]
            self.assertTrue(Path(file_component.file).exists())
            self.assertEqual(file_component.name, "finance_2026-08.csv")

    def test_llm_can_request_month_export(self):
        decision = {
            "should_record": False,
            "should_list": False,
            "should_export": True,
            "transactions": [],
            "period": {},
            "export": {"month": "2026-08", "format": "image"},
        }
        with tempfile.TemporaryDirectory() as directory:
            plugin = self._plugin(directory, decision)
            event = _Event("把八月账单按周导出成图片")
            replies = asyncio.run(_collect(plugin.analyze_message(event)))

            self.assertTrue(event.stopped)
            self.assertIn("2026-08 月度收支摘要", replies[0])
            self.assertEqual(replies[1][0], "image")

    def test_image_failure_falls_back_to_csv(self):
        async def fail_render(*_args, **_kwargs):
            raise RuntimeError("renderer unavailable")

        with tempfile.TemporaryDirectory() as directory:
            plugin = self._plugin(directory)
            plugin.html_render = fail_render
            event = _Event("/导出账单 2026-08 图片")
            replies = asyncio.run(_collect(plugin.export_month(event)))

            self.assertIn("已自动改为 CSV 文件", replies[0])
            self.assertEqual(replies[1][0], "chain")

    def test_remove_today_and_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = self._plugin(directory)
            now = datetime.now().astimezone().replace(tzinfo=None)
            plugin.ledger.add_many(
                "test-bot:alice",
                [
                    PLUGIN.make_transaction(
                        kind="expense",
                        amount="12",
                        note="今天消费",
                        timestamp=now.strftime("%Y-%m-%d %H:%M"),
                    )
                ],
            )
            remove_event = _Event("/撤销记账 当天")
            remove_replies = asyncio.run(
                _collect(plugin.undo_record(remove_event))
            )

            self.assertIn("已撤销当天记录 1 笔", remove_replies[0])
            self.assertEqual(plugin.ledger.list_records("test-bot:alice"), [])

            restore_event = _Event("/恢复撤销")
            restore_replies = asyncio.run(
                _collect(plugin.restore_records(restore_event))
            )
            self.assertIn("已恢复当天记录 1 笔", restore_replies[0])
            self.assertEqual(len(plugin.ledger.list_records("test-bot:alice")), 1)

    def test_remove_all_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            plugin = self._plugin(directory)
            plugin.ledger.add_many(
                "test-bot:alice",
                [PLUGIN.make_transaction(kind="income", amount="100", note="存入")],
            )
            warning_event = _Event("/撤销记账 全部")
            warning_replies = asyncio.run(
                _collect(plugin.undo_record(warning_event))
            )

            self.assertIn("全部账目", warning_replies[0])
            self.assertEqual(len(plugin.ledger.list_records("test-bot:alice")), 1)

            confirm_event = _Event("/撤销记账 全部 确认")
            confirm_replies = asyncio.run(
                _collect(plugin.undo_record(confirm_event))
            )
            self.assertIn("已撤销全部记录 1 笔", confirm_replies[0])
            self.assertEqual(plugin.ledger.list_records("test-bot:alice"), [])


if __name__ == "__main__":
    unittest.main()
