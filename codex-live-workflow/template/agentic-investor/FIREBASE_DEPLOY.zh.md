# Agentic Investor Dashboard 上线说明

## 结论

如果 dashboard 只展示公开、脱敏报告，可以用 GitHub Pages。

如果 dashboard 展示账户净值、paper 持仓、决策队列、交易意图和实时快照，就需要数据库和权限控制。当前推荐路径：

- Firebase Hosting：托管静态 dashboard。
- Firebase Auth：只允许你自己的账号登录读取。
- Cloud Firestore：存最新 snapshot 和历史 snapshot。
- 本地 15 分钟任务：用 OpenBB 刷新数据，并把快照发布到 Firestore。

## 当前项目已经准备好的文件

- `firebase.json`：Firebase Hosting + Firestore 配置。
- `.firebaserc`：默认 Firebase project 是 `agentic-investor-47147`。
- `firestore.rules`：只允许登录用户读取自己的 `/users/{uid}/...`。
- `firestore.indexes.json`：Firestore 索引占位。
- `dashboard/firebase-client.js`：上线后从 Firestore 读取私有快照。
- `dashboard/firebase-config.example.js`：Web App 配置模板。
- `scripts/publish_dashboard_firestore.py`：把本地快照发布到 Firestore。

## 你需要在 Firebase 控制台做一次性设置

1. 在项目 `agentic-investor-47147` 里启用 Cloud Firestore API。
2. 在 Firebase Console 里创建 Firestore Database。
3. 开启 Firebase Authentication，建议先用 Google 登录。
4. 创建一个 Web App，把配置复制到 `dashboard/firebase-config.js`。
5. 在 `dashboard/firebase-config.js` 里填入你的 Firebase Auth UID。
6. 创建 service account key，保存到本机私密路径，不要放进 repo。
7. 在本机设置环境变量：

```powershell
$env:FIREBASE_PROJECT_ID="agentic-investor-47147"
$env:FIREBASE_SERVICE_ACCOUNT_EMAIL="firebase-adminsdk-fbsvc@agentic-investor-47147.iam.gserviceaccount.com"
$env:FIREBASE_SERVICE_ACCOUNT="C:\path\to\service-account.json"
$env:FIREBASE_USER_UID="你的 Firebase Auth UID"
```

更推荐复制 `.env.example` 为 `.env`，把真实本地路径和 UID 填进去。`.env` 已经被 `.gitignore` 忽略，15 分钟本地自动化会自动读取它。

也可以把 service account JSON 路径放在 Google 标准变量里：

```powershell
$env:GOOGLE_APPLICATION_CREDENTIALS="C:\path\to\service-account.json"
```

如果没有安装 `firebase_admin`，先安装：

```powershell
python -m pip install firebase-admin
```

## 本地验证

```powershell
python scripts\run_task.py intraday_monitor
python scripts\run_task.py firebase_publish_snapshot
```

第一个任务会刷新 OpenBB market snapshot、paper portfolio mark-to-market、order intents 和 dashboard snapshot。

第二个任务会把 `dashboard/data/snapshot.json` 发布到：

```text
users/{你的 UID}/snapshots/current
users/{你的 UID}/snapshots/{generatedAt}
```

## 部署

如果还没登录 Firebase CLI：

```powershell
npx firebase-tools login
```

部署 Hosting、Firestore rules 和 indexes：

```powershell
npx firebase-tools deploy --project agentic-investor-47147
```

如果 CLI 因本机网络或权限拉不下来，至少先在 Firebase Console 手动发布 Firestore Rules：

1. Firebase Console 左侧点 `Build` -> `Firestore Database`。
2. 点上方 `Rules`。
3. 把项目里的 `firestore.rules` 内容完整复制进去。
4. 点 `Publish`。

## 安全边界

- 不把 `dashboard/data/snapshot.json` 或 `snapshot.js` 发布到 Firebase Hosting。
- 不在浏览器里放 service account key。
- 不读取真实券商账户。
- 不提交真实券商订单。
- 当前仍然是 advisory-only / paper-trade。
