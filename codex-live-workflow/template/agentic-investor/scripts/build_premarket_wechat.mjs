import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, "..");

function readText(filePath) {
  return fs.readFileSync(filePath, "utf8");
}

function readJsonLoose(filePath) {
  const raw = readText(filePath);
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start === -1 || end === -1 || end <= start) {
    throw new Error(`No JSON object found in ${filePath}`);
  }
  return JSON.parse(raw.slice(start, end + 1));
}

function tryReadJsonLoose(filePath) {
  try {
    if (!fs.existsSync(filePath)) return null;
    return readJsonLoose(filePath);
  } catch {
    return null;
  }
}

function money(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "N/A";
  return `$${Number(n).toFixed(2)}`;
}

function fmt(n, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "N/A";
  return Number(n).toFixed(digits);
}

function fmtPct(n, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "N/A";
  return `${Number(n).toFixed(digits)}%`;
}

function upper(s) {
  return String(s || "").toUpperCase();
}

function pickLatestFile(dir, predicate) {
  if (!fs.existsSync(dir)) return null;
  const files = fs
    .readdirSync(dir)
    .filter((f) => predicate(f))
    .map((f) => ({ name: f, full: path.join(dir, f) }))
    .sort((a, b) => a.name.localeCompare(b.name));
  return files.length ? files.at(-1).full : null;
}

function loadEarningsRisk() {
  const eventsDir = path.join(ROOT, "data", "events");
  const latest = pickLatestFile(eventsDir, (f) => f.includes("earnings_event_risk") && f.endsWith(".json"));
  return latest ? readJsonLoose(latest) : { events: [], generated_at: null };
}

function summarizeOpenOrders(openOrders) {
  const orders = (openOrders || []).filter((o) => {
    const st = upper(o.status || o.order_status || "");
    return !(st.includes("FILLED") || st.includes("CANCEL") || st.includes("FAILED") || st.includes("DELETED"));
  });
  if (!orders.length) return ["无未成交订单（已过滤成交/撤单/失败）"];
  return orders.map((o) => {
    const sym = o.symbol || String(o.code || "").split(".").at(-1) || "N/A";
    const side = o.side || o.trd_side || "N/A";
    const qty = o.qty ?? o.quantity ?? null;
    const price = o.price ?? null;
    const status = o.status || o.order_status || "N/A";
    return `${upper(sym)} ${side} ${fmt(qty, 0)} @${money(price)} 状态:${status}`;
  });
}

function rankPositions(positions) {
  const rows = (positions || []).map((p) => ({
    ...p,
    _mv: Number(p.market_val ?? p.marketVal ?? 0) || 0,
    _symbol: upper(p.symbol || String(p.code || "").split(".").at(-1) || ""),
  }));
  rows.sort((a, b) => (b._mv || 0) - (a._mv || 0));
  const total = rows.reduce((s, r) => s + (r._mv || 0), 0);
  return rows.map((r) => ({ ...r, _weight: total > 0 ? (r._mv / total) * 100 : null }));
}

function pickAccountSnapshot() {
  // Prefer real-account snapshots produced by `scripts/run_task.py intraday_monitor`.
  const latestPath = path.join(ROOT, "data", "broker", "moomoo", "real_account_latest.json");
  const latest = tryReadJsonLoose(latestPath);

  const ok =
    latest &&
    latest.status &&
    String(latest.status).toUpperCase() === "PASS" &&
    latest.accinfo &&
    Array.isArray(latest.accinfo.records) &&
    latest.accinfo.records.length > 0;

  if (ok) return { snapshot: latest, source: "broker_latest", note: null };

  const accountDir = path.join(ROOT, "data", "broker", "moomoo");
  const fallbackFile = pickLatestFile(accountDir, (f) => f.includes("real_account_snapshot") && f.endsWith(".json") && !f.includes("latest"));
  const fallback = fallbackFile ? tryReadJsonLoose(fallbackFile) : null;

  if (fallback && String(fallback.status || "").toUpperCase() === "PASS") {
    const errText = latest?.errors ? JSON.stringify(latest.errors) : null;
    return {
      snapshot: fallback,
      source: "fallback",
      note: `实时抓取受限（未解锁交易且不做任何解锁/下单动作）。改用上次成功快照：${fallback.timestamp}${
        errText ? `；本次错误:${errText}` : ""
      }`,
    };
  }

  return {
    snapshot: latest || { timestamp: new Date().toISOString(), status: "FAIL", errors: {} },
    source: "none",
    note: latest ? "账户快照为空/受限（仍保持只读，不解锁交易）。" : "未找到可用账户快照（仍保持只读，不解锁交易）。",
  };
}

