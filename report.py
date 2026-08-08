"""月度账单的按周统计与 CSV 导出。"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

try:
    from .ledger import LedgerError, Transaction, parse_timestamp
except ImportError:  # 允许单独运行领域层测试。
    from ledger import LedgerError, Transaction, parse_timestamp


MONTHLY_REPORT_HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0; padding: 34px; width: 960px;
      background: #f4f7fb; color: #172033;
      font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    }
    .header {
      padding: 30px; border-radius: 24px;
      color: white; background: linear-gradient(135deg, #275efe, #7239ea);
      box-shadow: 0 12px 30px rgba(55, 65, 160, .22);
    }
    .title { margin: 0; font-size: 38px; font-weight: 800; }
    .subtitle { margin-top: 8px; font-size: 17px; opacity: .86; }
    .metrics {
      display: grid; grid-template-columns: repeat(3, 1fr);
      gap: 14px; margin: 20px 0;
    }
    .metric {
      padding: 19px; background: white; border-radius: 17px;
      box-shadow: 0 5px 18px rgba(42, 54, 92, .08);
    }
    .metric .name { color: #75809a; font-size: 15px; }
    .metric .value { margin-top: 7px; font-size: 25px; font-weight: 760; }
    .income { color: #168b65; }
    .expense { color: #dd4d5c; }
    .balance { color: #305bd8; }
    .week {
      margin-top: 18px; padding: 22px; background: white; border-radius: 19px;
      box-shadow: 0 5px 18px rgba(42, 54, 92, .07);
    }
    .week-head { display: flex; justify-content: space-between; align-items: end; }
    .week-title { font-size: 22px; font-weight: 760; }
    .week-range { color: #7a849a; font-size: 14px; }
    .week-summary { margin-top: 7px; color: #68748e; font-size: 14px; }
    table { width: 100%; border-collapse: collapse; margin-top: 14px; }
    th { color: #8992a7; font-size: 13px; font-weight: 600; text-align: left; }
    th, td { padding: 11px 8px; border-bottom: 1px solid #eef1f6; }
    td { font-size: 15px; }
    th:last-child, td:last-child { text-align: right; }
    tr:last-child td { border-bottom: 0; }
    .tag {
      display: inline-block; min-width: 48px; padding: 4px 8px;
      border-radius: 8px; text-align: center; font-size: 13px;
    }
    .tag-income { color: #117557; background: #e7f7f1; }
    .tag-expense { color: #c33d4b; background: #ffedf0; }
    .empty { padding: 18px 0 4px; color: #9aa2b3; text-align: center; }
    .footer { margin-top: 20px; color: #929bad; text-align: center; font-size: 13px; }
  </style>
</head>
<body>
  <section class="header">
    <h1 class="title">{{ month }} 月度收支报告</h1>
    <div class="subtitle">按自然周（周一至周日）汇总 · 仅包含当前 QQ 用户账目</div>
  </section>

  <section class="metrics">
    <div class="metric"><div class="name">存入笔数</div><div class="value income">{{ income_count }} 笔</div></div>
    <div class="metric"><div class="name">支出笔数</div><div class="value expense">{{ expense_count }} 笔</div></div>
    <div class="metric"><div class="name">总记录</div><div class="value">{{ record_count }} 笔</div></div>
    <div class="metric"><div class="name">存入总额</div><div class="value income">¥{{ income_total }}</div></div>
    <div class="metric"><div class="name">支出总额</div><div class="value expense">¥{{ expense_total }}</div></div>
    <div class="metric"><div class="name">本月结余</div><div class="value balance">¥{{ balance }}</div></div>
    <div class="metric"><div class="name">平均单笔支出</div><div class="value">¥{{ average_expense }}</div></div>
    <div class="metric"><div class="name">有支出日均</div><div class="value">¥{{ active_day_average }}</div></div>
    <div class="metric"><div class="name">最大单笔支出</div><div class="value">¥{{ largest_expense }}</div></div>
  </section>

  {% for week in weeks %}
  <section class="week">
    <div class="week-head">
      <div class="week-title">第 {{ week.index }} 周</div>
      <div class="week-range">{{ week.range }}</div>
    </div>
    <div class="week-summary">
      存入 {{ week.income_count }} 笔 / ¥{{ week.income_total }}　
      支出 {{ week.expense_count }} 笔 / ¥{{ week.expense_total }}　
      周结余 ¥{{ week.balance }}
    </div>
    {% if week.records %}
    <table>
      <thead><tr><th>时间</th><th>类型</th><th>备注</th><th>金额</th></tr></thead>
      <tbody>
      {% for item in week.records %}
        <tr>
          <td>{{ item.time }}</td>
          <td><span class="tag tag-{{ item.kind_class }}">{{ item.kind }}</span></td>
          <td>{{ item.note | e }}</td>
          <td class="{{ item.kind_class }}">{{ item.sign }}¥{{ item.amount }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    {% else %}
    <div class="empty">本周暂无收支记录</div>
    {% endif %}
  </section>
  {% endfor %}
  <div class="footer">AstrBot 智能记账 · 生成时间 {{ generated_at }}</div>
</body>
</html>
"""


