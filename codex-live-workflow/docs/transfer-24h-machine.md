# 24 小时机器迁移说明

目标：让另一台 Codex 机器复现当前投资监控工作流，但不复制任何真实账户快照、密钥、微信 target 或本机日志。

## 安装

```powershell
git clone https://github.com/jrchuckie/Agentic-Investment.git
cd Agentic-Investment\codex-live-workflow
powershell -NoProfile -ExecutionPolicy Bypass -File .\skill\agentic-investor-live-workflow\scripts\bootstrap_agentic_investor.ps1
```

如果没有 git，也可以下载 zip 后在 `codex-live-workflow` 目录运行同一条 bootstrap 命令。

## 必须手动完成

- 在新机器登录 moomoo/OpenD。
- 在新机器登录 OpenClaw 微信。
- 让微信用户给 bot 发一条消息，生成 `%USERPROFILE%\.codex\weixin-bridge\latest-target.json`。
- 把 `template\agentic-investor\.env.example` 复制为工作区里的 `.env`，填入本机私有配置。

## 验证

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File %USERPROFILE%\.codex\skills\agentic-investor-live-workflow\scripts\self_check.ps1
node %USERPROFILE%\Documents\Codex\weixin-send.mjs "测试：24小时机器微信联通正常"
powershell -NoProfile -ExecutionPolicy Bypass -File %USERPROFILE%\Documents\New project\agentic-investor\scripts\run_reduction_guard.ps1 --force-push --push
```

## 定时任务建议

- 每天 09:00 Asia/Shanghai：生成投资简报并推送微信。
- 美股开盘后 15/30/45/60 分钟：运行 opening decision push。
- 用户要求盯盘时：15 分钟一次，直到用户指定时间。

所有任务都必须保持 advisory-only；脚本不解锁、不下单、不撤单、不改单。