function pickMacro(market) {
  const trueMacro = Object.fromEntries(((market.trueMacroSeries || []) ?? []).map((x) => [upper(x.symbol), x]));
  const futures = market.futures || [];
  const find = (sym) => futures.find((x) => upper(x.symbol) === upper(sym)) || null;
  return {
    es: find("ES=F"),
    nq: find("NQ=F"),
    ym: find("YM=F"),
    rty: find("RTY=F"),
    vix: trueMacro.VIX || null,
    dgs10: trueMacro.DGS10 || null,
    usdcnh: trueMacro.USDCNH || null,
  };
}

function buildTop3({ positions, earningsRisk, watchlist, macroOverlay }) {
  const top3 = [];
  const held = new Set(positions.map((p) => upper(p._symbol)));

  // 1) Reduce-risk candidate: largest loser by PL%
  const losers = positions
    .filter((p) => Number.isFinite(Number(p.pl_ratio)))
    .slice()
    .sort((a, b) => (Number(a.pl_ratio) || 0) - (Number(b.pl_ratio) || 0));
  const worst = losers[0];
  if (worst) {
    const sym = upper(worst._symbol);
    const cost = Number(worst.cost_price ?? null);
    const trigger = Number.isFinite(cost) ? `开盘后跌破成本价 ${money(cost)} 下方约2%（≈${money(cost * 0.98)}）` : "开盘后若走弱并跌破关键均线/昨日低点（以盘中确认）";
    top3.push({
      title: `${sym}：优先减风险（不加仓摊平）`,
      trigger,
      invalidation: "若强势高开并站稳关键支撑/均线，减仓可延后但禁止追涨加杠杆。",
      amount: "建议先减 20%~50% 仓位（按你的风险承受与流动性）。",
    });
  }

  // 2) Earnings-window control for NVDA if held & flagged
  const nvdaEvent = (earningsRisk.events || []).find((e) => upper(e.symbol) === "NVDA") || null;
  if (held.has("NVDA")) {
    top3.push({
      title: "NVDA：财报窗口风控（不加杠杆/不卖裸期权）",
      trigger: nvdaEvent ? `财报事件临近：${nvdaEvent.earnings_date || "见日历"}（${nvdaEvent.window || "窗口期"}）` : "财报窗口期内",
      invalidation: "若财报已过且波动回落，再评估是否加仓/调仓。",
      amount: "0（风控动作：只做减风险/设止损，不做加仓）。",
    });
  }

  // 3) Tiny probe: pick top momentum watchlist (non-held) if macro allows
  const wl = (watchlist.items || []).map((x) => upper(x.symbol)).filter(Boolean);
  const candidates = (macroOverlay?.minimum_cash ?? 0.25) > 0 ? wl.filter((s) => !held.has(s)) : [];
  const pick = candidates[0] || null;
  top3.push({
    title: pick ? `小额试探：${pick}（仅用少量现金）` : "小额试探：今日无高胜率入口（先等确认）",
    trigger: pick ? "仅当盘前/开盘后维持强势（不破关键支撑），且大盘不出现快速转弱。" : "等待 watchlist 标的重新站稳关键均线并确认量能。",
    invalidation: "若指数期货由正转负并快速走弱，或VIX跳升，则取消试探。",
    amount: pick ? "$300~$500（不超过现金的 6%）" : "0",
  });

  return top3.slice(0, 3);
}

const { snapshot: account, note: accountNote } = pickAccountSnapshot();
const market = readJsonLoose(path.join(ROOT, "data", "market", "latest.json"));
const social = readJsonLoose(path.join(ROOT, "data", "social_sentiment", "latest.json"));
const watchlist = readJsonLoose(path.join(ROOT, "watchlist.json"));
const dashboard = readJsonLoose(path.join(ROOT, "dashboard", "data", "snapshot.json"));
const earningsRisk = loadEarningsRisk();

const assets = (account.accinfo && account.accinfo.records && account.accinfo.records[0]) || account.assets || {};
const positions = rankPositions((account.positions && account.positions.records) || account.positions || []);
const openOrderLines = summarizeOpenOrders((account.orders && account.orders.records) || account.open_orders || account.openOrders || []);
const macro = pickMacro(market);

const overlay = (market.macroRegime && market.macroRegime.overlay) || null;
const macroOverlay = overlay || { max_gross_exposure: 0.75, minimum_cash: 0.25, new_options_allowed: false };

