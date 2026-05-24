# Transfer Checklist

On the always-on Windows machine:

1. Install Codex, Node.js, Python, moomoo desktop, and moomoo OpenD.
2. Download or clone this GitHub package.
3. Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\skill\agentic-investor-live-workflow\scripts\bootstrap_agentic_investor.ps1
```

4. Copy `template\agentic-investor\.env.example` to `.env` and fill only local private paths and credentials.
5. Log in to moomoo/OpenD manually and confirm OpenD is listening on `127.0.0.1:11111`.
6. Set up OpenClaw WeChat, then send one message to the bot so `latest-target.json` is created.
7. Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\skill\agentic-investor-live-workflow\scripts\self_check.ps1
node %USERPROFILE%\Documents\Codex\weixin-send.mjs "测试：24小时机器微信联通正常"
```

The package does not contain your real account data or WeChat target. Those must be created on the new machine by logging in locally.

