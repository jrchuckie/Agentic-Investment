# OpenClaw 从零重建 Agentic Investor 的 Prompt 顺序

如果不迁移任何文件，只靠 prompt 在另一台 OpenClaw 机器上重建，请按下面顺序执行。不要跳过 Prompt 0；它定义投资目标、理念和风险边界。

## Prompt 0：投资宪法与用户目标

```text
请先为我创建 Agentic Investor 的核心投资上下文，并把它写入：
- investor-profile.md
- STRATEGY.md
- rule-engine.json 的核心目标/风控字段
- SKILL.md 的 Current Operating Context

这是一个 advisory-only / paper-trade first 的美股投资系统，默认中文输出，不读取真实券商账户，不提交真实券商订单。

核心目标：
1. 目标资金：2028年5月前准备 300,000 USD。
2. 用途：美国代孕/家庭计划，是硬 deadline，不是泛泛的财务自由目标。
3. 时间窗口：2026年5月到2028年5月，约24个月。
4. 每月新增投入：约 3,000 USD。
5. 年终奖注入：2026年约 23,000 USD，2027年约 42,000 USD。
6. 自有资金路径大约 135,000 USD；投资端需要补足约 65,000 USD，所以目标收益较激进，但不是无约束赌博。
7. 小红书期权/公司股权是最后兜底资产，原则上尽量不动用。

投资理念：
1. 确定性优先：宁可少赚一点，也要保护刚性目标。
2. 主动管理：不希望默认买 ETF，被动收益不够；ETF 可作为基准/风险观察，不是核心持仓。
3. AI + Tech 是主线，尤其关注 AI infrastructure、GPU、cloud、大模型、网络安全、AI physical infrastructure。
4. 个股为主，选择有明确基本面、技术面、财务或事件催化的标的。
5. 期权是工具不是赌具：优先 covered call / cash-secured put / defined-risk spread；不允许无理由裸赌 call/put。
6. 社媒舆情只能作为最多 15% 的 confidence/crowding overlay，不能覆盖宏观、财报、仓位、价格和人工批准。
7. 议员组合和基金经理持仓是 idea source，不是自动买入信号。
8. 重大新闻和突发事件必须进入 event radar；我不能每天手动告诉你所有 CRCL/GME 这类事件。

风险偏好：
1. 总体风格：积极/激进，但必须有风控。
2. 最大回撤：-20% 触发降风险/减半仓位。
3. 单日亏损：-3% 后停止当日新开仓。
4. 单票上限：20% 总仓位。
5. 现金底线：至少 10%，除非经过明确 playbook 允许。
6. 期权连亏 3 次暂停 30 天。
7. 财报 blackout 期间避免新开裸多 call/put，除非已有明确、defined-risk、用户预批准 scenario。

初始重点股票池：
- Core：NVDA, GOOG, AMZN, MSFT
- Satellite：PANW, CRWD, PLTR, META
- 重点观察：AMD, PLTR
- AI physical infrastructure / Situation Awareness Fund 相关：BE, CRWV, CORZ
- 新增事件雷达观察：CRCL, COIN, GME, EBAY
- 基准/风险观察：QQQ, SPY, SMH, SOXX, VIX, 10Y yield, USD/CNH

工作流偏好：
1. 北京时间 23:45 给我睡前盘中校准，形成可批量批准/拒绝的 paper playbook。
2. 美股开盘期间每15分钟刷新 market/paper/intel/social/event radar/dashboard。
3. 中国白天不要刷新新的美股行情，只刷新新闻、舆情、KOL、基金经理/议员披露、财报日历和复盘。
4. 每个交易建议必须给 Top3 最强理由，可来自基本面、技术面、财务数据、新闻/社媒、事件风险、宏观。
5. dashboard 必须可实时看到：账户、paper P&L、持仓、市场、watchlist、突发事件、新闻/帖子链接、决策队列、批量 prompt。

请把这些上下文作为系统的投资宪法。后续生成代码、cron、dashboard、order intent、research committee 时都必须遵守。
```

## Prompt 1：重建核心项目

