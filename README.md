# AstrBot 智能记账插件

一个面向 AstrBot 的自然语言记账插件。它会调用当前会话的 LLM，判断用户是在新增账目、查询记账目录，还是普通聊天；只有确认属于记账操作时才会接管消息。

每笔账目包含：

- 年、月、日、时、分
- 收入或支出
- 金额
- 备注

账本按“平台 + 用户”隔离，保存在 AstrBot 的 `data/plugin_data/astrbot_plugin_finance/ledger.json`，插件升级或重装不会覆盖数据。金额使用十进制定点数计算，目录会自动显示收入合计、支出合计和结余。

## 使用方法

自然语言示例：

```text
今天中午吃饭花了 25 元
昨天工资到账 5000 元
刚才买水果 18.5，再记一笔打车 26 元
给我看看本月的记账目录
统计一下今年的总收支
把本月账单按周导出成图片
```

明确指令：

```text
/记账 支出 25.5 午饭
/记账 收入 5000 工资
/账单 今天
/账单 本月
/账单 2026-08
/账单 全部
/总额 本月
/导出账单 2026-08 图片
/导出账单 2026-08 文件
/撤销记账
/记账帮助
```

## 配置

在 AstrBot WebUI 的插件配置中可以：

- 开关普通消息的 LLM 自动分析；
- 指定分析模型（留空则使用当前会话模型）；
- 设置目录最多显示多少条记录；
- 设置 LLOneBot 月度账单的默认导出格式；
- 设置自动分析的消息长度上限。

## LLOneBot 月度导出

LLOneBot 在 AstrBot 中对应 `aiocqhttp`（OneBot v11）适配器。插件可以把一个自然月的账目按周一至周日分组，并输出：

- 总记录数、存入笔数、支出笔数；
- 存入总额、支出总额和结余；
- 平均单笔支出、有支出日均、最大单笔支出；
- 每周存入/支出笔数、金额和逐笔明细。

图片适合直接在 QQ 中阅读；CSV 使用 UTF-8 BOM，可直接用 Windows Excel 打开。导出文件只包含当前 QQ 用户的数据，不会发送保存所有用户账目的原始 `ledger.json`。

生成的 CSV 位于 `data/plugin_data/astrbot_plugin_finance/exports/<用户哈希>/`，同一用户、同一月份再次导出时会更新对应文件。

如果 AstrBot 与 LLOneBot 不在同一台主机上，图片发送仍会转为 Base64；CSV 文件发送则建议在 AstrBot 中配置可被 LLOneBot 访问的 `callback_api_base`。

若没有可用 LLM，明确指令仍然可以正常使用。LLM 分析失败时插件不会拦截消息，AstrBot 会继续原有对话流程。

## 安装

将仓库放入 AstrBot 的 `data/plugins/astrbot_plugin_finance`，然后在 WebUI 中重载插件。插件要求 AstrBot `>= 4.5.7, < 5`。

开发规范参考 [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)。
