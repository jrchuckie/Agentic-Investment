import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..");

function readJson(relOrAbsPath, fallback = null) {
  try {
    const fullPath = path.isAbsolute(relOrAbsPath)
      ? relOrAbsPath
      : path.join(projectRoot, relOrAbsPath);
    return JSON.parse(fs.readFileSync(fullPath, "utf8"));
  } catch {
    return fallback;
  }
}

function money(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : "N/A";
}

function symbolOf(code) {
  return String(code || "").replace(/^US\./, "");
}

function nowCN() {
  return new Date().toLocaleString("sv-SE", { timeZone: "Asia/Shanghai" }).replace(" ", "T");
}

function pick(obj, paths, fallback = undefined) {
  for (const p of paths) {
    const parts = p.split(".");
    let cur = obj;
    let ok = true;
    for (const part of parts) {
      if (!cur || typeof cur !== "object" || !(part in cur)) {
        ok = false;
        break;
      }
      cur = cur[part];
    }
    if (ok && cur !== undefined && cur !== null) return cur;
  }
  return fallback;
}

function fmtStep(name, step) {
  if (!step) return `${name}: 未知`;
  if (step.ok === true) return `${name}: OK`;
  const err = String(step.error || "unknown").replace(/\s+/g, " ").slice(0, 240);
  return `${name}: 失败（${err}）`;
}

function resolveWeixinSender() {
  const configured = process.env.WEIXIN_SEND_SCRIPT || process.env.WECHAT_SEND_SCRIPT;
  if (configured && configured.trim()) return configured.trim();
  return path.join(os.homedir(), "Documents", "Codex", "weixin-send.mjs");
}

function withTimeout(promise, timeoutMs, label) {
  let timeoutId;
  const timeoutPromise = new Promise((_, reject) => {
    timeoutId = setTimeout(() => reject(new Error(`${label} timeout after ${timeoutMs}ms`)), timeoutMs);
  });
  return Promise.race([promise, timeoutPromise]).finally(() => clearTimeout(timeoutId));
}

function findMarketRow(market, predicate) {
  const rows = [];
  function walk(node) {
    if (Array.isArray(node)) {
      for (const item of node) walk(item);
      return;
    }
    if (!node || typeof node !== "object") return;
    if ("symbol" in node && ("last" in node || "value" in node)) rows.push(node);
    for (const value of Object.values(node)) {
      if (value && typeof value === "object") walk(value);
    }
  }
  walk(market);
  for (const row of rows) {
    try {
      if (predicate(row)) return row;
    } catch {
      // ignore
    }
  }
  return null;
}

function fmtQuote(row, prefixOverride = null) {
  if (!row) return "N/A";
  const last = Number(row.last ?? row.value);
  const pct = Number(row.dayChangePct);
  const lastStr = Number.isFinite(last) ? last.toFixed(2) : "N/A";
  const pctStr = Number.isFinite(pct) ? `${pct.toFixed(2)}%` : "N/A";
  const sym = prefixOverride || symbolOf(row.symbol);
  return `${sym} ${lastStr}（日内 ${pctStr}）`;
}

const args = process.argv.slice(2);
const ctxArgIdx = args.findIndex((x) => x === "--context");
const ctxPath = ctxArgIdx >= 0 ? args[ctxArgIdx + 1] : null;
const ctx = ctxPath ? readJson(ctxPath, null) : null;
const label = (ctx?.label || "").trim() || "15分钟盯盘结论";

const account = readJson("data/broker/moomoo/real_account_latest.json", {});
const market = readJson("data/market/latest.json", {});
const state = readJson("state.json", {});

const acc = pick(account, ["accinfo.records.0"], {});
const positions = pick(account, ["positions.records"], []);
const orders = pick(account, ["orders.records"], []);

const accountTs = String(account?.timestamp || "N/A");
const marketTs = String(market?.timestamp || "N/A");
const stateTs = String(state?.last_check || state?.last_intraday_monitor || "N/A");

const totalAssets = money(acc?.total_assets);
const cash = money(acc?.cash);
const riskStatus = String(acc?.risk_status || "N/A");
const posCount = Array.isArray(positions) ? positions.length : 0;
const orderCount = Array.isArray(orders) ? orders.length : 0;

const refreshOk = ctx?.account_refresh?.ok === true && ctx?.intraday_monitor?.ok === true;
const conclusion = refreshOk
  ? "结论：按计划执行（风控优先），无明确触发则继续等待。"
  : "结论：数据刷新失败，暂停任何新增动作（只做观察/排障）。";