```text
请在 ~/.openclaw/workspace/agentic-investor 从零创建 Agentic Investor 项目。

目标：美股 advisory-only / paper-trade 投资工作流，不读取真实券商账户，不提交真实订单。

请创建：
- SKILL.md
- state.json
- rule-engine.json
- watchlist.json
- intelligence-sources.json
- event-risk.json
- fund-managers.json
- congress-traders.json
- market-data-sources.json
- trade-log.json
- scripts/agentic_investor_common.py
- scripts/run_task.py
- scripts/install_openbb.sh
- scripts/cron_entry.sh

必须实现这些 task：
openbb_smoke, market_snapshot, intel_monitor, social_sentiment_feed, earnings_event_risk, paper_fill_engine, order_intents, research_committee, dashboard_snapshot, firebase_publish_snapshot, intraday_monitor。

重要逻辑：
1. OpenBB 优先，公开行情/宏观源其次，moomoo OpenD 只做兜底。
2. intraday_monitor 是 15 分钟核心任务：market snapshot + paper MTM + playbook + paper fills + intel/social/event radar + order intents + dashboard snapshot + Firestore publish。
3. event radar 必须覆盖：大涨大跌、并购/收购、监管、财报 surprise/guidance、analyst upgrade/downgrade、meme/social crowding；即使 ticker 不在 watchlist，也进入 candidate_symbol_hits。
4. 默认中文输出。
5. 所有真实交易能力关闭。
```

## Prompt 2：重建 dashboard

```text
请继续在 ~/.openclaw/workspace/agentic-investor 创建私有 Firebase dashboard。

创建：
- dashboard/index.html
- dashboard/app.js
- dashboard/styles.css
- dashboard/firebase-client.js
- dashboard/firebase-config.example.js
- firebase.json
- firestore.rules
- firestore.indexes.json
- scripts/publish_dashboard_firestore.py

dashboard 必须中文，包含：
账户 NAV/cash/P&L/持仓、市场脉冲、VIX/10Y/USD-CNH、sector breadth、watchlist coverage、新闻/社媒舆情、突发事件雷达、决策队列、批量 prompt、基金经理、议员组合、审计记录。

Firestore 路径：
users/{uid}/snapshots/current
users/{uid}/snapshots/{generatedAt}

浏览器端不要包含 service account key。
```

## Prompt 3：配置 Firebase / OpenBB / cron

```text
请创建 .env.example 和 agentic-investor-cron.txt。

.env.example 包含：
FIREBASE_PROJECT_ID=agentic-investor-47147
FIREBASE_SERVICE_ACCOUNT_EMAIL=firebase-adminsdk-fbsvc@agentic-investor-47147.iam.gserviceaccount.com
FIREBASE_SERVICE_ACCOUNT=/home/<you>/.agentic-investor-secrets/agentic-investor-47147-firebase-adminsdk.json
FIREBASE_USER_UID=j20TtbKlLXVR22GAxNqMmuDAwxw1

cron 使用 Asia/Shanghai 时间：
- 美股开盘期间每 15 分钟运行 intraday_monitor
- 北京时间 23:50 后运行 research_committee/order_intents/dashboard_snapshot/firebase_publish_snapshot
- 北京时间 10:00 后运行 intel_monitor/social_sentiment_feed/earnings_event_risk/research_committee/order_intents/dashboard_snapshot/firebase_publish_snapshot
- 周度 fund_holdings_tracker + backtest_v2
- 月度/季度 congress_trades_tracker

所有 cron 必须调用 scripts/cron_entry.sh，不允许 natural-language agent 自动执行真实交易。
```

## Prompt 4：验收和修复

```text
请现在做验收，不要提交真实订单，不要读取真实账户。

依次运行：
python3 scripts/run_task.py openbb_smoke
python3 scripts/run_task.py intel_monitor
python3 scripts/run_task.py social_sentiment_feed
python3 scripts/run_task.py intraday_monitor
python3 scripts/run_task.py dashboard_snapshot
python3 scripts/run_task.py firebase_publish_snapshot

修复所有失败，直到：
- openbb_available: true
- intraday_monitor market_status: PASS 或 WARN 但不中断
- dashboard/data/snapshot.json 存在
- firebase_publish_snapshot status: published
- eventRadar 字段存在
```
