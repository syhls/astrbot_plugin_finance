"""不依赖 AstrBot 的账本领域逻辑与 JSON 持久化。"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M"
VALID_KINDS = {"income", "expense"}
MAX_DELETION_BATCHES = 20


class LedgerError(ValueError):
    """账本数据或用户输入不合法。"""


@dataclass(frozen=True)
class Transaction:
    id: str
    timestamp: str
    kind: str
    amount: Decimal
    note: str

    def to_dict(self) -> dict[str, str]:
        value = asdict(self)
        value["amount"] = format(self.amount, "f")
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Transaction":
        return make_transaction(
            kind=value.get("kind", ""),
            amount=value.get("amount", ""),
            note=value.get("note", ""),
            timestamp=value.get("timestamp", ""),
            transaction_id=value.get("id", ""),
        )


@dataclass(frozen=True)
class DeletionBatch:
    id: str
    deleted_at: str
    scope: str
    records: tuple[Transaction, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "deleted_at": self.deleted_at,
            "scope": self.scope,
            "records": [item.to_dict() for item in self.records],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DeletionBatch":
        raw_records = value.get("records", [])
        if not isinstance(raw_records, list):
            raise LedgerError("撤销记录结构无效")
        if any(not isinstance(item, dict) for item in raw_records):
            raise LedgerError("撤销记录结构无效")
        records = tuple(Transaction.from_dict(item) for item in raw_records)
        return cls(
            id=str(value.get("id", "")),
            deleted_at=str(value.get("deleted_at", "")),
            scope=str(value.get("scope", "")),
            records=records,
        )


def parse_timestamp(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.now().astimezone().replace(tzinfo=None, second=0, microsecond=0)
    normalized = text.replace("T", " ")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise LedgerError("时间格式应为 YYYY-MM-DD HH:MM") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.replace(second=0, microsecond=0)


def _normalize_kind(value: Any) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "收入": "income",
        "income": "income",
        "收": "income",
        "支出": "expense",
        "expense": "expense",
        "消费": "expense",
        "支": "expense",
    }
    kind = mapping.get(text, text)
    if kind not in VALID_KINDS:
        raise LedgerError("类型必须是“收入”或“支出”")
    return kind


def _normalize_amount(value: Any) -> Decimal:
    text = str(value or "").strip().replace(",", "")
    text = text.removeprefix("¥").removeprefix("￥")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise LedgerError("金额必须是有效数字") from exc
    if not amount.is_finite() or amount <= 0:
        raise LedgerError("金额必须大于 0")
    if amount.as_tuple().exponent < -2:
        raise LedgerError("金额最多保留两位小数")
    if amount > Decimal("999999999999.99"):
        raise LedgerError("金额过大")
    return amount


def make_transaction(
    *,
    kind: Any,
    amount: Any,
    note: Any,
    timestamp: Any = "",
    transaction_id: Any = "",
) -> Transaction:
    parsed_time = parse_timestamp(timestamp)
    clean_note = " ".join(str(note or "").strip().split())
    if not clean_note:
        clean_note = "无备注"
    if len(clean_note) > 200:
        clean_note = clean_note[:200]
    clean_id = str(transaction_id or "").strip() or uuid.uuid4().hex
    return Transaction(
        id=clean_id,
        timestamp=parsed_time.strftime(TIMESTAMP_FORMAT),
        kind=_normalize_kind(kind),
        amount=_normalize_amount(amount),
        note=clean_note,
    )


def parse_manual_record(payload: str) -> Transaction:
    """解析“收入/支出 金额 备注”格式的指令参数。"""
    match = re.fullmatch(
        r"\s*(收入|支出|income|expense)\s+[¥￥]?([0-9][0-9,]*(?:\.[0-9]+)?)"
        r"(?:\s+(.+?))?\s*",
        payload,
        flags=re.IGNORECASE,
    )
    if not match:
        raise LedgerError("格式应为：/记账 支出 25.5 午饭")
    return make_transaction(
        kind=match.group(1), amount=match.group(2), note=match.group(3) or "无备注"
    )


def parse_removal_scope(
    payload: str, now: datetime
) -> tuple[str, datetime | None, datetime | None, bool]:
    """解析批量撤销范围，返回范围名、起止时间及是否要求确认。"""
    tokens = payload.strip().split()
    confirmed = "确认" in tokens
    scope_text = "".join(token for token in tokens if token != "确认")
    if scope_text in {"", "最后", "最后一笔", "上一笔"}:
        return "最后一笔", None, None, False
    if scope_text in {"当天", "今天", "今日"}:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return "当天", start, start + timedelta(days=1), False
    if scope_text in {"当周", "本周", "这周"}:
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return "当周", start, start + timedelta(days=7), False
    if scope_text in {"当月", "本月", "这个月"}:
        start, end = _month_bounds(now)
        return "当月", start, end, False
    if scope_text in {"全部", "所有", "all"}:
        return "全部", None, None, confirmed
    raise LedgerError(
        "范围应为最后一笔、当天、当周、当月或全部；撤销全部需追加“确认”"
    )


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def parse_period(
    text: str,
    now: datetime,
    llm_period: dict[str, Any] | None = None,
) -> tuple[datetime | None, datetime | None, str]:
    """把指令或 LLM 的期间转换为左闭右开时间范围。"""
    value = text.strip()
    if not value and llm_period:
        raw_start = str(llm_period.get("start", "")).strip()
        raw_end = str(llm_period.get("end", "")).strip()
        if raw_start and raw_end:
            start = parse_timestamp(raw_start)
            end = parse_timestamp(raw_end)
            if end <= start:
                raise LedgerError("账单结束时间必须晚于开始时间")
            label = " ".join(str(llm_period.get("label", "")).strip().split())
            return start, end, label[:40] or f"{start:%Y-%m-%d} 至 {end:%Y-%m-%d}"
    if value in {"全部", "所有", "all"}:
        return None, None, "全部"
    if value in {"今天", "今日"}:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1), "今天"
    if value in {"今年", "本年"}:
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, start.replace(year=start.year + 1), f"{start.year} 年"
    if value in {"", "本月", "这个月"}:
        start, end = _month_bounds(now)
        return start, end, f"{start:%Y-%m}"
    if re.fullmatch(r"\d{4}-\d{2}", value):
        try:
            start = datetime.strptime(value, "%Y-%m")
        except ValueError as exc:
            raise LedgerError("月份格式无效") from exc
        _, end = _month_bounds(start)
        return start, end, value
    raise LedgerError("范围应为今天、本月、今年、全部或 YYYY-MM")


class LedgerStore:
    """以原子替换方式保存单个 JSON 账本文件。"""

    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 2, "accounts": {}, "trash": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LedgerError("账本文件损坏或无法读取") from exc
        if not isinstance(value, dict) or not isinstance(value.get("accounts"), dict):
            raise LedgerError("账本文件结构无效")
        trash = value.setdefault("trash", {})
        if not isinstance(trash, dict):
            raise LedgerError("撤销记录结构无效")
        value["version"] = 2
        return value

    def _save(self, value: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, self.path)
        except OSError as exc:
            raise LedgerError("账本文件无法写入") from exc

    def add_many(self, account_id: str, transactions: Iterable[Transaction]) -> None:
        items = list(transactions)
        if not items:
            return
        data = self._load()
        account = data["accounts"].setdefault(account_id, [])
        if not isinstance(account, list):
            raise LedgerError("用户账本结构无效")
        account.extend(item.to_dict() for item in items)
        self._save(data)

    def list_records(
        self,
        account_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Transaction]:
        data = self._load()
        raw_records = data["accounts"].get(account_id, [])
        if not isinstance(raw_records, list):
            raise LedgerError("用户账本结构无效")
        records: list[Transaction] = []
        for raw in raw_records:
            if not isinstance(raw, dict):
                raise LedgerError("账目记录结构无效")
            item = Transaction.from_dict(raw)
            timestamp = parse_timestamp(item.timestamp)
            if start is not None and timestamp < start:
                continue
            if end is not None and timestamp >= end:
                continue
            records.append(item)
        records.sort(key=lambda item: (item.timestamp, item.id))
        return records

    @staticmethod
    def _append_deletion_batch(
        data: dict[str, Any],
        account_id: str,
        records: list[Transaction],
        scope: str,
    ) -> DeletionBatch:
        batch = DeletionBatch(
            id=uuid.uuid4().hex,
            deleted_at=datetime.now()
            .astimezone()
            .replace(tzinfo=None)
            .strftime(TIMESTAMP_FORMAT),
            scope=scope,
            records=tuple(records),
        )
        trash = data.setdefault("trash", {})
        if not isinstance(trash, dict):
            raise LedgerError("撤销记录结构无效")
        batches = trash.setdefault(account_id, [])
        if not isinstance(batches, list):
            raise LedgerError("用户撤销记录结构无效")
        batches.append(batch.to_dict())
        if len(batches) > MAX_DELETION_BATCHES:
            del batches[:-MAX_DELETION_BATCHES]
        return batch

    def remove_last(self, account_id: str) -> Transaction | None:
        data = self._load()
        raw_records = data["accounts"].get(account_id, [])
        if not isinstance(raw_records, list):
            raise LedgerError("用户账本结构无效")
        if not raw_records:
            return None
        raw = raw_records.pop()
        if not isinstance(raw, dict):
            raise LedgerError("账目记录结构无效")
        removed = Transaction.from_dict(raw)
        self._append_deletion_batch(data, account_id, [removed], "最后一笔")
        self._save(data)
        return removed

    def remove_range(
        self,
        account_id: str,
        *,
        start: datetime | None,
        end: datetime | None,
        scope: str,
    ) -> DeletionBatch | None:
        """撤销范围内记录，并保留最近的撤销批次用于恢复。"""
        data = self._load()
        raw_records = data["accounts"].get(account_id, [])
        if not isinstance(raw_records, list):
            raise LedgerError("用户账本结构无效")
        kept: list[dict[str, Any]] = []
        removed: list[Transaction] = []
        for raw in raw_records:
            if not isinstance(raw, dict):
                raise LedgerError("账目记录结构无效")
            item = Transaction.from_dict(raw)
            timestamp = parse_timestamp(item.timestamp)
            in_range = (start is None or timestamp >= start) and (
                end is None or timestamp < end
            )
            if in_range:
                removed.append(item)
            else:
                kept.append(raw)
        if not removed:
            return None
        data["accounts"][account_id] = kept
        batch = self._append_deletion_batch(data, account_id, removed, scope)
        self._save(data)
        return batch

    def restore_last_batch(self, account_id: str) -> DeletionBatch | None:
        """恢复当前用户最近一次撤销的记录。"""
        data = self._load()
        trash = data.get("trash", {})
        if not isinstance(trash, dict):
            raise LedgerError("撤销记录结构无效")
        batches = trash.get(account_id, [])
        if not isinstance(batches, list):
            raise LedgerError("用户撤销记录结构无效")
        if not batches:
            return None
        raw_batch = batches[-1]
        if not isinstance(raw_batch, dict):
            raise LedgerError("撤销批次结构无效")
        batch = DeletionBatch.from_dict(raw_batch)
        active = data["accounts"].setdefault(account_id, [])
        if not isinstance(active, list):
            raise LedgerError("用户账本结构无效")
        active_ids = {
            str(item.get("id", "")) for item in active if isinstance(item, dict)
        }
        restored = tuple(item for item in batch.records if item.id not in active_ids)
        active.extend(item.to_dict() for item in restored)
        batches.pop()
        restored_batch = DeletionBatch(
            id=batch.id,
            deleted_at=batch.deleted_at,
            scope=batch.scope,
            records=restored,
        )
        self._save(data)
        return restored_batch