def money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def csv_safe_text(value: str) -> str:
    """防止备注在 Excel 中被解释为公式。"""
    text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def month_bounds(month: str) -> tuple[datetime, datetime]:
    try:
        start = datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise LedgerError("导出月份应为 YYYY-MM，例如 2026-08") from exc
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def parse_export_request(
    payload: str,
    now: datetime,
    default_format: str = "图片",
) -> tuple[str, str]:
    """解析“[YYYY-MM|本月] [图片|文件]”，参数顺序不限。"""
    month = now.strftime("%Y-%m")
    normalized_default = str(default_format).strip().lower()
    mode = "file" if normalized_default in {"文件", "file", "csv"} else "image"
    seen_month = False
    for token in payload.strip().split():
        lowered = token.lower()
        if token in {"本月", "这个月"}:
            continue
        if lowered in {"图片", "图", "image", "img"}:
            mode = "image"
            continue
        if lowered in {"文件", "csv", "file"}:
            mode = "file"
            continue
        if re.fullmatch(r"\d{4}-\d{2}", token) and not seen_month:
            month_bounds(token)
            month = token
            seen_month = True
            continue
        raise LedgerError("格式应为：/导出账单 [YYYY-MM] [图片|文件]")
    return month, mode


@dataclass(frozen=True)
class WeeklyReport:
    index: int
    start: datetime
    end: datetime
    records: tuple[Transaction, ...]

    @property
    def income_records(self) -> tuple[Transaction, ...]:
        return tuple(item for item in self.records if item.kind == "income")

    @property
    def expense_records(self) -> tuple[Transaction, ...]:
        return tuple(item for item in self.records if item.kind == "expense")

    @property
    def income_total(self) -> Decimal:
        return sum((item.amount for item in self.income_records), Decimal("0"))

    @property
    def expense_total(self) -> Decimal:
        return sum((item.amount for item in self.expense_records), Decimal("0"))


