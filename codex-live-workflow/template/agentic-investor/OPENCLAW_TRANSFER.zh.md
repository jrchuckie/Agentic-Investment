# Agentic Investor OpenClaw 迁移与运行 SOP

本文是把当前这台机器上的 Agentic Investor 迁移到另一台 OpenClaw 机器时的主清单。默认语言为中文，默认模式为 `advisory-only / paper-trade only`，不会读取真实券商账户，也不会提交真实订单。

如果不迁移任何文件、只靠 prompt 从零重建，请先使用 `OPENCLAW_BOOTSTRAP_PROMPTS.zh.md`。其中 Prompt 0 是投资宪法，必须先跑，再生成代码/dashboard/cron。

## 1. 当前系统边界

- 交易模式：只读建议 + 本地 paper trade。
- 真实账户：不读取、不修改。
- 真实订单：不解锁、不提交。
- Paper 执行：只处理用户已批准、且带有触发条件/失效条件/size/有效窗口的本地 paper scenario 或 order intent。
- 数据优先级：OpenBB 优先；公开行情/宏观源其次；moomoo OpenD 只做兜底。
- 默认交付：中文 dashboard、中文复盘、中文操作建议。

## 2. 当前核心工作流

### 美股交易时段，每 15 分钟

任务：`intraday_monitor`

它会刷新：

- market snapshot，包括指数、watchlist、VIX/10Y/USD-CNH 等宏观代理；
- paper portfolio mark-to-market；
- conditional playbook；
- paper fill engine；
- order intents；
- intel monitor 与 social sentiment；
- event radar；
- dashboard snapshot；
- Firebase Firestore 快照。

关键改动：`intraday_monitor` 现在不只是行情刷新，它已经直接运行 `intel_monitor` 和 `social_sentiment_feed`。如果新闻源临时失败，不会把上一轮有效 event radar 清空。

### 北京时间 23:45 睡前校准

任务：`agentic-investment-pre-market-check` 自动化，确定盘中状态、需要用户批准/拒绝的意图、AMD/PLTR 等重点观察、以及每个动作的 Top3 理由。

### 北京时间白天 / 美股盘后复盘

任务链：

1. `intel_monitor`
2. `social_sentiment_feed`
3. `earnings_event_risk`
4. `research_committee`
5. `order_intents`
6. `dashboard_snapshot`
7. `firebase_publish_snapshot`

白天不刷新新的市场行情，只使用昨夜交易时段已经生成的 market snapshot；白天主要刷新新闻、社媒、KOL、议员/基金经理披露、财报和事件风险。

## 3. Event Radar 必须覆盖的新闻类型

以后不要只靠用户手动指出 CRCL/GME 这类事件。流程必须自动检查：

- 单日大涨/大跌；
- 并购、收购、unsolicited bid、hostile bid；
- 监管法案、审批、SEC/DOJ/FDA 等催化；
- 财报 surprise、guidance、revenue/profit miss/beat；
- analyst upgrade/downgrade、price target；
- meme/social crowding 和重大分歧。

即使 ticker 不在原 watchlist，也要作为 `candidate_symbol_hits` 进入 `eventRadar`，并在 dashboard 和复盘里说明对 playbook、paper position、order intent 的影响。

## 4. Dashboard

私有 dashboard 使用：

- Firebase Hosting：静态 dashboard；
- Firebase Auth：只允许指定 UID 登录；
- Firestore：存最新和历史 dashboard snapshot；
- 本地任务：把 `dashboard/data/snapshot.json` 发布到 Firestore。

重要文件：

- `dashboard/index.html`
- `dashboard/app.js`
- `dashboard/styles.css`
- `dashboard/firebase-config.js`
- `dashboard/firebase-client.js`
- `firebase.json`
- `firestore.rules`
- `scripts/publish_dashboard_firestore.py`

当前 dashboard 已包含：

- 账户视图：paper NAV、cash、P&L、持仓；
- 市场脉冲：指数、VIX、10Y、USD/CNH、sector breadth；
- watchlist coverage：价格、趋势、最新新闻/帖子、操作含义；
- 社媒舆情；
- 突发事件雷达；
- 决策队列和批量 prompt；
- 基金经理与议员组合；
- 审计记录。

如果只刷新数据，运行：

```bash
python3 scripts/run_task.py dashboard_snapshot
python3 scripts/run_task.py firebase_publish_snapshot
```

如果改了 dashboard 静态页面，还需要在目标机部署 Hosting：

```bash
npx firebase-tools deploy --only hosting --project agentic-investor-47147
```

## 5. 目标机目录结构

建议放到：

```text
~/.openclaw/workspace/agentic-investor
```

如果要让 Codex/OpenClaw 把它当 skill 发现，再复制或软链接到：

```text
~/.codex/skills/agentic-investor
```

二选一即可；如果 skill 目录只是软链接到 workspace 目录，后续更新更简单。

## 6. 目标机首次安装

进入项目目录：

```bash
cd ~/.openclaw/workspace/agentic-investor
```

创建虚拟环境，推荐但不是强制：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

安装 OpenBB：

```bash
chmod +x scripts/install_openbb.sh
PYTHON_BIN=python bash scripts/install_openbb.sh
```