const riskLines = [];
if (macro.es) riskLines.push(`ES=F ${fmt(macro.es.last, 0)} (${fmtPct(macro.es.dayChangePct)})`);
if (macro.nq) riskLines.push(`NQ=F ${fmt(macro.nq.last, 0)} (${fmtPct(macro.nq.dayChangePct)})`);
if (macro.vix) riskLines.push(`VIX ${fmt(macro.vix.last, 2)} (${fmtPct(macro.vix.dayChangePct)})`);
if (macro.dgs10) riskLines.push(`10Y ${fmt(macro.dgs10.last, 3)}% (${fmtPct(macro.dgs10.dayChangePct)})`);
if (macro.usdcnh) riskLines.push(`USD/CNH ${fmt(macro.usdcnh.last, 3)}`);

const posLines = positions.length
  ? positions.map((p) => {
      const sym = upper(p._symbol);
      const qty = p.qty ?? null;
      const canSell = p.can_sell_qty ?? null;
      const cost = p.cost_price ?? null;
      const mv = p.market_val ?? null;
      const pl = p.pl_val ?? null;
      const plr = p.pl_ratio ?? null;
      const today = p.today_pl_val ?? null;
      const w = p._weight;
      return `${sym} 持仓${fmt(qty, 0)} 可卖${fmt(canSell, 0)} | 成本${money(cost)} | 市值${money(mv)} (${fmt(w, 1)}%) | 未实现${money(pl)} (${fmtPct(plr)}) | 今日${money(today)}`;
    })
  : ["（本次未能读取到持仓；不解锁交易，已尽最大只读努力）"];

const mood = social.marketMood || null;
const moodLine = mood ? `${mood.labelZh}（拥挤度:${mood.crowdingRisk} 置信度:${mood.confidence}）` : "N/A";

const eventRadar = dashboard?.data?.eventRadar?.items || dashboard?.eventRadar?.items || [];
const topEvents = Array.isArray(eventRadar) ? eventRadar.slice(0, 6).map((e) => `- ${e.title || e.headline || JSON.stringify(e).slice(0, 120)}`) : [];

const top3 = buildTop3({ positions, earningsRisk, watchlist, macroOverlay });

const lines = [];
lines.push("【美股盘前检查】（只读建议｜不自动交易）");
lines.push(`时间：${account.timestamp || "N/A"}（Asia/Shanghai）`);
lines.push("账户：moomoo REAL / FUTUINC / US / Margin（只读：不解锁交易、不下单/撤单/改单）");
if (accountNote) lines.push(`备注：${accountNote}`);
lines.push("");

lines.push("一、账户快照");
lines.push(`- 总资产：${money(assets.total_assets)} | 现金：${money(assets.cash)} | 购买力：${money(assets.power)}`);
lines.push(
  `- 维持保证金：${money(assets.maintenance_margin)} | 风险：${assets.risk_status || assets.risk_level || "N/A"} | MarginCall门槛：${
    assets.margin_call_margin ?? "N/A"
  }`
);
lines.push("");

lines.push("二、盘前宏观/风险快照");
lines.push(`- ${riskLines.length ? riskLines.join(" | ") : "N/A"}`);
lines.push(`- 社媒情绪：${moodLine}（仅作<=15% overlay，不覆盖仓位/宏观/财报）`);
lines.push("");

lines.push("三、实盘持仓（按市值排序）");
for (const l of posLines) lines.push(`- ${l}`);
lines.push("");

lines.push("四、未成交订单");
for (const l of openOrderLines) lines.push(`- ${l}`);
lines.push("");

lines.push("五、事件雷达（可能影响开盘波动）");
if (topEvents.length) lines.push(...topEvents);
else lines.push("- （无高优先级事件或数据缺失）");
lines.push("");

lines.push("六、今日 Top3 操作建议（手动执行；每条含触发/失效/金额）");
top3.forEach((op, idx) => {
  lines.push(`${idx + 1}) ${op.title}`);
  lines.push(`   - 触发：${op.trigger}`);
  lines.push(`   - 止损/失效：${op.invalidation}`);
  lines.push(`   - 建议金额/规模：${op.amount}`);
});
lines.push("");

lines.push("七、出现以下信号 → 立刻微信推送我（预警）");
lines.push("- 指数期货：ES=F 或 NQ=F 由正转负并加速下跌（例如 <-0.8%）");
lines.push("- 波动率：VIX 盘前/盘中快速冲上 >20");
lines.push("- 利率：10Y 快速上冲并站稳在更高平台（阈值以当日环境为准）");
lines.push("- 个股：核心持仓开盘后跌破成本价下方约2%且放量（先减风险，禁止摊平加杠杆）");
lines.push("");

lines.push("八、禁止动作（脚本层面也禁止）");
lines.push("- 不解锁交易、不输入交易密码");
lines.push("- 不下单/撤单/改单；所有输出仅为建议");
lines.push("- 不在财报窗口期新增高杠杆/裸卖期权敞口");

process.stdout.write(lines.join("\n"));