@dataclass(frozen=True)
class MonthlyReport:
    month: str
    start: datetime
    end: datetime
    records: tuple[Transaction, ...]
    weeks: tuple[WeeklyReport, ...]

    @property
    def income_records(self) -> tuple[Transaction, ...]:
        return tuple(item for item in self.records if item.kind == "income")

    @property
    def expense_records(self) -> tuple[Transaction, ...]:
        return tuple(item for item in self.records if item.kind == "expense")

    @property
    def income_total(self) -> Decimal:
        return sum((item.amount for item in self.income_records), Decimal("0"))

    @property
    def expense_total(self) -> Decimal:
        return sum((item.amount for item in self.expense_records), Decimal("0"))

    @property
    def balance(self) -> Decimal:
        return self.income_total - self.expense_total

    @property
    def average_expense(self) -> Decimal:
        count = len(self.expense_records)
        return self.expense_total / count if count else Decimal("0")

    @property
    def active_expense_days(self) -> int:
        return len({item.timestamp[:10] for item in self.expense_records})

    @property
    def active_day_average(self) -> Decimal:
        days = self.active_expense_days
        return self.expense_total / days if days else Decimal("0")

    @property
    def largest_expense(self) -> Transaction | None:
        return max(self.expense_records, key=lambda item: item.amount, default=None)

    def template_data(self, generated_at: datetime) -> dict:
        largest = self.largest_expense
        weeks = []
        for week in self.weeks:
            items = []
            for item in week.records:
                is_income = item.kind == "income"
                items.append(
                    {
                        "time": item.timestamp[5:],
                        "kind": "存入" if is_income else "支出",
                        "kind_class": "income" if is_income else "expense",
                        "sign": "+" if is_income else "-",
                        "amount": money(item.amount),
                        "note": item.note,
                    }
                )
            weeks.append(
                {
                    "index": week.index,
                    "range": f"{week.start:%m-%d} 至 {(week.end - timedelta(days=1)):%m-%d}",
                    "income_count": len(week.income_records),
                    "expense_count": len(week.expense_records),
                    "income_total": money(week.income_total),
                    "expense_total": money(week.expense_total),
                    "balance": money(week.income_total - week.expense_total),
                    "records": items,
                }
            )
        return {
            "month": self.month,
            "record_count": len(self.records),
            "income_count": len(self.income_records),
            "expense_count": len(self.expense_records),
            "income_total": money(self.income_total),
            "expense_total": money(self.expense_total),
            "balance": money(self.balance),
            "average_expense": money(self.average_expense),
            "active_day_average": money(self.active_day_average),
            "largest_expense": money(largest.amount if largest else Decimal("0")),
            "weeks": weeks,
            "generated_at": generated_at.strftime("%Y-%m-%d %H:%M"),
        }


def build_monthly_report(
    records: list[Transaction], month: str
) -> MonthlyReport:
    start, end = month_bounds(month)
    in_month = tuple(
        item
        for item in records
        if start <= parse_timestamp(item.timestamp) < end
    )
    weeks: list[WeeklyReport] = []
    cursor = start
    index = 1
    while cursor < end:
        next_monday = cursor + timedelta(days=7 - cursor.weekday())
        week_end = min(next_monday, end)
        week_records = tuple(
            item
            for item in in_month
            if cursor <= parse_timestamp(item.timestamp) < week_end
        )
        weeks.append(WeeklyReport(index, cursor, week_end, week_records))
        cursor = week_end
        index += 1
    return MonthlyReport(month, start, end, in_month, tuple(weeks))


def write_report_csv(report: MonthlyReport, path: Path) -> Path:
    """生成带 UTF-8 BOM 的 CSV，便于 Windows Excel 直接打开。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    largest = report.largest_expense
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerows(
                [
                    ["账单月份", report.month],
                    ["总记录数", len(report.records)],
                    ["存入笔数", len(report.income_records)],
                    ["支出笔数", len(report.expense_records)],
                    ["存入总额", money(report.income_total)],
                    ["支出总额", money(report.expense_total)],
                    ["结余", money(report.balance)],
                    ["平均单笔支出", money(report.average_expense)],
                    ["有支出日均", money(report.active_day_average)],
                    ["最大单笔支出", money(largest.amount if largest else Decimal("0"))],
                    [],
                    ["自然周", "时间", "类型", "金额", "备注"],
                ]
            )
            for week in report.weeks:
                week_name = (
                    f"第{week.index}周 "
                    f"({week.start:%m-%d}~{(week.end - timedelta(days=1)):%m-%d})"
                )
                for item in week.records:
                    writer.writerow(
                        [
                            week_name,
                            item.timestamp,
                            "存入" if item.kind == "income" else "支出",
                            money(item.amount),
                            csv_safe_text(item.note),
                        ]
                    )
        os.replace(temporary, path)
    except OSError as exc:
        raise LedgerError("导出文件无法写入") from exc
    return path