const headline = `【${label}】`;
const refreshLine = [
  fmtStep("REAL账户刷新", ctx?.account_refresh),
  fmtStep("盘中监控刷新", ctx?.intraday_monitor),
].join("；");
const accountLine = `账户：总资产 ${totalAssets}；现金 ${cash}；风控 ${riskStatus}；持仓 ${posCount}；未成交订单 ${orderCount}`;

const spy = findMarketRow(market, (r) => symbolOf(r.symbol) === "SPY");
const qqq = findMarketRow(market, (r) => symbolOf(r.symbol) === "QQQ");
const vix = findMarketRow(market, (r) => /VIX/i.test(String(r.symbol)) || /VIX/i.test(String(r.label || "")));
const us10y = findMarketRow(
  market,
  (r) => /10Y|10YR|US10Y/i.test(String(r.symbol)) || /10Y|10YR/i.test(String(r.label || "")),
);
const usdcnh = findMarketRow(
  market,
  (r) => /USDCNH|USD\/CNH/i.test(String(r.symbol)) || /USD.*CNH/i.test(String(r.label || "")),
);

const paperNav = money(state?.paper_portfolio_nav);
const paperUpnl = money(state?.paper_unrealized_pnl);
const firebaseStatus = String(state?.firebase_publish_status || "N/A");
const realReadEnabled = Boolean(state?.real_account_read_enabled);

const alerts = [];
if (!refreshOk) {
  const aErr = String(ctx?.account_refresh?.error || "").trim();
  const mErr = String(ctx?.intraday_monitor?.error || "").trim();
  if (aErr) alerts.push(`- REAL账户刷新失败：${aErr}`);
  if (mErr) alerts.push(`- 盘中监控刷新失败：${mErr}`);
  if (!alerts.length) alerts.push("- 数据刷新失败：未知原因（请检查 OpenD/网络）");
} else {
  alerts.push("- 无高优先级告警。");
}

const top3 = refreshOk
  ? [
      "- 继续等待（Recommended）：触发=N/A；失效=N/A；规模=0；窗口=本时段。",
      "- 若出现明确触发再行动：触发=规则触发价位；失效=规则失效位；规模=按规则；窗口=下一次 15 分钟检查前。",
      "- 仅做风控检查：触发=异常波动/告警；失效=异常解除；规模=0；窗口=立即。",
    ]
  : [
      "- 排障 OpenD（Recommended）：触发=11111 端口可连通；失效=仍 WSAECONNREFUSED；规模=0；窗口=立即。",
      "- 只读核对 REAL 账户：触发=OpenD 恢复；失效=OpenD 不可用；规模=0；窗口=下一次检查前。",
      "- 本时段观望：触发=N/A；失效=OpenD 恢复且规则触发；规模=0；窗口=本时段。",
    ];

const dataLine = `数据时间：账户 ${accountTs}；市场 ${marketTs}；状态 ${stateTs}；生成 ${nowCN()}`;

const message = [
  headline,
  conclusion,
  "",
  "【alerts】",
  ...alerts,
  "",
  "【market snapshot】",
  `- ${fmtQuote(spy)}`,
  `- ${fmtQuote(qqq)}`,
  "",
  "【VIX/10Y/USD-CNH】",
  `- VIX: ${fmtQuote(vix, "VIX")}`,
  `- 10Y: ${fmtQuote(us10y, "10Y")}`,
  `- USD/CNH: ${fmtQuote(usdcnh, "USD/CNH")}`,
  "",
  "【paper NAV/positions】",
  `- paper NAV: ${paperNav}；uPnL: ${paperUpnl}`,
  "",
  "【scenario】",
  refreshOk
    ? "- 可执行：仅在规则明确触发时给出建议（本脚本不下单）。"
    : "- 不可执行：数据链路中断，禁止任何新增动作（只读排障）。",
  "",
  "【new fill】",
  `- last_paper_fill_engine: ${String(state?.last_paper_fill_engine || "N/A")}`,
  "",
  "【eventRadar】",
  "- N/A（本次为确定性推送，不拉取新增事件雷达）",
  "",
  "【dashboard/Firebase】",
  `- firebase_publish_status: ${firebaseStatus}；real_account_read_enabled: ${realReadEnabled}`,
  "",
  "【Top3】",
  ...top3,
  "",
  refreshLine,
  accountLine,
  "",
  "备注：只读检查；不解锁交易；不下单/撤单/改单。",
  dataLine,
].join("\n");

const weixinPath = resolveWeixinSender();
const { sendWeixinText } = await import(pathToFileURL(weixinPath).href);

try {
  const to = await withTimeout(sendWeixinText(message), 30_000, "WeChat push");
  console.log(`Opening decision sent to ${to}`);
} catch (err) {
  console.error(`Opening decision push failed: ${err?.message || String(err)}`);
  process.exitCode = 2;
}