如果是 Windows 目标机：

```powershell
powershell -ExecutionPolicy Bypass -File ".\scripts\install_openbb.ps1"
```

Smoke test：

```bash
python scripts/run_task.py openbb_smoke
```

`openbb_available: true` 即可继续；`status: WARN` 通常只是个别 provider 没拉全。

## 7. 私密配置

复制样例：

```bash
cp .env.example .env
```

在 `.env` 里填入：

```text
FIREBASE_PROJECT_ID=agentic-investor-47147
FIREBASE_SERVICE_ACCOUNT_EMAIL=firebase-adminsdk-fbsvc@agentic-investor-47147.iam.gserviceaccount.com
FIREBASE_SERVICE_ACCOUNT=/home/<you>/.agentic-investor-secrets/agentic-investor-47147-firebase-adminsdk.json
FIREBASE_USER_UID=j20TtbKlLXVR22GAxNqMmuDAwxw1
```

把 service account JSON 放在 repo 外部，例如：

```text
/home/<you>/.agentic-investor-secrets/agentic-investor-47147-firebase-adminsdk.json
```

不要把 service account JSON、`.env`、券商密码放进 repo。

## 8. Firebase Web 配置

`dashboard/firebase-config.js` 是浏览器端公开配置，不是 service account key。它需要跟随项目一起迁移，用于 Firebase Auth 和读取 Firestore。

当前配置已启用：

- projectId: `agentic-investor-47147`
- userUid: `j20TtbKlLXVR22GAxNqMmuDAwxw1`
- autoLogin: `true`

如果目标机重新生成或覆盖文件，按 `dashboard/firebase-config.example.js` 创建，并填入当前 Firebase Web App config。

## 9. 安装 cron

确认 `agentic-investor-cron.txt` 里的：

```text
PYTHON_BIN=/usr/bin/python3
AGENTIC_INVESTOR_HOME=$HOME/.openclaw/workspace/agentic-investor
```

如果使用虚拟环境，把 `PYTHON_BIN` 改为：

```text
PYTHON_BIN=$HOME/.openclaw/workspace/agentic-investor/.venv/bin/python
```

安装：

```bash
crontab agentic-investor-cron.txt
crontab -l
```

当前 cron 文件是迁移到 OpenClaw 机器后的 source of truth。Codex Desktop 里的 app automations 已经同步过，但它们属于当前机器/当前 App；搬到另一台长期运行机器时，以这个 crontab 文件为准。

时间安排：

- 21:30、21:45：美股开盘初段 monitor；
- 22:00-23:45：每 15 分钟盘中 monitor；
- 次日 00:00-03:45：每 15 分钟后半段 monitor；
- 23:50-23:56：睡前 research/order/dashboard/Firebase 刷新；
- 10:00-10:22：盘后新闻/舆情/事件/财报/投委/订单/看板/Firebase 复盘链；
- 周六：基金经理持仓、backtest v2、周度复盘；
- 每月/每季：议员组合披露追踪。

日志在：

```text
/tmp/agentic-investor-intraday.log
/tmp/agentic-investor-intel.log
/tmp/agentic-investor-social.log
/tmp/agentic-investor-dashboard-snapshot.log
/tmp/agentic-investor-firebase-publish.log
```

## 10. 手动验收清单

按顺序运行：

```bash
python scripts/run_task.py openbb_smoke
python scripts/run_task.py intel_monitor
python scripts/run_task.py social_sentiment_feed
python scripts/run_task.py earnings_event_risk
python scripts/run_task.py intraday_monitor
python scripts/run_task.py dashboard_snapshot
python scripts/run_task.py firebase_publish_snapshot
```

验收标准：

- OpenBB smoke: `openbb_available: true`
- `intraday_monitor`: `market_status: PASS`
- `intel_monitor`: 输出 highlights；如果有突发事件，应有 `event_radar`
- `social_sentiment_feed`: `status: PASS` 或 `WARN`
- `dashboard_snapshot`: 写入 `dashboard/data/snapshot.json`
- `firebase_publish_snapshot`: `status: published`

## 11. 常见故障

### Firebase Hosting 页面没更新

Firestore 数据更新和 Hosting 静态页面部署是两件事。数据更新只需要：

```bash
python scripts/run_task.py firebase_publish_snapshot
```

页面代码更新需要：

```bash
npx firebase-tools deploy --only hosting --project agentic-investor-47147
```

### OpenBB import 问题

不要用 `pip install --target vendor/python openbb`。正确做法是在实际运行任务的 Python 环境里安装 OpenBB。`vendor/python` 只是补充路径，不能让残缺 namespace 包遮住真正的 OpenBB。

### 目标机休眠

cron 只会在机器在线时执行。如果目标机休眠，15 分钟 monitor 不会跑。把 OpenClaw 放在常开机器、Mac mini、云主机或不休眠的工作站上。

## 12. 迁移完成后的第一晚

第一晚保持低风险：

- 只允许 paper trade；
- 不新增真实券商连接；
- 观察 cron 是否每 15 分钟写日志；
- 观察 dashboard 是否更新；
- 观察 `eventRadar` 是否保留具体来源链接；
- 第二天白天看盘后复盘，再决定是否调整 playbook。
