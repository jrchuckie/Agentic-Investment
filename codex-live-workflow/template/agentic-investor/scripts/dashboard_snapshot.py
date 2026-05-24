from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_investor_common import ROOT, json_safe, now_iso, read_json, write_json


DASHBOARD_DIR = ROOT / "dashboard"
SNAPSHOT_PATH = DASHBOARD_DIR / "data" / "snapshot.json"
SNAPSHOT_JS_PATH = DASHBOARD_DIR / "data" / "snapshot.js"
PAPER_PORTFOLIO_PATH = ROOT / "data" / "trading" / "paper-portfolio.json"
PAPER_NAV_PATH = ROOT / "data" / "trading" / "paper-nav.jsonl"
CONDITIONAL_PLAYBOOK_PATH = ROOT / "data" / "trading" / "conditional-playbook.json"
STRATEGY_COMPARE_PATH = ROOT / "data" / "backtests" / "latest_strategy_compare.json"

ETF_TOOL_SYMBOLS = {"QQQ", "SPY", "SMH", "SOXX", "DRAM", "SNXX", "MULL", "EWY"}
LEVERAGED_ETF_TOOL_SYMBOLS = {"SNXX", "MULL"}
INVALID_WATCHLIST_SYMBOLS = {"MUU"}

ETF_LOOKTHROUGH = {
    "SNXX": {
        "typeZh": "2x单股ETF",
        "primarySymbol": "SNDK",
        "underlyingSymbols": ["SNDK"],
        "summaryZh": "SNXX 是 SNDK 的 2x 日内做多工具；判断先看 SNDK 本身，再看 SNXX 成交量/价差。",
    },
    "MULL": {
        "typeZh": "2x单股ETF",
        "primarySymbol": "MU",
        "underlyingSymbols": ["MU"],
        "summaryZh": "MULL 是 MU 的 2x 日内做多工具；判断先看 MU 本身，再看 MULL 成交量/价差。",
    },
    "DRAM": {
        "typeZh": "全球存储ETF",
        "primarySymbol": "DRAM",
        "underlyingSymbols": ["EWY", "005930.KS", "000660.KS", "MU", "285A.T", "SNDK", "WDC", "STX", "2408.TW", "2344.TW"],
        "summaryZh": "DRAM 是全球 memory basket；EWY 可作为韩国市场代理，另外要看 Samsung、SK Hynix、Kioxia、Nanya、Winbond 和美股存储链。",
    },
}

UNDERLYING_LABELS = {
    "005930.KS": "Samsung",
    "000660.KS": "SK Hynix",
    "285A.T": "Kioxia",
    "2408.TW": "Nanya",
    "2344.TW": "Winbond",
    "MU": "Micron",
    "SNDK": "SanDisk",
    "WDC": "Western Digital",
    "STX": "Seagate",
    "EWY": "韩国ETF",
    "PSTG": "Pure Storage",
    "NTAP": "NetApp",
}

WATCHLIST_GROUPS = {
    "ETF/市场": {"QQQ", "SPY", "SMH", "SOXX"},
    "存储/内存 ETF工具": {"DRAM", "SNXX", "MULL"},
    "韩国/非美代理": {"EWY"},
    "AI 存储/内存": {"MU", "WDC", "STX", "PSTG", "NTAP", "SNDK"},
    "非美存储/内存": {"005930.KS", "000660.KS", "285A.T", "2408.TW", "2344.TW"},
    "AI 芯片/算力": {
        "NVDA",
        "AMD",
        "AVGO",
        "TSM",
        "ASML",
        "ARM",
        "MRVL",
        "LRCX",
        "MPWR",
        "NXPI",
        "ON",
        "ALAB",
        "TSEM",
        "INTC",
    },
    "AI 应用层/平台软件": {"PLTR", "MSFT", "GOOG", "GOOGL", "META", "ORCL", "CRWD", "PANW", "GTLB", "SNOW", "DDOG", "APP", "TEM"},
    "消费/广告/内容": {"AMZN", "AAPL", "TSLA", "NFLX", "RDDT", "SHOP", "SPOT", "ROKU", "BILL", "TEM", "GME", "EBAY"},
    "Fintech/Crypto": {"CRCL", "COIN", "HOOD"},
    "激进投资/13D事件": {"PINS", "TXN", "WBD", "HHH", "FUN", "BP", "LW", "SDRL"},
    "AI 基础设施/电力": {
        "SMCI",
        "DELL",
        "ANET",
        "VRT",
        "COHR",
        "GLW",
        "LITE",
        "POET",
        "GEV",
        "PWR",
        "ETN",
        "NBIS",
        "IREN",
        "APLD",
        "BE",
        "CRWV",
        "CORZ",
        "CRDO",
        "NVT",
        "FN",
        "CLS",
        "CEG",
        "VST",
        "NRG",
        "TLN",
        "KGS",
        "FLNC",
        "DLR",
        "EQIX",
        "CIEN",
        "MOD",
        "JCI",
        "CARR",
        "HUBB",
        "GNRC",
        "CIFR",
        "CLSK",
        "RIOT",
        "HUT",
        "BTDR",
        "BITF",
        "EQT",
        "LBRT",
        "PUMP",
        "PSIX",
    },
}

AI_CHAIN_LAYERS = [
    {
        "id": "compute",
        "nameZh": "1. 计算",
        "descriptionZh": "GPU/ASIC/CPU/定制芯片，决定算力供给和服务器 ASP。",
        "symbols": {"NVDA", "AMD", "AVGO", "ARM", "INTC", "MRVL", "MPWR", "NXPI", "ON", "ALAB", "TSEM"},
    },
    {
        "id": "memory_storage",
        "nameZh": "2. 内存/存储",
        "descriptionZh": "HBM、DDR5、NAND、SSD；推理和多模态会继续拉高容量与带宽需求。",
        "symbols": {
            "MU",
            "WDC",
            "STX",
            "PSTG",
            "NTAP",
            "SNDK",
            "DRAM",
            "SNXX",
            "MULL",
            "EWY",
            "005930.KS",
            "000660.KS",
            "285A.T",
            "2408.TW",
            "2344.TW",
        },
    },
    {
        "id": "network_optical",
        "nameZh": "3. 网络/光通信",
        "descriptionZh": "交换机、光模块、AEC/CPO/Retimer；训练集群和推理集群都需要更高带宽。",
        "symbols": {"ANET", "AVGO", "MRVL", "ALAB", "COHR", "GLW", "LITE", "POET", "CRDO", "CIEN", "FN"},
    },
    {
        "id": "generation_power",
        "nameZh": "4. 发电",
        "descriptionZh": "核电、燃气、现场发电、燃料电池和储能；解决 AI 数据中心的电力来源。",
        "symbols": {"GEV", "BE", "CEG", "VST", "NRG", "TLN", "EQT", "KGS", "NEE", "BEP", "FLNC", "PSIX"},
    },
    {
        "id": "power_distribution",
        "nameZh": "5. 供配电",
        "descriptionZh": "PDU、UPS、变压器、开关柜、输配电施工；数据中心 capex 中占比高。",
        "symbols": {"ETN", "PWR", "GEV", "VRT", "NVT", "HUBB", "GNRC"},
    },
    {
        "id": "cooling",
        "nameZh": "6. 冷却",
        "descriptionZh": "液冷、风冷、浸没式、CDU、换热；高密度机柜会推高散热价值量。",
        "symbols": {"VRT", "ETN", "NVT", "MOD", "JCI", "CARR", "SMCI", "DELL"},
    },
    {
        "id": "packaging_pcb",
        "nameZh": "7. 封装/PCB",
        "descriptionZh": "CoWoS、FC-BGA、ABF、PCB/CCL、基板；先进封装和高速互连的瓶颈。",
        "symbols": {"TSM", "ASML", "LRCX", "AMAT", "KLAC", "MU", "AVGO", "FN", "CLS"},
    },
    {
        "id": "land_construction",
        "nameZh": "8. 土地/建设",
        "descriptionZh": "数据中心建设、土地、施工、交付、colo；把电力和芯片转成可用容量。",
        "symbols": {"PWR", "DLR", "EQIX", "CRWV", "CORZ", "APLD", "IREN", "NBIS", "CLS"},
    },
    {
        "id": "energy_infra",
        "nameZh": "9. 能源基础设施",
        "descriptionZh": "输电、PPA、天然气、储能和并网；决定 gigawatt-scale AI campus 能否落地。",
        "symbols": {"PWR", "GEV", "ETN", "CEG", "VST", "NRG", "TLN", "BE", "EQT", "KGS", "FLNC", "LBRT", "PUMP", "PSIX"},
    },
    {
        "id": "facility_systems",
        "nameZh": "10. 设施系统",
        "descriptionZh": "机房、DCIM、消防、安防、楼宇控制和监控；提高 uptime 和运维效率。",
        "symbols": {"VRT", "JCI", "CARR", "HON", "NVT", "HUBB", "EQIX", "DLR"},
    },
    {
        "id": "operators_cloud",
        "nameZh": "承载/云平台",
        "descriptionZh": "AI 云、HPC hosting、hyperscaler 和边缘/混合部署。",
        "symbols": {"CRWV", "CORZ", "APLD", "IREN", "NBIS", "MSFT", "GOOG", "GOOGL", "AMZN", "META", "ORCL"},
    },
    {
        "id": "ai_applications",
        "nameZh": "AI 应用层",
        "descriptionZh": "企业 AI 应用、Agent/workflow、垂直行业 AI 和安全/数据平台；重点看收入兑现、续约和估值消化。",
        "symbols": {"PLTR", "TEM", "APP", "SNOW", "DDOG", "GTLB", "CRWD", "PANW", "MSFT", "GOOG", "GOOGL", "META", "ORCL", "AMZN", "RDDT", "SHOP", "BILL"},
    },
    {
        "id": "transition_miners",
        "nameZh": "不确定项：矿工转 AI",
        "descriptionZh": "BTC 矿场转 HPC/AI hosting，弹性高但商业模式、融资和电力交付风险也高。",
        "symbols": {"CORZ", "IREN", "APLD", "CIFR", "CLSK", "RIOT", "HUT", "BTDR", "BITF"},
    },
]

AI_CHAIN_BY_SYMBOL: dict[str, list[dict[str, Any]]] = {}
for layer in AI_CHAIN_LAYERS:
    for symbol in layer["symbols"]:
        AI_CHAIN_BY_SYMBOL.setdefault(symbol, []).append(layer)

STATIC_WATCHLIST_SYMBOLS = (
    set().union(*WATCHLIST_GROUPS.values())
    | set().union(*(layer["symbols"] for layer in AI_CHAIN_LAYERS))
    | set().union(*(set(item.get("underlyingSymbols", [])) for item in ETF_LOOKTHROUGH.values()))
)

ACTIVIST_ALPHA_SOURCE_URL = "https://activist-alpha-v2.vercel.app/?tab=opportunities"

ACTIVIST_ALPHA_OVERLAYS = {
    "PINS": {
        "investor": "Elliott Management",
        "stageId": "private",
        "stageZh": "Private：友好型 13D/可转债介入",
        "scannerScore": 45,
        "opportunityScore": 61,
        "coreRelevance": "HIGH",
        "thesisTypeZh": "AI广告变现 + 资本回报",
        "catalystZh": "Elliott 披露约 10 亿美元可转债投资，并支持回购；后续看 ARPU、AI广告投放效率和 Q1/Q2 财报验证。",
        "riskZh": "友好型 activism 不一定带来强制重组；如果广告增长或用户参与度不兑现，估值修复会失效。",
        "sourceNoteZh": "Activist Alpha 页面列为 Elliott 最新新仓/友好型 campaign；交易前仍需核验 SEC 13D/13D-A 原文。",
    },
    "TXN": {
        "investor": "Elliott Management",
        "stageId": "private",
        "stageZh": "Private：资本回报/运营改善施压",
        "scannerScore": None,
        "opportunityScore": 57,
        "coreRelevance": "HIGH",
        "thesisTypeZh": "模拟芯片周期 + FCF/资本开支纪律",
        "catalystZh": "模拟半导体周期恢复与 FCF 目标延迟形成施压窗口；关注管理层资本开支、回购和利润率承诺。",
        "riskZh": "TXN 是高质量但成熟公司，activist upside 取决于资本配置改变，而不是 AI 直接瓶颈重估。",
        "sourceNoteZh": "Activist Alpha 页面把 TXN 列为 stage 3 private opportunity；需用 SEC/公司公告复核。",
    },
    "INTC": {
        "investor": "potential activist / strategic review",
        "stageId": "recon",
        "stageZh": "Recon：CEO 战略审查/Foundry 价值解锁候选",
        "scannerScore": 46,
        "opportunityScore": None,
        "coreRelevance": "HIGH",
        "thesisTypeZh": "Foundry 分拆/18A 良率/资本开支重构",
        "catalystZh": "新 CEO 战略审查、18A 节点良率和 foundry 亏损收窄是可验证节点；若出现 13D/董事会压力，事件强度上调。",
        "riskZh": "foundry 亏损、AI GPU 份额落后和重资本开支仍是核心风险；反弹不能替代基本面验证。",
        "sourceNoteZh": "Activist Alpha Scanner 将 INTC 列为高脆弱度 pre-target。",
    },
    "WBD": {
        "investor": "Ancora Holdings",
        "stageId": "public",
        "stageZh": "Public：战略清晰度/资产处置施压",
        "scannerScore": 48,
        "opportunityScore": None,
        "coreRelevance": "MEDIUM",
        "thesisTypeZh": "媒体资产折价 + HBO/Max 战略选择",
        "catalystZh": "Ancora 反对内容授权交易并要求战略清晰；后续看债务、流媒体亏损和潜在资产重组。",
        "riskZh": "高债务和传统媒体衰退会抵消事件催化，属于高波动事件驱动，不是 AI 主线。",
        "sourceNoteZh": "Activist Alpha Scanner 给 WBD 最高脆弱度之一。",
    },
    "BILL": {
        "investor": "Starboard Value",
        "stageId": "settlement",
        "stageZh": "Settlement：董事会席位已落地",
        "scannerScore": None,
        "opportunityScore": 60,
        "coreRelevance": "MEDIUM",
        "thesisTypeZh": "Rule of 50 / SaaS 运营改善",
        "catalystZh": "Starboard 已拿到董事会席位；后续看费用纪律、增长恢复和 Rule of 50 路径。",
        "riskZh": "settlement 后 alpha 取决于执行，不再是 13D 公告初期的价格重估。",
        "sourceNoteZh": "Activist Alpha 页面列为 Starboard settlement case。",
    },
    "HOOD": {
        "investor": "scanner watch only",
        "stageId": "watch",
        "stageZh": "Watch：估值重估后等待回撤",
        "scannerScore": 26,
        "opportunityScore": None,
        "coreRelevance": "MEDIUM",
        "thesisTypeZh": "交易量/加密/零售投机周期",
        "catalystZh": "只有在交易量降温或出现 30-40% 回撤时才重新评估 activist/估值修复逻辑。",
        "riskZh": "股价已经大幅重估，双重股权结构限制治理施压力度。",
        "sourceNoteZh": "Activist Alpha Scanner 明确偏 watch-only。",
    },
    "HHH": {
        "investor": "Pershing Square",
        "stageId": "structural",
        "stageZh": "Structural：控制型重组",
        "scannerScore": None,
        "opportunityScore": 64,
        "coreRelevance": "LOW",
        "thesisTypeZh": "地产控股/资本配置重组",
        "catalystZh": "Pershing Square 高持股和战略重组是核心事件。",
        "riskZh": "非科技主线，只作为事件驱动样本，不进入 AI Top3 默认候选。",
        "sourceNoteZh": "Activist Alpha opportunity score 最高之一。",
    },
    "FUN": {
        "investor": "Jana Partners",
        "stageId": "public",
        "stageZh": "Public：出售/运营改善倡议",
        "scannerScore": None,
        "opportunityScore": 58,
        "coreRelevance": "LOW",
        "thesisTypeZh": "主题公园/体验消费资产出售",
        "catalystZh": "Jana 要求评估公司出售或战略替代方案。",
        "riskZh": "宏观消费和杠杆风险较高，非 AI 主线。",
        "sourceNoteZh": "Activist Alpha live campaign。",
    },
    "BP": {
        "investor": "Elliott Management",
        "stageId": "public",
        "stageZh": "Public：能源资本配置施压",
        "scannerScore": None,
        "opportunityScore": 58,
        "coreRelevance": "LOW",
        "thesisTypeZh": "能源资产组合/资本回报",
        "catalystZh": "传统能源公司资本配置和资产组合调整。",
        "riskZh": "油价和政策风险主导，不适合作为 AI 产业链候选。",
        "sourceNoteZh": "Activist Alpha live campaign。",
    },
    "LW": {
        "investor": "Jana Partners",
        "stageId": "public",
        "stageZh": "Public：运营改善/董事会压力",
        "scannerScore": None,
        "opportunityScore": 54,
        "coreRelevance": "LOW",
        "thesisTypeZh": "消费食品运营改善",
        "catalystZh": "Jana 增持并批评 EBITDA 损失。",
        "riskZh": "食品周期和执行风险，与当前 AI 主线相关性低。",
        "sourceNoteZh": "Activist Alpha live campaign。",
    },
    "SDRL": {
        "investor": "Elliott Management",
        "stageId": "public",
        "stageZh": "Public：海上钻井周期/仓位管理",
        "scannerScore": None,
        "opportunityScore": 61,
        "coreRelevance": "LOW",
        "thesisTypeZh": "离岸钻井周期",
        "catalystZh": "离岸钻井需求周期恢复和多次 13D/A 仓位更新。",
        "riskZh": "能源周期股，高波动，非 AI 主线。",
        "sourceNoteZh": "Activist Alpha live campaign。",
    },
}

AI_REPRICING_OVERLAYS = {
    "SNDK": {
        "stageZh": "基准样本：AI 推理存储重估",
        "score": 94,
        "demandLagMonths": "6-12",
        "reasonsZh": [
            "QLC SSD 从消费存储变成推理/KV cache/vector DB 基础设施",
            "旧周期股估值框架可能被 AI inference 需求重新定价",
            "属于已验证案例，用作 next-SNDK 对照组而不是主要新增仓候选",
        ],
        "buyConditionZh": "只在回撤消化后评估，不追单日大涨；SNXX 只能作为短线工具，不替代 SNDK 本体研究。",
    },
    "ALAB": {
        "stageZh": "架构新节点：PCIe/CXL retimer",
        "score": 88,
        "demandLagMonths": "0-12",
        "reasonsZh": [
            "AI server 复杂拓扑让 PCIe/CXL retimer 从边缘小件变成必需节点",
            "新架构新增价值量，和传统半导体周期相关性较低",
            "若客户导入和收入增速继续兑现，估值框架可能从小型芯片股切换到 AI interconnect 平台",
        ],
        "buyConditionZh": "优先等回撤或财报后确认；若守住 MA50 且营收/客户导入继续强，才考虑小仓 paper starter。",
    },
    "MRVL": {
        "stageZh": "角色重定价：1.6T 光互连 DSP/定制硅",
        "score": 84,
        "demandLagMonths": "6-18",
        "reasonsZh": [
            "从传统网络/存储芯片重新定位到 AI interconnect 和 1.6T 光模块 DSP",
            "AI 集群东西向流量增长会继续拉动交换、DSP、定制硅价值量",
            "比纯光模块更偏平台型，但需要财报确认 AI 收入兑现速度",
        ],
        "buyConditionZh": "适合回撤买而不是追涨；关注 AI revenue 指引、1.6T 订单和毛利率是否同步改善。",
    },
    "CRDO": {
        "stageZh": "架构新节点：AEC/高速连接",
        "score": 82,
        "demandLagMonths": "6-18",
        "reasonsZh": [
            "AEC 和高速互连受益于 AI rack 内部连接复杂度提升",
            "比大型芯片股更早暴露在新架构价值量变化中",
            "弹性高但估值/客户集中度风险也高，需要价格确认",
        ],
        "buyConditionZh": "只在趋势健康且不高于 MA50 太多时考虑；若单日过热，等待回踩 VWAP/MA50。",
    },
    "LITE": {
        "stageZh": "需求传导：光模块/激光器",
        "score": 78,
        "demandLagMonths": "6-18",
        "reasonsZh": [
            "AI 集群带宽需求从 GPU 传导到光模块、激光器和相关器件",
            "可能受益于 800G/1.6T 升级周期",
            "比纯算力龙头更像第二/第三层传导标的",
        ],
        "buyConditionZh": "需要确认订单、毛利率和产能利用率；技术面不破 MA50 再进入候选。",
    },
    "FN": {
        "stageZh": "供应链瓶颈：光模块制造/互连",
        "score": 76,
        "demandLagMonths": "6-18",
        "reasonsZh": [
            "处在 AI 网络、光互连和制造供应链交叉点",
            "若 1.6T 光模块升级持续，制造和互连环节可能继续被重估",
            "小中盘弹性高，但流动性和客户集中度需要额外打折",
        ],
        "buyConditionZh": "只作为卫星候选；需要成交量、订单和客户集中度确认。",
    },
    "CLS": {
        "stageZh": "供应链兑现：AI server/封装制造",
        "score": 74,
        "demandLagMonths": "6-18",
        "reasonsZh": [
            "AI 服务器和先进封装相关制造需求可能滞后于 GPU capex 体现",
            "比上游龙头更靠近订单兑现，但周期和毛利率弹性需要跟踪",
            "若收入/利润率同步上行，可能继续享受供应链重估",
        ],
        "buyConditionZh": "适合等财报确认后跟随；若估值已经修复且价格远离 MA50，先观察。",
    },
    "VRT": {
        "stageZh": "物理基础设施：供电/冷却/机柜",
        "score": 73,
        "demandLagMonths": "12-24",
        "reasonsZh": [
            "AI 数据中心功率密度上升，冷却、供电、机柜系统价值量提升",
            "需求传导通常晚于 GPU capex，但订单能见度较高",
            "已经被市场较充分关注，next-SNDK 属性低于更隐蔽节点",
        ],
        "buyConditionZh": "只买回撤和财报确认，不追高；看 backlog、margin 和数据中心订单。",
    },
    "NVT": {
        "stageZh": "物理基础设施：电气连接/机柜配套",
        "score": 72,
        "demandLagMonths": "12-24",
        "reasonsZh": [
            "供配电、机柜和保护系统受益于数据中心 capex",
            "比热门 GPU/光模块拥挤度低，可能是后周期传导候选",
            "需要验证 AI 数据中心收入占比，而不是泛工业周期",
        ],
        "buyConditionZh": "优先作为低拥挤基础设施候选；估值合理且订单强再考虑。",
    },
    "POET": {
        "stageZh": "早期高风险：光互连/集成光子",
        "score": 66,
        "demandLagMonths": "12-24",
        "reasonsZh": [
            "集成光子/光引擎方向贴近 AI 光互连升级叙事",
            "若技术路线被大客户采纳，弹性可能很高",
            "但商业化、收入规模、流动性和融资风险都高，不能和 SNDK/ALAB 同等级处理",
        ],
        "buyConditionZh": "仅研究观察，不默认建仓；必须先补财务、客户、现金流和流动性数据。",
    },
}


TASK_SUMMARY_ZH = {
    "health_check": "完成系统健康检查；重点看数据源警告、OpenD 状态和待复核意图。",
    "macro_regime": "宏观/Fed 风险覆盖已更新，用于限制仓位和期权风险。",
    "watchlist_review": "观察池已复核，用于筛选可交易标的和排除项。",
    "intel_monitor": "情报监控已刷新；外部内容只作为灵感源。",
    "social_sentiment_feed": "社媒舆情 feed 已刷新；用于判断模型里的情绪和拥挤度 overlay。",
    "earnings_event_risk": "财报事件风险已刷新；相关标的的期权动作需要额外谨慎。",
    "pre_market_scan": "盘前检查清单已生成；不涉及券商下单。",
    "trading_signals": "交易信号已刷新；输出为观察/建议，不是订单。",
    "research_committee": "研究委员会已生成模拟复核结论。",
    "order_intents": "订单意图已刷新；所有内容仍为只读建议，不是券商订单。",
    "review_dashboard": "HTML 复核看板已生成。",
    "dashboard_snapshot": "交互式看板数据快照已刷新。",
    "post_market_summary": "盘后总结已生成。",
    "mid_day_review": "盘中复核已生成。",
    "fund_holdings_tracker": "基金经理持仓追踪已刷新，并更新候选池覆盖。",
    "congress_trades_tracker": "议员组合披露追踪已刷新；仍需核验官方披露。",
}


def _load(path: Path, default: Any) -> Any:
    try:
        return read_json(path, default)
    except (json.JSONDecodeError, OSError):
        return default


def _latest_strategy_compare() -> dict[str, Any]:
    result = _load(STRATEGY_COMPARE_PATH, {})
    if not result:
        return {
            "status": "MISSING",
            "messageZh": "尚未运行 strategy_compare_backtest。",
            "dataPolicy": {"moomooUsed": False},
            "ranking": [],
            "strategies": [],
        }
    return {
        "status": result.get("status", "UNKNOWN"),
        "generatedAt": result.get("generatedAt"),
        "range": result.get("range", {}),
        "transactionCostBps": result.get("transactionCostBps"),
        "dataPolicy": result.get("dataPolicy", {}),
        "warnings": result.get("warnings", []),
        "ranking": result.get("ranking", []),
        "strategies": [
            {
                "key": item.get("key"),
                "name": item.get("name"),
                "description": item.get("description"),
                "scoreModel": item.get("scoreModel"),
                "topN": item.get("topN"),
                "exposure": item.get("exposure"),
                "maxPosition": item.get("maxPosition"),
                "riskFilter": item.get("riskFilter"),
                "metrics": item.get("metrics", {}),
                "lastPositions": item.get("lastPositions", []),
            }
            for item in result.get("strategies", [])
        ],
    }


def _latest_json(folder: Path, pattern: str) -> dict[str, Any]:
    files = sorted(folder.glob(pattern))
    if not files:
        return {}
    return _load(files[-1], {})


def _as_pct(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
        if not math.isfinite(number):
            return None
        return round(number * 100, 2)
    except (TypeError, ValueError):
        return None


def _round(value: Any, digits: int = 2) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
        if not math.isfinite(number):
            return None
        return round(number, digits)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _effective_last(row: dict[str, Any]) -> float | None:
    for key in ("last", "regularMarketPrice", "price", "close"):
        value = _to_float(row.get(key))
        if value is not None:
            return value

    bid = _to_float(row.get("bid"))
    ask = _to_float(row.get("ask"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2

    sparkline = [_to_float(value) for value in row.get("sparkline", [])]
    sparkline = [value for value in sparkline if value is not None]
    return sparkline[-1] if sparkline else None


def _effective_day_change_pct(row: dict[str, Any]) -> float | None:
    value = _to_float(row.get("dayChangePct"))
    if value is not None:
        return value

    sparkline = [_to_float(item) for item in row.get("sparkline", [])]
    sparkline = [item for item in sparkline if item is not None]
    if len(sparkline) >= 2 and sparkline[-2]:
        return (sparkline[-1] - sparkline[-2]) / abs(sparkline[-2]) * 100
    return None


def _sparkline_values(row: dict[str, Any]) -> list[float]:
    values = [_to_float(value) for value in row.get("sparkline", [])]
    return [value for value in values if value is not None]


def _effective_ma50(row: dict[str, Any]) -> float | None:
    for key in ("ma50", "ma_50d", "movingAverage50d", "fiftyDayAverage"):
        value = _to_float(row.get(key))
        if value is not None:
            return value
    values = _sparkline_values(row)
    if not values:
        return None
    window = values[-50:] if len(values) >= 50 else values
    return sum(window) / len(window)


def _effective_above_ma50(row: dict[str, Any], last: float | None = None) -> bool | None:
    existing = row.get("aboveMa50")
    if existing is not None:
        return bool(existing)
    last_price = last if last is not None else _effective_last(row)
    ma50 = _effective_ma50(row)
    if last_price is None or ma50 is None:
        return None
    return last_price > ma50


def _price_to_ma50_pct(row: dict[str, Any], last: float | None = None) -> float | None:
    last_price = last if last is not None else _effective_last(row)
    ma50 = _effective_ma50(row)
    if last_price is None or not ma50:
        return None
    return (last_price - ma50) / abs(ma50) * 100


def _valuation_items(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    container = raw.get("symbols") or raw.get("rows") or raw.get("data") or raw
    rows = container.values() if isinstance(container, dict) else container if isinstance(container, list) else []
    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or row.get("ticker") or "").upper()
        if symbol:
            items[symbol] = row
    return items


def _valuation_map() -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for filename in ("valuation_latest.json", "valuation_overrides.json"):
        for symbol, row in _valuation_items(_load(ROOT / "data" / "market" / filename, {})).items():
            merged.setdefault(symbol, {}).update(row)
    return merged


def _valuation_for_symbol(symbol: str, valuation: dict[str, dict[str, Any]], last_price: float | None) -> dict[str, Any]:
    raw = valuation.get(symbol.upper(), {})
    trailing_pe = None
    for key in ("peRatio", "trailingPE", "trailingPe", "pe"):
        trailing_pe = _to_float(raw.get(key))
        if trailing_pe is not None:
            break
    forward_pe = None
    for key in ("forwardPE", "forwardPe"):
        forward_pe = _to_float(raw.get(key))
        if forward_pe is not None:
            break

    target = None
    for key in ("averageTargetPrice", "targetConsensus", "targetMedian", "priceTargetAverage", "targetMeanPrice", "targetPrice", "analystTargetPrice"):
        target = _to_float(raw.get(key))
        if target is not None:
            break

    upside = (target - last_price) / last_price * 100 if target is not None and last_price else None
    rating = raw.get("rating") or raw.get("recommendation") or raw.get("consensus")
    source = raw.get("source") or raw.get("sourceLabel") or raw.get("provider")
    return {
        "peRatio": _round(trailing_pe, 1),
        "trailingPE": _round(trailing_pe, 1),
        "forwardPE": _round(forward_pe, 1),
        "pegRatio": _round(raw.get("pegRatio"), 2),
        "revenueGrowth": _round(raw.get("revenueGrowth"), 4),
        "earningsGrowth": _round(raw.get("earningsGrowth"), 4),
        "grossMargin": _round(raw.get("grossMargin"), 4),
        "operatingMargin": _round(raw.get("operatingMargin"), 4),
        "profitMargin": _round(raw.get("profitMargin"), 4),
        "returnOnEquity": _round(raw.get("returnOnEquity"), 4),
        "debtToEquity": _round(raw.get("debtToEquity"), 2),
        "currentRatio": _round(raw.get("currentRatio"), 2),
        "enterpriseToRevenue": _round(raw.get("enterpriseToRevenue"), 2),
        "enterpriseToEbitda": _round(raw.get("enterpriseToEbitda"), 2),
        "beta": _round(raw.get("beta"), 2),
        "wallStreetTargetPrice": _round(target, 2),
        "targetUpsidePct": _round(upside, 1),
        "analystRating": rating,
        "numberOfAnalysts": _round(raw.get("numberOfAnalysts"), 0),
        "valuationScore": _round(raw.get("valuationScore"), 1),
        "valuationBucketZh": raw.get("valuationBucketZh"),
        "valuationFlagsZh": raw.get("valuationFlagsZh", []),
        "valuationSource": source,
        "valuationUpdatedAt": raw.get("updatedAt") or raw.get("asOf") or raw.get("timestamp"),
        "valuationDataQuality": "PASS" if trailing_pe is not None or forward_pe is not None or target is not None else "MISSING",
    }


def _normalize_market_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    last = _effective_last(normalized)
    day_change = _effective_day_change_pct(normalized)
    if last is not None:
        normalized["last"] = _round(last, 4)
        normalized.setdefault("value", _round(last, 4))
    ma50 = _effective_ma50(normalized)
    if ma50 is not None:
        normalized["ma50"] = _round(ma50, 4)
        normalized["priceToMa50Pct"] = _round(_price_to_ma50_pct(normalized, last), 2)
        normalized["aboveMa50"] = _effective_above_ma50(normalized, last)
    if day_change is not None:
        normalized["dayChangePct"] = _round(day_change, 2)
    if not normalized.get("dataQuality"):
        normalized["dataQuality"] = "PASS" if last is not None or normalized.get("sparkline") else "MISSING"
    return normalized


def _recent_trade_log(limit: int = 12) -> list[dict[str, Any]]:
    log = _load(ROOT / "trade-log.json", {"records": []})
    records = log.get("records", [])
    return [
        {
            "timestamp": row.get("timestamp"),
            "task": row.get("task"),
            "status": row.get("status"),
            "summary": row.get("summary"),
            "summaryZh": TASK_SUMMARY_ZH.get(row.get("task")),
            "report": row.get("report"),
        }
        for row in records[-limit:]
    ]


def _condition_label(condition: dict[str, Any]) -> str:
    metric_labels = {
        "price": "价格",
        "day_change_pct": "日内涨跌",
        "vix": "VIX",
        "dgs10": "10Y",
        "usdcnh": "USD/CNH",
        "qqq_day_change_pct": "QQQ",
        "sector_day_change_pct": "板块",
    }
    metric = str(condition.get("metric") or "")
    symbol = condition.get("symbol")
    operator = condition.get("operator") or ""
    value = condition.get("value")
    label = metric_labels.get(metric, metric or "条件")
    if symbol and metric == "price":
        label = f"{symbol} 价格"
    elif symbol and metric.endswith("day_change_pct"):
        label = f"{symbol} 日内涨跌"
    return f"{label} {operator} {value}"


def _conditional_playbook_summary(playbook: dict[str, Any]) -> dict[str, Any]:
    sessions = playbook.get("sessions", []) if isinstance(playbook, dict) else []
    if not sessions:
        return {
            "status": "MISSING",
            "latestSession": None,
            "policy": playbook.get("policy", {}) if isinstance(playbook, dict) else {},
        }

    latest = sessions[-1]
    scenarios = []
    for scenario in latest.get("scenarios", []):
        conditions = scenario.get("conditions", [])
        scenarios.append(
            {
                "id": scenario.get("id"),
                "symbol": scenario.get("symbol"),
                "side": scenario.get("side"),
                "instrumentType": scenario.get("instrument_type"),
                "orderType": scenario.get("order_type"),
                "targetWeightPct": _round(_money(scenario.get("target_weight")) * 100, 2),
                "maxRiskPct": _round(_money(scenario.get("max_risk_pct")) * 100, 2),
                "status": scenario.get("status"),
                "validAfter": scenario.get("valid_after"),
                "validUntil": scenario.get("valid_until"),
                "approval": scenario.get("approval", {}),
                "conditions": conditions,
                "conditionSummaryZh": "；".join(_condition_label(item) for item in conditions),
                "entryTriggerZh": scenario.get("entry_trigger"),
                "invalidationZh": scenario.get("invalidation"),
                "rationaleZh": scenario.get("rationale"),
            }
        )

    return {
        "status": latest.get("status"),
        "policy": playbook.get("policy", {}),
        "latestSession": {
            "id": latest.get("id"),
            "date": latest.get("date"),
            "status": latest.get("status"),
            "title": latest.get("title"),
            "createdAt": latest.get("created_at"),
            "updatedAt": latest.get("updated_at"),
            "validAfter": min((row.get("validAfter") for row in scenarios if row.get("validAfter")), default=None),
            "validUntil": max((row.get("validUntil") for row in scenarios if row.get("validUntil")), default=None),
            "scenarios": scenarios,
        },
    }


def _strategy_label(strategy: Any) -> str:
    labels = {
        "zero_position_starter": "零仓位启动组合",
        "pre_market_signal": "盘前信号",
    }
    return labels.get(str(strategy), str(strategy or "未命名策略"))


def _starter_order_copy(symbol: str) -> dict[str, str]:
    base = {
        "rationaleZh": "当前账户为零仓位，市场状态偏多但宏观偏鹰，因此只做小比例股票/ETF模拟首仓，不开启期权风险。",
        "entryTriggerZh": "仅在美股开盘后 15-30 分钟观察确认：价格守住开盘 VWAP，且没有明显风险反转时，才假设纸面入场。",
        "invalidationZh": "如果价格跌破前一交易日低点、跌回 MA50 下方，或市场状态转弱，则暂停或降低该模拟仓位。",
    }
    overrides = {
        "QQQ": {
            "rationaleZh": "用 QQQ 作为核心科技敞口的第一笔模拟仓位；宏观偏鹰，所以仓位保持保守。",
            "invalidationZh": "如果 QQQ 跌破前一交易日低点，或市场状态不再偏多，则暂停该模拟仓位。",
        },
        "NVDA": {
            "rationaleZh": "NVDA 趋势仍强且基金经理共识高，但半导体拥挤度较高，所以只给小比例首仓。",
            "invalidationZh": "如果 NVDA 收回 MA50 下方，或半导体板块出现明显风险反转，则暂停或降低该模拟仓位。",
        },
        "AMZN": {
            "rationaleZh": "AMZN 近期动量和趋势较强，适合做小比例股票首仓；宏观偏鹰下不追高。",
            "entryTriggerZh": "仅在开盘后 15-30 分钟确认守住开盘 VWAP，且没有超过 2% 高开后无延续时，才假设纸面入场。",
        },
        "GOOG": {
            "rationaleZh": "GOOG 近期动量和趋势较强，适合做小比例股票首仓；宏观偏鹰下不追高。",
            "entryTriggerZh": "仅在开盘后 15-30 分钟确认守住开盘 VWAP，且没有超过 2% 高开后无延续时，才假设纸面入场。",
        },
    }
    base.update(overrides.get(symbol, {}))
    return base


def _staged_orders(staged: dict[str, Any]) -> list[dict[str, Any]]:
    orders = []
    for order in staged.get("orders", []):
        intent = order.get("intent", {})
        symbol = intent.get("normalized_symbol") or intent.get("symbol")
        market_guard = {}
        for guard in order.get("guard_result", {}).get("guards", []):
            if guard.get("name") == "market_data":
                market_guard = guard.get("details", {})
                break
        zh_copy = _starter_order_copy(str(symbol)) if intent.get("strategy") == "zero_position_starter" else {}
        orders.append(
            {
                "intentId": order.get("intent_id"),
                "status": order.get("status"),
                "guardStatus": order.get("guard_result", {}).get("status"),
                "symbol": symbol,
                "side": intent.get("side"),
                "instrumentType": intent.get("instrument_type"),
                "targetWeightPct": _as_pct(intent.get("target_weight")),
                "strategy": intent.get("strategy"),
                "strategyLabel": _strategy_label(intent.get("strategy")),
                "rationale": intent.get("rationale"),
                "rationaleZh": zh_copy.get("rationaleZh"),
                "entryTrigger": intent.get("entry_trigger"),
                "entryTriggerZh": zh_copy.get("entryTriggerZh"),
                "invalidation": intent.get("invalidation"),
                "invalidationZh": zh_copy.get("invalidationZh"),
                "lastPrice": _round(market_guard.get("last_price")),
                "momentum30dPct": _round(market_guard.get("momentum_30d_pct")),
                "aboveMa50": market_guard.get("above_ma50"),
                "notes": order.get("notes"),
            }
        )
    return orders


def _symbol_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("symbol") or "").upper(): row for row in rows if row.get("symbol")}


def _market_symbol_rows(market: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("indices", "macroProxies", "sectorEtfs", "watchSymbols"):
        rows.extend(market.get(key, []) or [])
    return _symbol_map(rows)


def _social_symbol_rows(social: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return _symbol_map(social.get("symbolSignals", []) or [])


def _event_symbol_rows(earnings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return _symbol_map(_earnings_events(earnings))


def _manager_score_rows(fund: dict[str, Any]) -> dict[str, float]:
    scores = fund.get("backtest_feed", {}).get("normalized_symbol_scores", {}) or {}
    return {str(symbol).upper(): float(score or 0) for symbol, score in scores.items()}


def _enrich_order_reasons(
    orders: list[dict[str, Any]],
    fund: dict[str, Any],
    social: dict[str, Any],
    earnings: dict[str, Any],
    market: dict[str, Any],
) -> None:
    market_rows = _market_symbol_rows(market)
    social_rows = _social_symbol_rows(social)
    event_rows = _event_symbol_rows(earnings)
    manager_scores = _manager_score_rows(fund)
    for order in orders:
        symbol = str(order.get("symbol") or "").upper()
        reasons: list[dict[str, Any]] = []
        market_row = market_rows.get(symbol, {})
        momentum = order.get("momentum30dPct")
        if momentum is None:
            momentum = market_row.get("momentum30dPct")
        above_ma50 = order.get("aboveMa50")
        if above_ma50 is None:
            above_ma50 = market_row.get("aboveMa50")
        if momentum is not None:
            reasons.append(
                {
                    "category": "技术面",
                    "title": f"30日动量 {float(momentum):.2f}%",
                    "detail": "价格仍在 MA50 上方，趋势确认度较高。" if above_ma50 else "价格未站稳 MA50，需要降低仓位或等待确认。",
                    "strength": 0.82 if above_ma50 and float(momentum) > 0 else 0.48,
                }
            )
        manager_score = manager_scores.get(symbol, 0.0)
        if manager_score:
            reasons.append(
                {
                    "category": "机构持仓",
                    "title": f"基金经理共识 {manager_score:.2f}",
                    "detail": "公开披露持仓给出正向 idea overlay，但 13F/基金披露存在滞后。",
                    "strength": min(0.95, 0.45 + manager_score * 0.45),
                }
            )
        social_row = social_rows.get(symbol, {})
        if social_row:
            reasons.append(
                {
                    "category": "新闻/舆情",
                    "title": f"{social_row.get('sentimentLabelZh') or social_row.get('sentimentLabel') or '中性'} / 拥挤 {social_row.get('crowdingRisk', 'n/a')}",
                    "detail": f"来源覆盖 {social_row.get('mentionCount', 0)} 条，作为置信度和拥挤度 overlay。",
                    "strength": 0.74 if social_row.get("sentimentLabel") == "POSITIVE" else 0.55,
                }
            )
        event_row = event_rows.get(symbol, {})
        if event_row:
            risk = event_row.get("riskLevel", "UNKNOWN")
            reasons.append(
                {
                    "category": "事件风险",
                    "title": f"财报/事件风险 {risk}",
                    "detail": "若处于财报窗口，避免新开裸多 call/put，优先 defined-risk 或等待事件后。",
                    "strength": 0.9 if risk == "HIGH" else 0.62,
                }
            )
        if order.get("guardStatus"):
            reasons.append(
                {
                    "category": "风控",
                    "title": f"Guard: {order.get('guardStatus')}",
                    "detail": "所有动作仍需要人工批准；只允许 advisory-only / paper-trade。",
                    "strength": 0.7,
                }
            )
        reasons.sort(key=lambda row: row.get("strength", 0), reverse=True)
        order["topReasons"] = reasons[:3]


def _manager_ideas(fund: dict[str, Any], limit: int = 18) -> list[dict[str, Any]]:
    feed = fund.get("backtest_feed", {})
    normalized = feed.get("normalized_symbol_scores", {})
    raw_scores = feed.get("symbol_scores", {})
    ideas = []
    for symbol, score in sorted(normalized.items(), key=lambda item: item[1], reverse=True)[:limit]:
        ideas.append(
            {
                "symbol": symbol,
                "score": _round(score, 3),
                "rawScore": _round(raw_scores.get(symbol), 3),
            }
        )
    return ideas


def _top_holding_sources(fund: dict[str, Any], symbols: set[str]) -> dict[str, list[dict[str, Any]]]:
    by_symbol: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
    for row in fund.get("holdings", []):
        symbol = row.get("symbol")
        if symbol not in by_symbol:
            continue
        by_symbol[symbol].append(
            {
                "manager": row.get("manager"),
                "firm": row.get("firm"),
                "vehicle": row.get("vehicle"),
                "weightPct": _round(row.get("weight_pct")),
                "asOf": row.get("as_of") or row.get("filing_date"),
            }
        )
    for symbol, rows in by_symbol.items():
        rows.sort(key=lambda item: item.get("weightPct") or 0, reverse=True)
        by_symbol[symbol] = rows[:4]
    return by_symbol


def _earnings_events(earnings: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": row.get("symbol"),
            "company": row.get("company"),
            "date": row.get("earnings_date"),
            "time": row.get("time"),
            "daysUntil": row.get("days_until"),
            "riskLevel": row.get("risk_level"),
            "callAction": row.get("option_playbook", {}).get("call_action"),
            "callActionZh": "财报窗口内不要新开 long call 或 covered call；如已有短 call，先复核是否需要平仓或滚动。",
            "putAction": row.get("option_playbook", {}).get("put_action"),
            "putActionZh": "财报窗口内不要新开 long put 或 cash-secured put；先复核跳空和指派风险。",
            "source": row.get("source"),
            "notes": row.get("notes"),
        }
        for row in earnings.get("events", [])
    ]


def _congress_signals(congress: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": row.get("symbol"),
            "netScore": _round(row.get("net_score"), 3),
            "buyCount": row.get("buy_count"),
            "sellCount": row.get("sell_count"),
            "memberCount": row.get("member_count"),
            "members": row.get("members", []),
            "requiresVerification": any(
                trade.get("official_verification_required")
                for trade in row.get("trades", [])
            ),
        }
        for row in congress.get("signals", [])
    ]


def _social_symbol_map(social: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("symbol")): row for row in social.get("symbolSignals", []) if row.get("symbol")}


def _watchlist_group(symbol: str) -> str:
    for group, symbols in WATCHLIST_GROUPS.items():
        if symbol in symbols:
            return group
    return "其他观察"


def _ai_chain_for_symbol(symbol: str) -> dict[str, Any]:
    layers = AI_CHAIN_BY_SYMBOL.get(str(symbol).upper(), [])
    primary = layers[0] if layers else None
    return {
        "primaryId": primary["id"] if primary else "other",
        "primaryNameZh": primary["nameZh"] if primary else "其他",
        "roleZh": primary["descriptionZh"] if primary else "暂未归入 AI 数据中心产业链分类。",
        "layers": [
            {
                "id": layer["id"],
                "nameZh": layer["nameZh"],
                "descriptionZh": layer["descriptionZh"],
            }
            for layer in layers
        ],
    }


def _ai_repricing_overlay(symbol: str) -> dict[str, Any]:
    overlay = AI_REPRICING_OVERLAYS.get(str(symbol or "").upper())
    if not overlay:
        return {}
    return {
        "aiRepricingStageZh": overlay.get("stageZh"),
        "aiRepricingScore": _round(overlay.get("score"), 1),
        "aiDemandLagMonths": overlay.get("demandLagMonths"),
        "aiRepricingReasonsZh": overlay.get("reasonsZh", [])[:3],
        "aiRepricingBuyConditionZh": overlay.get("buyConditionZh"),
        "aiRepricingSourceZh": "小红书「美股芒格君」AI 产业链框架：需求传导时间差 + Qual 周期 + 架构新节点重估；仅作叙事发现 overlay，不能单独触发交易。",
    }


def _activist_alpha_overlay(symbol: str) -> dict[str, Any]:
    overlay = ACTIVIST_ALPHA_OVERLAYS.get(str(symbol or "").upper())
    if not overlay:
        return {}
    score_values = [
        _money(overlay.get("opportunityScore")),
        _money(overlay.get("scannerScore")),
    ]
    score_values = [value for value in score_values if value > 0]
    score = max(score_values) if score_values else None
    return {
        "activistAlpha": {
            "sourceUrl": ACTIVIST_ALPHA_SOURCE_URL,
            "investor": overlay.get("investor"),
            "stageId": overlay.get("stageId"),
            "stageZh": overlay.get("stageZh"),
            "scannerScore": _round(overlay.get("scannerScore"), 1),
            "opportunityScore": _round(overlay.get("opportunityScore"), 1),
            "score": _round(score, 1),
            "coreRelevance": overlay.get("coreRelevance"),
            "thesisTypeZh": overlay.get("thesisTypeZh"),
            "catalystZh": overlay.get("catalystZh"),
            "riskZh": overlay.get("riskZh"),
            "sourceNoteZh": overlay.get("sourceNoteZh"),
        },
        "activistAlphaScore": _round(score, 1),
        "activistStageZh": overlay.get("stageZh"),
        "activistCatalystZh": overlay.get("catalystZh"),
        "activistCoreRelevance": overlay.get("coreRelevance"),
    }


def _breadth_summary(rows: list[dict[str, Any]], *, symbols: set[str] | None = None) -> dict[str, Any]:
    selected = [
        row
        for row in rows
        if symbols is None or str(row.get("symbol") or "").upper() in symbols
    ]
    with_change = [row for row in selected if _to_float(row.get("dayChangePct")) is not None]
    up = [row for row in with_change if _to_float(row.get("dayChangePct")) and _to_float(row.get("dayChangePct")) > 0]
    down = [row for row in with_change if _to_float(row.get("dayChangePct")) and _to_float(row.get("dayChangePct")) < 0]
    flat = [row for row in with_change if _to_float(row.get("dayChangePct")) == 0]
    changes = [_to_float(row.get("dayChangePct")) for row in with_change]
    changes = [value for value in changes if value is not None]
    ranked = sorted(with_change, key=lambda row: _to_float(row.get("dayChangePct")) or 0, reverse=True)
    return {
        "count": len(selected),
        "pricedCount": len(with_change),
        "upCount": len(up),
        "downCount": len(down),
        "flatCount": len(flat),
        "missingCount": max(0, len(selected) - len(with_change)),
        "avgDayChangePct": _round(sum(changes) / len(changes), 2) if changes else None,
        "breadthPct": _round(len(up) / len(with_change) * 100, 1) if with_change else None,
        "topMovers": [
            {"symbol": row.get("symbol"), "dayChangePct": _round(row.get("dayChangePct"), 2)}
            for row in ranked[:3]
        ],
        "laggards": [
            {"symbol": row.get("symbol"), "dayChangePct": _round(row.get("dayChangePct"), 2)}
            for row in list(reversed(ranked[-3:])) if ranked
        ],
    }


def _watchlist_group_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for group in sorted({row.get("group") for row in rows if row.get("group")}):
        group_rows = [row for row in rows if row.get("group") == group]
        summary = _breadth_summary(group_rows)
        summary.update({"group": group, "nameZh": group})
        summaries.append(summary)
    summaries.sort(key=lambda row: row.get("avgDayChangePct") if row.get("avgDayChangePct") is not None else -999, reverse=True)
    return summaries


def _ai_chain_layer_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for layer in AI_CHAIN_LAYERS:
        layer_rows = [
            row
            for row in rows
            if any(item.get("id") == layer["id"] for item in row.get("aiChainLayers", []))
        ]
        symbols = [row["symbol"] for row in layer_rows]
        summary = _breadth_summary(layer_rows)
        summary.update(
            {
                "id": layer["id"],
                "nameZh": layer["nameZh"],
                "descriptionZh": layer["descriptionZh"],
                "count": len(symbols),
                "symbols": symbols[:14],
            }
        )
        summaries.append(summary)
    return summaries


def _standard_news_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": row.get("title"),
        "url": row.get("url"),
        "sourceLabel": row.get("sourceLabel") or row.get("source_label") or row.get("sourceId") or row.get("source_id"),
        "publishedAt": row.get("publishedAt") or row.get("published_at"),
        "sentimentLabel": row.get("sentimentLabel") or row.get("sentiment_label"),
        "score": row.get("score"),
    }


def _news_items_by_symbol(social: dict[str, Any], intel: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for signal in social.get("symbolSignals", []):
        symbol = str(signal.get("symbol") or "")
        if not symbol:
            continue
        for item in signal.get("topItems", []) or []:
            by_symbol.setdefault(symbol, []).append(_standard_news_item(item))

    for item in social.get("catalystWatch", []) or []:
        for symbol in item.get("symbols", []) or []:
            by_symbol.setdefault(str(symbol), []).append(_standard_news_item(item))

    for item in intel.get("items", []) or []:
        for symbol in item.get("symbol_hits", []) or []:
            by_symbol.setdefault(str(symbol), []).append(_standard_news_item(item))

    deduped: dict[str, list[dict[str, Any]]] = {}
    for symbol, rows in by_symbol.items():
        seen: set[str] = set()
        clean_rows = []
        for row in rows:
            key = str(row.get("url") or row.get("title") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            clean_rows.append(row)
        clean_rows.sort(key=lambda item: (item.get("publishedAt") or "", item.get("score") or 0), reverse=True)
        deduped[symbol] = clean_rows[:4]
    return deduped


def _watchlist_stance(symbol: str, market_row: dict[str, Any], event: dict[str, Any], social: dict[str, Any]) -> str:
    if event:
        risk = str(event.get("risk_level") or event.get("riskLevel") or "").upper()
        if risk == "HIGH":
            return "财报窗口：不新开期权；等结果、盘后反应和次日成交量确认。"

    if not market_row or market_row.get("dataQuality") == "MISSING":
        return "行情缺口：先只观察，等下一轮 OpenBB/Yahoo 快照补齐。"

    day = _effective_day_change_pct(market_row)
    momentum = _to_float(market_row.get("momentum30dPct")) or 0.0
    above_ma50 = market_row.get("aboveMa50")
    crowding = str(social.get("crowdingRisk") or "").upper()

    if day is not None and day <= -5:
        return "单日大跌：先查新闻/财报原因，不接飞刀；只考虑小仓位纸面观察。"
    if momentum >= 20 and above_ma50:
        return "强趋势：适合列为优先观察，但只等回踩或开盘后确认，不追高。"
    if momentum >= 5 and above_ma50:
        return "趋势健康：可放入建仓候选，仍需看大盘、VIX、利率和开盘 VWAP。"
    if above_ma50 is False or momentum < 0:
        return "趋势偏弱：暂不主动建仓，除非出现财报后重新定价或明确反转。"
    if crowding == "HIGH":
        return "叙事拥挤：降低追涨权重，更多用来识别风险和分歧。"
    return "观察：等待更强的价格、新闻或事件确认。"


def _buy_condition_zh(
    symbol: str,
    market_row: dict[str, Any],
    event: dict[str, Any],
    social: dict[str, Any],
    valuation: dict[str, Any],
) -> str:
    risk = str(event.get("risk_level") or event.get("riskLevel") or "").upper() if event else ""
    if risk == "HIGH":
        return "不买/不新开期权：等财报结果、盘后反应和次日成交量确认。"

    if not market_row or market_row.get("dataQuality") == "MISSING":
        return "暂不买：行情缺口，先等下一轮快照恢复。"

    day = _effective_day_change_pct(market_row)
    momentum = _to_float(market_row.get("momentum30dPct"))
    above_ma50 = market_row.get("aboveMa50")
    crowding = str(social.get("crowdingRisk") or "").upper()
    target_upside = _to_float(valuation.get("targetUpsidePct"))
    trailing_pe = _to_float(valuation.get("peRatio"))
    forward_pe = _to_float(valuation.get("forwardPE"))
    valuation_quality = valuation.get("valuationDataQuality")
    valuation_score = _to_float(valuation.get("valuationScore"))

    if valuation_quality != "PASS":
        return "暂不高置信买入：PE/目标价缺数据；只能作为技术/事件观察。"
    if valuation_score is not None and valuation_score < 45:
        return "估值不支持主动买入：除非出现财报上修或重大催化，否则只观察。"

    if target_upside is not None and target_upside < 5 and (momentum or 0) > 10:
        return "不追：华尔街目标价上行不足，除非新催化重新抬高估值锚。"
    if trailing_pe is not None and forward_pe is not None and trailing_pe > forward_pe * 3 and target_upside is not None and target_upside < 5:
        return "Forward PE 看似便宜，但华尔街目标价没有上行；先按周期股折扣处理，不主动追。"
    if forward_pe is not None and forward_pe > 80 and target_upside is not None and target_upside < 15:
        return "估值偏满：只等回踩或业绩上修，不做追高买入。"
    if day is not None and day >= 8:
        return "不追高：等回踩 VWAP/前高不破，或连续两日消化后再看。"
    if day is not None and day <= -5:
        return "不接飞刀：先确认下跌来自一次性消息还是基本面恶化。"
    if momentum is not None and momentum >= 20 and above_ma50:
        return "优先候选：回踩不破 VWAP/前高，且板块继续强，可小仓 paper starter。"
    if momentum is not None and momentum >= 5 and above_ma50:
        return "可买条件：开盘后 15-30 分钟守住 VWAP，大盘/VIX 不转弱。"
    if above_ma50 is False or (momentum is not None and momentum < 0):
        return "暂不买：等收回 MA50 或出现财报/新闻后的明确反转。"
    if crowding == "HIGH":
        return "降低权重：叙事拥挤，只在价格确认且仓位很小的情况下试。"
    return "观察候选：需要价格强度、新闻催化或估值上行空间再确认。"


def _score_high_good(value: Any, points: list[tuple[float, float]]) -> float | None:
    number = _to_float(value)
    if number is None:
        return None
    for threshold, score in points:
        if number >= threshold:
            return score
    return points[-1][1] if points else None


def _score_low_good(value: Any, points: list[tuple[float, float]]) -> float | None:
    number = _to_float(value)
    if number is None:
        return None
    for threshold, score in points:
        if number <= threshold:
            return score
    return points[-1][1] if points else None


def _avg_scores(scores: list[float | None]) -> tuple[float | None, int, int]:
    valid = [score for score in scores if score is not None]
    return (sum(valid) / len(valid) if valid else None, len(valid), len(scores))


def _lookthrough_config(symbol: str) -> dict[str, Any]:
    return ETF_LOOKTHROUGH.get(str(symbol or "").upper(), {})


def _avg_numeric(values: list[Any]) -> float | None:
    numbers = [_to_float(value) for value in values]
    numbers = [number for number in numbers if number is not None]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _lookthrough_market_row(symbol: str, own_row: dict[str, Any], market_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    config = _lookthrough_config(symbol)
    if not config:
        return own_row

    merged = dict(own_row)
    underlyings = [str(item).upper() for item in config.get("underlyingSymbols", [])]
    rows = [market_rows.get(item, {}) for item in underlyings]
    rows = [row for row in rows if row]

    if str(symbol).upper() in LEVERAGED_ETF_TOOL_SYMBOLS:
        primary = str(config.get("primarySymbol") or "").upper()
        source = market_rows.get(primary, {})
        if source:
            for key in ("dayChangePct", "momentum30dPct", "aboveMa50"):
                if merged.get(key) is None:
                    merged[key] = source.get(key)
            if not merged.get("sparkline") and source.get("sparkline"):
                merged["sparkline"] = source.get("sparkline")
            merged["lookThroughSourceSymbol"] = primary
        return merged

    if rows:
        for key in ("dayChangePct", "momentum30dPct"):
            if merged.get(key) is None:
                merged[key] = _avg_numeric([row.get(key) for row in rows])
        if not merged.get("sparkline"):
            first_sparkline = next((row.get("sparkline") for row in rows if row.get("sparkline")), None)
            if first_sparkline:
                merged["sparkline"] = first_sparkline
        above_values = [row.get("aboveMa50") for row in rows if row.get("aboveMa50") is not None]
        if merged.get("aboveMa50") is None and above_values:
            merged["aboveMa50"] = sum(1 for value in above_values if value) / len(above_values) >= 0.5
        merged["lookThroughBreadthPct"] = _round(sum(1 for value in above_values if value) / len(above_values) * 100, 1) if above_values else None
    return merged


def _lookthrough_summary(symbol: str, market_rows: dict[str, dict[str, Any]], valuations: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    config = _lookthrough_config(symbol)
    if not config:
        return None

    items = []
    for underlying in config.get("underlyingSymbols", []):
        key = str(underlying).upper()
        market_row = market_rows.get(key, {})
        valuation = valuations.get(key, {})
        items.append(
            {
                "symbol": key,
                "labelZh": UNDERLYING_LABELS.get(key, key),
                "last": _effective_last(market_row),
                "dayChangePct": _round(_effective_day_change_pct(market_row), 2),
                "momentum30dPct": _round(_to_float(market_row.get("momentum30dPct")), 2),
                "priceToMa50Pct": _round(_price_to_ma50_pct(market_row), 2),
                "forwardPE": _round(valuation.get("forwardPE"), 2),
                "targetUpsidePct": _round(valuation.get("targetUpsidePct"), 2),
                "dataQuality": market_row.get("dataQuality") or valuation.get("dataQuality") or "MISSING",
            }
        )

    available = [
        item
        for item in items
        if item.get("last") is not None
        or item.get("momentum30dPct") is not None
        or item.get("forwardPE") is not None
        or item.get("targetUpsidePct") is not None
    ]
    positive_momentum = [item for item in items if _to_float(item.get("momentum30dPct")) is not None and (_to_float(item.get("momentum30dPct")) or 0) > 0]
    extended = [item for item in items if _to_float(item.get("priceToMa50Pct")) is not None and (_to_float(item.get("priceToMa50Pct")) or 0) > 25]
    summary = config.get("summaryZh") or ""
    if available:
        summary += f" 当前底层可读 {len(available)}/{len(items)}；30日动量为正 {len(positive_momentum)} 个；离MA50过远 {len(extended)} 个。"
    else:
        summary += " 当前底层行情缺口较大，需等待数据源恢复。"

    return {
        "typeZh": config.get("typeZh"),
        "primarySymbol": config.get("primarySymbol"),
        "underlyingSymbols": [item["symbol"] for item in items],
        "summaryZh": summary,
        "items": items,
    }


def _decision_model_v1(
    symbol: str,
    market_row: dict[str, Any],
    event: dict[str, Any],
    social: dict[str, Any],
    valuation: dict[str, Any],
    manager_score: float,
    congress_score: float,
) -> dict[str, Any]:
    normalized_symbol = str(symbol or "").upper()
    if normalized_symbol in ETF_TOOL_SYMBOLS:
        momentum = _to_float(market_row.get("momentum30dPct"))
        day_change = _effective_day_change_pct(market_row)
        price_to_ma50 = _price_to_ma50_pct(market_row)
        social_net = _to_float(social.get("netSentiment"))
        crowding = str(social.get("crowdingRisk") or "").upper()
        is_leveraged = normalized_symbol in LEVERAGED_ETF_TOOL_SYMBOLS

        technical_score, technical_have, technical_need = _avg_scores([
            _score_high_good(momentum, [(20, 82), (5, 70), (0, 55), (-999, 30)]),
            _score_low_good(abs(price_to_ma50) if price_to_ma50 is not None else None, [(5, 78), (12, 68), (25, 48), (999999, 25)]),
            _score_low_good(day_change, [(-5, 35), (3, 72), (8, 55), (999999, 30)]) if day_change is not None else None,
        ])
        if technical_score is not None and price_to_ma50 is not None and price_to_ma50 > 25:
            technical_score = min(technical_score, 45)

        catalyst_score = 50.0
        if social_net is not None:
            catalyst_score += min(8, max(-8, social_net * 20))
        if normalized_symbol == "DRAM":
            catalyst_score += 10
        if is_leveraged:
            catalyst_score += 4
        if crowding == "HIGH":
            catalyst_score -= 10
        catalyst_score = max(0.0, min(100.0, catalyst_score))

        risk_score = 55.0 if is_leveraged else 68.0
        if crowding == "HIGH":
            risk_score -= 12
        if price_to_ma50 is not None and price_to_ma50 > 25:
            risk_score -= 12
        risk_score = max(0.0, min(100.0, risk_score))

        group_scores = {
            "fundamental": None,
            "valuation": None,
            "catalyst": catalyst_score,
            "technical": technical_score,
            "risk": risk_score,
        }
        weights = {"catalyst": 0.25, "technical": 0.45, "risk": 0.30}
        weighted_sum = 0.0
        used_weight = 0.0
        for key, weight in weights.items():
            score = group_scores.get(key)
            if score is not None:
                weighted_sum += score * weight
                used_weight += weight
        raw_score = weighted_sum / used_weight if used_weight else 0.0

        available = technical_have + 2 + int(social_net is not None)
        required = technical_need + 4
        completeness = available / required * 100 if required else 0.0
        capped_score = raw_score
        if completeness < 55:
            capped_score = min(capped_score, 60)

        blockers: list[str] = []
        if is_leveraged:
            blockers.append("2x日内杠杆工具，不适合默认隔夜")
        if price_to_ma50 is not None and price_to_ma50 > 25:
            blockers.append("底层/工具价格离MA50过远")
        if day_change is not None and day_change >= 8:
            blockers.append("单日涨幅过大，不追高")

        missing = []
        if momentum is None:
            missing.append("30日动量")
        if price_to_ma50 is None:
            missing.append("MA50距离")
        if social_net is None:
            missing.append("社媒情绪")
        missing.extend(["实时成交量/价差", "期权链流动性"])

        reasons: list[str] = []
        if normalized_symbol == "DRAM":
            reasons.append("纯memory basket，覆盖Samsung/SK Hynix/MU/SNDK/WDC/STX")
        elif normalized_symbol == "SNXX":
            reasons.append("2x SNDK日内工具，放大SanDisk方向")
        elif normalized_symbol == "MULL":
            reasons.append("2x MU日内工具，放大Micron方向")
        else:
            reasons.append("半导体/市场ETF工具")
        if momentum is not None:
            reasons.append(f"30日动量 {momentum:+.1f}%")
        if price_to_ma50 is not None:
            reasons.append(f"距MA50 {price_to_ma50:+.1f}%")

        if is_leveraged:
            tier = "高风险工具"
            condition = "仅作日内/极短线 paper 工具：必须确认底层股票方向、成交量和价差；默认不隔夜，不自动执行。"
        elif normalized_symbol == "DRAM":
            tier = "板块工具"
            condition = "板块表达工具：当 MU/SNDK/WDC/STX 至少三只同向走强且DRAM守住VWAP时，可替代单股小仓表达。"
        else:
            tier = "ETF工具"
            condition = "ETF工具：用于板块确认或替代单股风险；不按个股PE/目标价直接评分。"
        if blockers:
            condition = condition + " 风险提示：" + "；".join(blockers[:2]) + "。"

        quality = "PASS" if completeness >= 70 else "WARN"
        return {
            "decisionScore": _round(capped_score, 1),
            "rawDecisionScore": _round(raw_score, 1),
            "decisionTierZh": tier,
            "decisionDataQuality": quality,
            "decisionDataCompletenessPct": _round(completeness, 1),
            "factorScores": {key: _round(value, 1) for key, value in group_scores.items()},
            "topDecisionReasonsZh": reasons[:3],
            "missingDecisionDataZh": missing[:5],
            "blockerFlagsZh": blockers[:5],
            "buyConditionZh": condition,
        }

    revenue_growth = _to_float(valuation.get("revenueGrowth"))
    earnings_growth = _to_float(valuation.get("earningsGrowth"))
    profit_margin = _to_float(valuation.get("profitMargin"))
    roe = _to_float(valuation.get("returnOnEquity"))
    debt_to_equity = _to_float(valuation.get("debtToEquity"))
    current_ratio = _to_float(valuation.get("currentRatio"))
    trailing_pe = _to_float(valuation.get("peRatio"))
    forward_pe = _to_float(valuation.get("forwardPE"))
    peg = _to_float(valuation.get("pegRatio"))
    target_upside = _to_float(valuation.get("targetUpsidePct"))
    analyst_count = _to_float(valuation.get("numberOfAnalysts"))
    momentum = _to_float(market_row.get("momentum30dPct"))
    day_change = _effective_day_change_pct(market_row)
    price_to_ma50 = _price_to_ma50_pct(market_row)
    beta = _to_float(valuation.get("beta"))
    social_net = _to_float(social.get("netSentiment"))
    crowding = str(social.get("crowdingRisk") or "").upper()
    event_risk = str(event.get("risk_level") or event.get("riskLevel") or "").upper() if event else ""
    rating = str(valuation.get("analystRating") or "").lower()
    repricing_overlay = _ai_repricing_overlay(normalized_symbol)
    ai_repricing_score = _to_float(repricing_overlay.get("aiRepricingScore"))

    fundamental_score, fundamental_have, fundamental_need = _avg_scores([
        _score_high_good(revenue_growth, [(0.30, 92), (0.15, 78), (0.05, 62), (0.00, 48), (-999, 25)]),
        _score_high_good(earnings_growth, [(0.30, 88), (0.10, 72), (0.00, 55), (-999, 25)]),
        _score_high_good(profit_margin, [(0.25, 90), (0.15, 76), (0.05, 58), (0.00, 42), (-999, 20)]),
        _score_high_good(roe, [(0.25, 86), (0.15, 72), (0.05, 52), (0.00, 38), (-999, 20)]),
        _score_low_good(debt_to_equity, [(40, 84), (100, 68), (180, 48), (999999, 25)]),
        _score_high_good(current_ratio, [(1.5, 78), (1.0, 60), (0.7, 42), (-999, 25)]),
    ])

    valuation_score, valuation_have, valuation_need = _avg_scores([
        _score_low_good(forward_pe, [(15, 90), (25, 76), (40, 60), (60, 42), (999999, 22)]),
        _score_low_good(trailing_pe, [(20, 84), (35, 68), (60, 48), (100, 30), (999999, 15)]),
        _score_low_good(peg, [(1.0, 88), (1.5, 74), (2.5, 54), (999999, 28)]),
        _score_high_good(target_upside, [(25, 90), (12, 74), (5, 60), (0, 45), (-999, 20)]),
        _score_high_good(analyst_count, [(25, 78), (10, 65), (3, 50), (-999, 32)]),
    ])

    catalyst_base = 50.0
    if rating == "strong_buy":
        catalyst_base += 18
    elif rating == "buy":
        catalyst_base += 10
    elif rating in {"hold", "none"}:
        catalyst_base -= 5
    if manager_score:
        catalyst_base += min(12, max(-8, manager_score * 20))
    if congress_score:
        catalyst_base += min(8, max(-8, congress_score * 20))
    if social_net is not None:
        catalyst_base += min(8, max(-8, social_net * 20))
    if event:
        catalyst_base += 4
    if event_risk == "HIGH":
        catalyst_base -= 25
    catalyst_score = max(0.0, min(100.0, catalyst_base))
    catalyst_have = 2 + int(bool(rating)) + int(social_net is not None) + int(bool(manager_score or congress_score))
    catalyst_need = 5

    technical_score, technical_have, technical_need = _avg_scores([
        _score_high_good(momentum, [(20, 82), (5, 70), (0, 55), (-999, 30)]),
        _score_low_good(abs(price_to_ma50) if price_to_ma50 is not None else None, [(5, 78), (12, 68), (25, 48), (999999, 25)]),
        _score_low_good(day_change, [(-5, 35), (3, 72), (8, 55), (999999, 30)]) if day_change is not None else None,
    ])
    if technical_score is not None and price_to_ma50 is not None and price_to_ma50 < -3:
        technical_score = min(technical_score, 42)
    if technical_score is not None and price_to_ma50 is not None and price_to_ma50 > 25:
        technical_score = min(technical_score, 45)

    risk_score = 72.0
    risk_have = 1
    risk_need = 4
    if event_risk == "HIGH":
        risk_score -= 30
    elif event_risk == "MEDIUM":
        risk_score -= 12
    if crowding == "HIGH":
        risk_score -= 14
        risk_have += 1
    elif crowding in {"LOW", "MEDIUM"}:
        risk_have += 1
    if beta is not None:
        risk_have += 1
        if beta > 2:
            risk_score -= 10
        elif beta < 1.2:
            risk_score += 4
    if target_upside is not None:
        risk_have += 1
        if target_upside < 0:
            risk_score -= 18
    risk_score = max(0.0, min(100.0, risk_score))

    group_scores = {
        "fundamental": fundamental_score,
        "valuation": valuation_score,
        "aiRepricing": ai_repricing_score,
        "catalyst": catalyst_score,
        "technical": technical_score,
        "risk": risk_score,
    }
    weights = {"fundamental": 0.22, "valuation": 0.20, "aiRepricing": 0.12, "catalyst": 0.15, "technical": 0.18, "risk": 0.13}
    weighted_sum = 0.0
    used_weight = 0.0
    for key, weight in weights.items():
        score = group_scores.get(key)
        if score is not None:
            weighted_sum += score * weight
            used_weight += weight
    raw_score = weighted_sum / used_weight if used_weight else 0.0

    ai_repricing_have = 1 if ai_repricing_score is not None else 0
    ai_repricing_need = 1 if ai_repricing_score is not None else 0
    available = fundamental_have + valuation_have + ai_repricing_have + catalyst_have + technical_have + risk_have
    required = fundamental_need + valuation_need + ai_repricing_need + catalyst_need + technical_need + risk_need
    completeness = available / required * 100 if required else 0.0
    capped_score = raw_score
    if completeness < 45:
        capped_score = min(capped_score, 50)
    elif completeness < 65:
        capped_score = min(capped_score, 68)

    blockers: list[str] = []
    if event_risk == "HIGH":
        blockers.append("财报/事件高风险窗口")
    if target_upside is not None and target_upside < 0:
        blockers.append("华尔街目标价低于现价")
    if day_change is not None and day_change >= 8:
        blockers.append("单日涨幅过大，不追高")
    if price_to_ma50 is not None and price_to_ma50 > 25:
        blockers.append("价格离 MA50 过远")
    if completeness < 65:
        blockers.append("关键数据覆盖不足")

    missing: list[str] = []
    for label, value in [
        ("TTM PE", trailing_pe),
        ("Fwd PE", forward_pe),
        ("PEG", peg),
        ("收入增长", revenue_growth),
        ("盈利增长", earnings_growth),
        ("利润率", profit_margin),
        ("ROE", roe),
        ("负债率", debt_to_equity),
        ("目标价上行", target_upside),
        ("30日动量", momentum),
        ("MA50距离", price_to_ma50),
        ("社媒情绪", social_net),
    ]:
        if value is None:
            missing.append(label)

    reasons: list[str] = []
    if trailing_pe is not None and forward_pe is not None:
        reasons.append(f"TTM/Fwd PE {trailing_pe:.1f}/{forward_pe:.1f}")
    elif forward_pe is not None:
        reasons.append(f"Fwd PE {forward_pe:.1f}")
    if target_upside is not None:
        reasons.append(f"目标价上行 {target_upside:+.1f}%")
    if revenue_growth is not None:
        reasons.append(f"收入增长 {revenue_growth * 100:.1f}%")
    if profit_margin is not None:
        reasons.append(f"利润率 {profit_margin * 100:.1f}%")
    if momentum is not None:
        reasons.append(f"30日动量 {momentum:+.1f}%")
    if price_to_ma50 is not None:
        reasons.append(f"距MA50 {price_to_ma50:+.1f}%")
    if ai_repricing_score is not None:
        reasons.append(f"AI重估 {ai_repricing_score:.0f}")
    if rating:
        reasons.append(f"评级 {rating}")

    if capped_score >= 78 and not blockers:
        tier = "优先候选"
        condition = "可进入买入候选：仍需开盘后 VWAP/大盘/VIX 确认，优先小仓 starter。"
    elif capped_score >= 68:
        tier = "条件候选"
        condition = "条件候选：等价格确认或回踩，不追高；若关键缺口补齐后仍高分再升级。"
    elif capped_score >= 55:
        tier = "观察"
        condition = "观察：因子不够一致，先等估值/趋势/催化至少两项改善。"
    else:
        tier = "暂不买"
        condition = "暂不买：综合分或数据质量不足，不主动建仓。"
    if blockers:
        condition = "暂不主动买入：" + "；".join(blockers[:2]) + "。"

    if not blockers and ai_repricing_score is not None and ai_repricing_score >= 80 and capped_score >= 68:
        condition = (repricing_overlay.get("aiRepricingBuyConditionZh") or condition) + " 仍需价格/VIX/板块确认，只能生成 paper 候选。"

    quality = "PASS"
    if completeness < 45:
        quality = "FAIL"
    elif completeness < 70 or missing:
        quality = "WARN"

    return {
        "decisionScore": _round(capped_score, 1),
        "rawDecisionScore": _round(raw_score, 1),
        "decisionTierZh": tier,
        "decisionDataQuality": quality,
        "decisionDataCompletenessPct": _round(completeness, 1),
        "factorScores": {key: _round(value, 1) for key, value in group_scores.items()},
        "topDecisionReasonsZh": reasons[:3],
        "missingDecisionDataZh": missing[:8],
        "blockerFlagsZh": blockers[:5],
        "buyConditionZh": condition,
        **repricing_overlay,
    }


def _watchlist_coverage(
    market: dict[str, Any],
    earnings: dict[str, Any],
    manager_ideas: list[dict[str, Any]],
    congress: dict[str, Any],
    social: dict[str, Any],
    intel: dict[str, Any],
    orders: list[dict[str, Any]],
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    market_rows = {str(row.get("symbol")): row for row in market.get("watchSymbols", []) if row.get("symbol")}
    manager_scores = {str(row.get("symbol")): _money(row.get("score")) for row in manager_ideas}
    social_rows = _social_symbol_map(social)
    congress_scores = {str(row.get("symbol")): _money(row.get("net_score")) for row in congress.get("signals", []) if row.get("symbol")}
    event_rows = {str(row.get("symbol")): row for row in earnings.get("events", []) if row.get("symbol")}
    order_symbols = {str(row.get("symbol")) for row in orders if row.get("symbol")}
    position_symbols = {str(row.get("symbol")) for row in positions if row.get("symbol")}
    news_by_symbol = _news_items_by_symbol(social, intel)
    valuations = _valuation_map()

    symbols = sorted(
        {
            *STATIC_WATCHLIST_SYMBOLS,
            *market_rows.keys(),
            *manager_scores.keys(),
            *social_rows.keys(),
            *congress_scores.keys(),
            *event_rows.keys(),
            *order_symbols,
            *position_symbols,
        }
        - {"", *INVALID_WATCHLIST_SYMBOLS}
    )

    rows = []
    for symbol in symbols:
        market_row = market_rows.get(symbol, {})
        analysis_market_row = _lookthrough_market_row(symbol, market_row, market_rows)
        social_row = social_rows.get(symbol, {})
        event = event_rows.get(symbol, {})
        news = news_by_symbol.get(symbol, [])
        chain = _ai_chain_for_symbol(symbol)
        lookthrough = _lookthrough_summary(symbol, market_rows, valuations)
        last_price = _effective_last(market_row)
        day = _effective_day_change_pct(market_row)
        momentum = _money(market_row.get("momentum30dPct"))
        ma50 = _effective_ma50(market_row)
        price_to_ma50 = _price_to_ma50_pct(market_row, last_price)
        above_ma50 = _effective_above_ma50(market_row, last_price)
        valuation = _valuation_for_symbol(symbol, valuations, last_price)
        activist = _activist_alpha_overlay(symbol)
        manager_score = manager_scores.get(symbol, 0.0)
        congress_score = _money(congress_scores.get(symbol))
        decision = _decision_model_v1(symbol, analysis_market_row, event, social_row, valuation, manager_score, congress_score)
        event_boost = 0.18 if event else 0.0
        social_boost = min(0.18, _money(social_row.get("mentionCount")) / 20)
        trend_boost = min(0.3, max(0.0, momentum) / 100)
        position_boost = 0.1 if symbol in position_symbols else 0.0
        activist_score = _money(activist.get("activistAlphaScore"))
        activist_boost = min(0.12, activist_score / 60 * 0.12) if activist_score else 0.0
        attention_score = min(1.0, manager_score * 0.32 + trend_boost + event_boost + social_boost + position_boost + activist_boost)
        rows.append(
            {
                "symbol": symbol,
                "group": _watchlist_group(symbol),
                "aiChainPrimaryId": chain["primaryId"],
                "aiChainPrimaryNameZh": chain["primaryNameZh"],
                "aiChainRoleZh": chain["roleZh"],
                "aiChainLayers": chain["layers"],
                "lookThrough": lookthrough,
                "lookThroughTypeZh": lookthrough.get("typeZh") if lookthrough else None,
                "lookThroughPrimarySymbol": lookthrough.get("primarySymbol") if lookthrough else None,
                "lookThroughSymbols": lookthrough.get("underlyingSymbols") if lookthrough else [],
                "lookThroughSummaryZh": lookthrough.get("summaryZh") if lookthrough else None,
                "lookThroughItems": lookthrough.get("items") if lookthrough else [],
                "last": _round(last_price),
                "dayChangePct": _round(day),
                "momentum30dPct": _round(momentum),
                "ma50": _round(ma50),
                "priceToMa50Pct": _round(price_to_ma50),
                "aboveMa50": above_ma50,
                "asOf": market_row.get("asOf"),
                "source": market_row.get("source"),
                "dataQuality": market_row.get("dataQuality", "MISSING"),
                "managerScore": _round(manager_score, 3),
                "socialLabelZh": social_row.get("sentimentLabelZh"),
                "socialNet": _round(social_row.get("netSentiment"), 3),
                "crowdingRisk": social_row.get("crowdingRisk"),
                "eventRisk": event.get("risk_level") or event.get("riskLevel"),
                "eventDate": event.get("earnings_date") or event.get("date"),
                "congressScore": _round(congress_score, 3),
                "positionHeld": symbol in position_symbols,
                "hasOpenDecision": symbol in order_symbols,
                "news": news,
                "latestNewsTitle": news[0].get("title") if news else None,
                "latestNewsUrl": news[0].get("url") if news else None,
                "latestNewsAt": news[0].get("publishedAt") if news else None,
                **valuation,
                **activist,
                "ruleBuyConditionZh": _buy_condition_zh(symbol, market_row, event, social_row, valuation),
                **decision,
                "stanceZh": _watchlist_stance(symbol, market_row, event, social_row),
                "attentionScore": _round(attention_score, 3),
            }
        )

    for row in rows:
        row.update(_alpha_workflow_for_row(row))

    rows.sort(
        key=lambda row: (
            row.get("eventRisk") == "HIGH",
            row.get("positionHeld"),
            row.get("hasOpenDecision"),
            row.get("decisionScore") or 0,
            row.get("attentionScore") or 0,
            abs(_money(row.get("dayChangePct"))),
        ),
        reverse=True,
    )
    quality_counts = Counter(str(row.get("decisionDataQuality") or "MISSING") for row in rows)
    tier_counts = Counter(str(row.get("decisionTierZh") or "未评分") for row in rows)
    missing_counts = Counter(
        item
        for row in rows
        for item in (row.get("missingDecisionDataZh") or [])
    )
    return {
        "timestamp": market.get("timestamp"),
        "source": "market_snapshot.watchSymbols + social_sentiment_feed + fund/congress/event overlays",
        "symbolsCovered": len(rows),
        "rows": rows,
        "priorityRows": rows[:18],
        "decisionModelV1": {
            "weights": {
                "fundamental": 22,
                "valuation": 20,
                "aiRepricing": 12,
                "catalyst": 15,
                "technical": 18,
                "risk": 13,
            },
            "qualityCounts": dict(quality_counts),
            "tierCounts": dict(tier_counts),
            "commonMissingData": [{"field": key, "count": value} for key, value in missing_counts.most_common(8)],
            "averageCompletenessPct": _round(
                sum(_money(row.get("decisionDataCompletenessPct")) for row in rows) / len(rows)
                if rows
                else None,
                1,
            ),
        },
        "groups": sorted({row["group"] for row in rows}),
        "marketBreadth": _breadth_summary(rows),
        "groupStats": _watchlist_group_summary(rows),
        "alphaLensWorkflow": _alpha_lens_workflow_summary(rows),
        "aiChainLayers": _ai_chain_layer_summary(rows),
        "activistAlphaMethodology": {
            "sourceUrl": ACTIVIST_ALPHA_SOURCE_URL,
            "methodZh": "新增 13D/激进投资 overlay：用 Value Discount、Catalyst Feasibility、Thesis Strength、Management Receptivity、Legal Feasibility、Risk Assessment 判断公司是否可能被治理/资本配置催化；只作为事件驱动补盲，不单独触发买入。",
            "coreSymbols": sorted([symbol for symbol, item in ACTIVIST_ALPHA_OVERLAYS.items() if item.get("coreRelevance") in {"HIGH", "MEDIUM"}]),
        },
        "aiChainNotesZh": "按图片里的完整数据中心解剖：10 层基础设施 + 承载/云平台 + AI 应用层 + 高风险矿工转 AI。一个标的可落入多层，筛选时按所有相关层匹配。",
        "valuationNotesZh": "PE 和华尔街平均目标价读取 data/market/valuation_latest.json 或 valuation_overrides.json；缺数据时不自动编造。",
        "notesZh": "行情来自本地 OpenBB-first 快照；新闻/帖子来自当前 social/intel feed；13D/激进投资来自 Activist Alpha 方法论 overlay。财报、目标价、13D 和重大新闻仍需要人工核验官方来源。",
    }


EVENT_CATEGORY_ZH = {
    "macro_policy": "宏观 / 政策 / 监管",
    "corporate_actions": "公司行动 / 并购 / 重组",
    "single_name": "个股财报 / 评级 / 经营变化",
    "market_movers": "市场异动 / 资金迁移",
    "political_flow": "议员 / 政治资金线索",
    "social_crowding": "社媒叙事 / 拥挤风险",
}


def _event_symbols(row: dict[str, Any]) -> list[str]:
    values = row.get("symbols") or row.get("symbol_hits") or row.get("candidate_symbol_hits") or []
    return sorted({str(value).upper() for value in values if value})


def _event_types(row: dict[str, Any]) -> list[str]:
    values = row.get("eventTypes") or row.get("event_hits") or []
    return sorted({str(value) for value in values if value})


def _event_category(row: dict[str, Any]) -> str:
    title = str(row.get("title") or "").lower()
    source = str(row.get("sourceLabel") or row.get("source_label") or row.get("sourceId") or "").lower()
    types = set(_event_types(row))
    text = f"{title} {source}"

    if "corporate_action" in types or any(word in text for word in ("acquir", "acquisition", "buyout", "bid", "merger", "deal", "takeover", "proposal", "offer for", "spin off", "spinoff")):
        return "corporate_actions"
    if any(word in text for word in ("fed", "fomc", "treasury", "yield", "cpi", "inflation", "tariff", "regulation", "regulatory", "stablecoin", "clarity act", "sec", "lummis")):
        return "macro_policy"
    if any(word in text for word in ("congress", "pelosi", "senator", "representative")):
        return "political_flow"
    if "analyst_action" in types or any(word in text for word in ("earnings", "downgrade", "upgrade", "price target", "guidance", "valuation", "beat", "miss")):
        return "single_name"
    if any(event in types for event in ("market_mover_up", "market_mover_down")) or any(word in text for word in ("jumps", "surges", "falls", "dives", "rally", "selloff", "plunges")):
        return "market_movers"
    return "social_crowding"


def _event_category_label(category: str) -> str:
    return EVENT_CATEGORY_ZH.get(category, category)


def _event_key(row: dict[str, Any], category: str, symbols: list[str]) -> str:
    if symbols:
        return f"{category}:{','.join(symbols[:4])}"
    title = str(row.get("title") or "").lower()
    clean = "".join(ch if ch.isalnum() else " " for ch in title)
    return f"{category}:{' '.join(clean.split()[:8])}"


def _event_source_link(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": row.get("title"),
        "url": row.get("url"),
        "sourceLabel": row.get("sourceLabel") or row.get("source_label") or row.get("sourceId") or row.get("source_id"),
        "publishedAt": row.get("publishedAt") or row.get("published_at"),
    }


def _event_summary_takeaway(category: str, symbols: list[str], title: str, rows: list[dict[str, Any]]) -> tuple[str, str, str]:
    symbol_set = set(symbols)
    lowered = title.lower()

    if {"GME", "EBAY"}.issubset(symbol_set):
        return (
            "GME 对 EBAY 的大型收购提案已经从普通新闻变成事件交易：EBAY 受益于潜在收购溢价，GME 承压来自融资、稀释和执行可行性。",
            "不把 GME 当作核心建仓；EBAY 也不追高。更合适的处理是事件观察：等董事会回应、融资细节和价差稳定后再评估小仓位 paper trade。",
            "观察 / 不追高",
        )
    if "CRCL" in symbol_set or "stablecoin" in lowered or "clarity act" in lowered:
        return (
            "稳定币监管/收益安排出现进展，带动 CRCL、COIN、HOOD 等 crypto/payment 相关股票异动。",
            "这是政策催化，不是基本面已经完全兑现。CRCL 单日波动很大，优先加入高优先级观察；若回踩不破关键均线且新闻继续确认，再考虑小仓位 paper starter。",
            "观察 / 等回踩",
        )
    if "AMD" in symbol_set or "earnings" in lowered:
        return (
            "AMD/相关半导体处在财报和预期再定价窗口，盘前盘后跳空风险高。",
            "不新开 naked call/put；股票仓位等财报后方向、成交量和第二天反应确认。若结果强且守住 VWAP，再考虑小仓位而不是期权追涨。",
            "等待财报确认",
        )
    if "PLTR" in symbol_set or "palantir" in lowered:
        return (
            "PLTR 的争议集中在估值和财报波动，评级/目标价下修会压制短线风险偏好。",
            "暂不主动建仓；如果财报后仍守住 MA50 且负面新闻被吸收，再重新评估。期权只考虑 defined-risk，不做裸多。",
            "暂缓 / 等确认",
        )

    if category == "macro_policy":
        return (
            "政策、监管或利率相关信息会影响市场风险偏好和板块估值，尤其会影响高估值科技股和 crypto/payment。",
            "把它作为仓位大小和追涨限制的上层过滤器：宏观偏鹰时，优先 ETF/高质量龙头，小仓分批，降低期权风险。",
            "影响仓位上限",
        )
    if category == "corporate_actions":
        return (
            "公司行动、并购或融资信息会带来一次性跳空和价差交易机会，但也容易出现假突破。",
            "先核验公告和融资结构；只把它作为事件交易候选，不直接提升核心组合仓位。",
            "事件观察",
        )
    if category == "single_name":
        return (
            "个股财报、评级和经营信息会改变短线估值锚，是今天需要重点消化的个股层信息。",
            "先看价格是否确认新闻方向；若新闻与价格背离，优先降低动作强度。",
            "个股复核",
        )
    if category == "market_movers":
        return (
            "出现显著大涨/大跌或成交异动，说明资金正在重新定价某个主题。",
            "不要只因为涨跌幅建仓；先判断是基本面、政策、财报还是短线挤压。",
            "识别资金方向",
        )
    return (
        "社媒和新闻叙事出现集中讨论，可能改变短线拥挤度和情绪。",
        "只作为置信度 overlay；不能单独触发买卖，必须和价格、基本面和风控一起确认。",
        "情绪 overlay",
    )


def _event_insight_sections(social: dict[str, Any], intel: dict[str, Any]) -> list[dict[str, Any]]:
    raw_rows = list(social.get("eventRadar", []) or [])
    for row in intel.get("highlights", []) or []:
        raw_rows.append(row)

    grouped: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        symbols = _event_symbols(row)
        category = _event_category(row)
        key = _event_key(row, category, symbols)
        event_types = _event_types(row)
        target = grouped.setdefault(
            key,
            {
                "category": category,
                "categoryZh": _event_category_label(category),
                "symbols": symbols,
                "eventTypes": event_types,
                "titles": [],
                "sources": [],
                "_sourceUrls": set(),
                "score": 0.0,
                "latestAt": None,
            },
        )
        target["symbols"] = sorted(set(target.get("symbols", [])) | set(symbols))
        target["eventTypes"] = sorted(set(target.get("eventTypes", [])) | set(event_types))
        if row.get("title"):
            target["titles"].append(row.get("title"))
        source_link = _event_source_link(row)
        source_key = str(source_link.get("url") or source_link.get("title") or "")
        if source_key and source_key not in target["_sourceUrls"]:
            target["_sourceUrls"].add(source_key)
            target["sources"].append(source_link)
        target["score"] = max(_money(target.get("score")), _money(row.get("score")))
        published = row.get("publishedAt") or row.get("published_at")
        if published and (not target.get("latestAt") or str(published) > str(target.get("latestAt"))):
            target["latestAt"] = published

    sections: dict[str, list[dict[str, Any]]] = {key: [] for key in EVENT_CATEGORY_ZH}
    for item in grouped.values():
        title = item.get("titles", ["未命名事件"])[0]
        summary, takeaway, action_bias = _event_summary_takeaway(
            item["category"],
            item.get("symbols", []),
            str(title),
            item.get("sources", []),
        )
        item.update(
            {
                "title": title,
                "summaryZh": summary,
                "takeawayZh": takeaway,
                "actionBiasZh": action_bias,
                "sourceCount": len(item.get("sources", [])),
                "sources": item.get("sources", [])[:4],
            }
        )
        item.pop("titles", None)
        item.pop("_sourceUrls", None)
        sections.setdefault(item["category"], []).append(item)

    result = []
    for category, label in EVENT_CATEGORY_ZH.items():
        items = sorted(
            sections.get(category, []),
            key=lambda row: (row.get("score") or 0, row.get("latestAt") or ""),
            reverse=True,
        )[:4]
        if not items:
            continue
        result.append(
            {
                "category": category,
                "titleZh": label,
                "summaryZh": _section_summary_zh(category, items),
                "takeawayZh": _section_takeaway_zh(category, items),
                "items": items,
            }
        )
    return result


def _event_radar_with_implications(social: dict[str, Any], sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    insight_by_url: dict[str, dict[str, Any]] = {}
    insight_by_title: dict[str, dict[str, Any]] = {}
    for section in sections:
        for item in section.get("items", []) or []:
            title_key = str(item.get("title") or "").strip().lower()
            if title_key:
                insight_by_title[title_key] = item
            for source in item.get("sources", []) or []:
                url_key = str(source.get("url") or "").strip()
                if url_key:
                    insight_by_url[url_key] = item

    enriched = []
    for row in list(social.get("eventRadar", []) or [])[:12]:
        event = dict(row)
        match = insight_by_url.get(str(event.get("url") or "").strip())
        if not match:
            match = insight_by_title.get(str(event.get("title") or "").strip().lower())
        symbols = _event_symbols(event)
        event["candidateTickers"] = symbols
        event["rawLinks"] = [
            {
                "title": event.get("title"),
                "url": event.get("url"),
                "sourceLabel": event.get("sourceLabel") or event.get("source_label"),
                "publishedAt": event.get("publishedAt") or event.get("published_at"),
            }
        ]
        if match:
            event["summaryZh"] = match.get("summaryZh")
            event["implicationZh"] = match.get("takeawayZh")
            event["actionBiasZh"] = match.get("actionBiasZh")
            event["eventCategoryZh"] = match.get("categoryZh")
            event["eventTypes"] = match.get("eventTypes") or event.get("eventTypes")
        else:
            event["summaryZh"] = "Event radar item requires manual source review before it can affect any paper playbook."
            event["implicationZh"] = "Use as a research alert only; do not trade from this item without price, macro, sizing, and guard confirmation."
            event["actionBiasZh"] = "research alert"
        enriched.append(event)
    return enriched


def _section_summary_zh(category: str, items: list[dict[str, Any]]) -> str:
    symbols = sorted({symbol for item in items for symbol in item.get("symbols", [])})
    symbol_text = "、".join(symbols[:6]) if symbols else "相关标的"
    if category == "macro_policy":
        return f"政策/监管线索集中影响 {symbol_text}，会改变风险偏好和板块估值。"
    if category == "corporate_actions":
        return f"公司行动和并购类新闻集中在 {symbol_text}，更偏事件交易而不是稳态基本面。"
    if category == "single_name":
        return f"个股层面的财报、评级或经营信息影响 {symbol_text}，需要逐个确认价格反应。"
    if category == "market_movers":
        return f"{symbol_text} 出现资金重定价或大幅异动，需要区分趋势突破和短线挤压。"
    if category == "political_flow":
        return f"政治/议员组合线索覆盖 {symbol_text}，只能作为滞后灵感，不作为直接交易触发。"
    return f"社媒叙事集中讨论 {symbol_text}，用于判断拥挤度和分歧。"


def _section_takeaway_zh(category: str, items: list[dict[str, Any]]) -> str:
    if category == "macro_policy":
        return "仓位上限要跟宏观走：偏鹰/监管不确定时，小仓分批，少用期权。"
    if category == "corporate_actions":
        return "并购/融资新闻先核验交易结构；不追第一根大阳线。"
    if category == "single_name":
        return "先看新闻是否被价格确认；财报窗口不做裸 call/put。"
    if category == "market_movers":
        return "涨跌幅只是警报，不是买卖理由；要找到背后的催化。"
    if category == "political_flow":
        return "议员数据有披露滞后，只能提高研究优先级。"
    return "社媒只占决策置信度的一小部分，避免被热门叙事带节奏。"


def _watch_row(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    upper = symbol.upper()
    return next((row for row in rows if str(row.get("symbol", "")).upper() == upper), {})


def _action_reasons(row: dict[str, Any], extra: list[dict[str, str]] | None = None, include_news: bool = True) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    if row:
        momentum = row.get("momentum30dPct")
        if momentum is not None:
            reasons.append(
                {
                    "title": "趋势确认",
                    "detail": f"30日动量 {float(momentum):.2f}%，{'仍在 MA50 上方' if row.get('aboveMa50') else '未站稳 MA50'}。",
                }
            )
        if row.get("managerScore"):
            reasons.append(
                {
                    "title": "机构共识",
                    "detail": f"基金经理/持仓共识分数 {float(row.get('managerScore') or 0):.2f}，可作为 idea overlay。",
                }
            )
        if include_news and row.get("latestNewsTitle"):
            reasons.append(
                {
                    "title": "新闻催化",
                    "detail": str(row.get("latestNewsTitle"))[:140],
                }
            )
    if extra:
        reasons.extend(extra)
    return reasons[:3]


STOCK_PITCH_EXCLUDE = {"QQQ", "SPY", "SMH", "SOXX", "DRAM", "SNXX", "MULL", "EWY"}

DISCOVERY_PITCH_EXCLUDE = STOCK_PITCH_EXCLUDE | {
    "AAPL",
    "AMZN",
    "AMD",
    "AVGO",
    "GOOG",
    "GOOGL",
    "META",
    "MSFT",
    "MU",
    "NVDA",
    "ORCL",
    "SNDK",
    "TSM",
}

DISCOVERY_CHAIN_BONUS = {
    "network_optical": 10.0,
    "operators_cloud": 9.0,
    "land_construction": 8.0,
    "memory_storage": 7.0,
    "generation_power": 5.0,
    "power_distribution": 5.0,
}


ALPHA_PILLARS = [
    ("cognitive", "P0 认知/组织", "创始人认知、组织机制、长期主义和执行文化"),
    ("endogenous", "P1 内生质量", "财务质量、护城河、增长和估值"),
    ("exogenous", "P2 外生叙事", "宏观、产业链、新闻催化和资金风格"),
    ("smartMoney", "P3 聪明钱", "基金经理、议员交易、社媒拥挤度和情绪确认"),
]


PRISM_PILLAR_WEIGHTS = {
    "cognitive": 0.25,
    "endogenous": 0.30,
    "exogenous": 0.25,
    "smartMoney": 0.20,
}

PRISM_FUNNEL_LEVELS = [
    {"level": "L1", "nameZh": "全市场宇宙", "logicZh": "先不做判断，A/H/KR/US/一级市场尽量全覆盖，避免过早漏掉黑马。", "targetZh": "3000-5000", "dataSourcesZh": "OpenBB/Yahoo/Finnhub/SEC/新闻/社媒/一级融资", "orgLinkZh": "只保证不漏"},
    {"level": "L2", "nameZh": "创始人初筛", "logicZh": "创始人在任、持股/激励强，或近年收入 CAGR 明显；剔除纯守成型管理层。", "targetZh": "200-300", "dataSourcesZh": "Proxy/Insider/公司官网/公开访谈", "orgLinkZh": "找到掌舵人"},
    {"level": "L3", "nameZh": "P0 认知引擎", "logicZh": "战略清晰、执行力、认知迭代、团队搭建、行业洞察；用认知领袖库做外部校准。", "targetZh": "50-80", "dataSourcesZh": "访谈/播客/财报会 Transcript/CEO 公开信", "orgLinkZh": "创始人认知质量"},
    {"level": "L4", "nameZh": "P1 内生引擎", "logicZh": "ROIC/毛利率/FCF/收益质量/估值；财务是最硬的反证。", "targetZh": "15-25", "dataSourcesZh": "OpenBB fundamentals/valuation/analyst consensus", "orgLinkZh": "组织盈利能力"},
    {"level": "L4.5", "nameZh": "量化验证", "logicZh": "Alpha、Sharpe、Beta、R2、波动率，用风险调整收益验证组织能力是否可量化。", "targetZh": "10-18", "dataSourcesZh": "价格历史/风险指标/回测", "orgLinkZh": "组织能力量化镜像"},
    {"level": "L5", "nameZh": "P2 叙事对齐", "logicZh": "TAM 扩容、技术催化、政策/宏观顺风、格局向龙头集中。", "targetZh": "5-8", "dataSourcesZh": "新闻/卖方/宏观/社媒/产业链数据", "orgLinkZh": "环境是否支持组织"},
    {"level": "L6", "nameZh": "P3 聪明钱", "logicZh": "13F、基金经理、议员、战略投资、一级融资；只做验证，不做唯一买入理由。", "targetZh": "2-4", "dataSourcesZh": "SEC 13F/基金持仓/议员交易/融资新闻", "orgLinkZh": "顶级机构验证"},
]

PRISM_THOUGHT_LEADERS = [
    {"name": "Jensen Huang", "roleZh": "NVIDIA CEO", "categoryZh": "芯片/算力", "worldviewZh": "5层蛋糕、Scaling Laws、Token 经济；AI 基础设施会把每 1 美元 GPU 拉出多倍下游活动。", "signalZh": "验证 NVDA/MRVL/AVGO/ALAB/硅光互联/定制 ASIC 等 AI infra 链条。", "symbols": ["NVDA", "MRVL", "AVGO", "ALAB"], "chainIds": ["compute", "network_optical", "packaging_pcb"]},
    {"name": "Dario Amodei", "roleZh": "Anthropic CEO", "categoryZh": "AI 模型", "worldviewZh": "Safety + Scale 双螺旋；Post-training 和企业 Coding 是下一战场。", "signalZh": "验证 AMZN/GOOG/MSFT 的模型与云投入，以及企业 AI 工具链。", "symbols": ["AMZN", "GOOG", "GOOGL", "MSFT"], "chainIds": ["operators_cloud", "ai_applications"]},
    {"name": "Sam Altman", "roleZh": "OpenAI CEO", "categoryZh": "AI 模型/算力", "worldviewZh": "Compute is new oil；大规模算力、Agent 工具链和推理能力是新基础设施。", "signalZh": "验证 MSFT/ORCL/NBIS/CRWV 等算力云和 Stargate 相关基础设施。", "symbols": ["MSFT", "ORCL", "NBIS", "CRWV"], "chainIds": ["operators_cloud", "energy_infra"]},
    {"name": "Ali Ghodsi", "roleZh": "Databricks CEO", "categoryZh": "数据/软件", "worldviewZh": "Lakehouse 统一数据与 AI；Mosaic AI/Post-training 可能成为企业 AI 默认栈。", "signalZh": "验证 Databricks/SNOW/AI 数据治理与企业 Agent 软件链。", "symbols": ["SNOW", "MSFT"], "chainIds": ["ai_applications", "operators_cloud"]},
    {"name": "Alex Karp", "roleZh": "Palantir CEO", "categoryZh": "数据/软件", "worldviewZh": "Ontology + Warfare-grade AI；企业 AI 要有本体论和高上下文密度才能推理。", "signalZh": "验证 PLTR 的 AIP/Foundry、国防与制造业 AI 重构。", "symbols": ["PLTR"], "chainIds": ["ai_applications"]},
    {"name": "Yann LeCun", "roleZh": "AMI Labs / ex-Meta FAIR", "categoryZh": "学术/World Model", "worldviewZh": "JEPA/World Model；智能不是只预测 token，而是理解世界。", "signalZh": "验证 World Model、Physical AI、机器人和空间智能赛道。", "symbols": ["META", "TSLA"], "chainIds": ["ai_applications"]},
    {"name": "Fei-Fei Li", "roleZh": "Stanford HAI / World Labs", "categoryZh": "空间智能", "worldviewZh": "Spatial Intelligence 是下一个 ImageNet 时刻，物理 AI 需要空间先验。", "signalZh": "验证机器人、自动驾驶、3D 世界模型和边缘 AI。", "symbols": ["TSLA", "NVDA"], "chainIds": ["compute", "ai_applications"]},
    {"name": "Demis Hassabis", "roleZh": "Google DeepMind CEO", "categoryZh": "AI for Science", "worldviewZh": "AlphaFold 之后，材料、药物、能源的 AI 科学发现是长期赛道。", "signalZh": "验证 GOOG/GOOGL 的 DeepMind、Gemini 和 AI for Science 资产。", "symbols": ["GOOG", "GOOGL"], "chainIds": ["ai_applications", "operators_cloud"]},
    {"name": "Hock Tan", "roleZh": "Broadcom CEO", "categoryZh": "ASIC/网络", "worldviewZh": "定制 ASIC 可能在规模推理中替代部分通用 GPU，AI 基础设施 TAM 很大。", "signalZh": "验证 AVGO 的 XPU/ASIC/网络与 hyperscaler 合作。", "symbols": ["AVGO"], "chainIds": ["compute", "network_optical"]},
    {"name": "Lisa Su", "roleZh": "AMD CEO", "categoryZh": "GPU/开放生态", "worldviewZh": "开放平台、ROCm、MI 系列和客户议价空间，是 NVDA 之外的第二曲线。", "signalZh": "验证 AMD 的 MI 系列 ramp、HBM/CoWoS 供给和开放生态。", "symbols": ["AMD"], "chainIds": ["compute", "packaging_pcb"]},
    {"name": "Sanjay Mehrotra", "roleZh": "Micron CEO", "categoryZh": "HBM/存储", "worldviewZh": "AI 把 DRAM/HBM/NAND 重新定价，存储可能是算力之后的瓶颈。", "signalZh": "验证 MU/SNDK/DRAM/EWY/Hanmi/SK Hynix 等存储链。", "symbols": ["MU", "SNDK", "DRAM", "MULL", "EWY", "000660.KS"], "chainIds": ["memory_storage", "packaging_pcb"]},
    {"name": "Arkady Volozh", "roleZh": "Nebius CEO", "categoryZh": "GPU 云/基础设施", "worldviewZh": "垂直整合 GPU 云，用自有 DC 和运营能力降低 hyperscaler 加价。", "signalZh": "验证 NBIS 及 AI neocloud 的订单、供电和扩容能力。", "symbols": ["NBIS"], "chainIds": ["operators_cloud", "land_construction"]},
    {"name": "Michael Intrator", "roleZh": "CoreWeave CEO", "categoryZh": "AI-first Cloud", "worldviewZh": "为 AI workload 从头设计云，backlog 和客户结构决定成长能见度。", "signalZh": "验证 CRWV/CORZ/APLD/IREN 等 GPU 云和矿转 AI。", "symbols": ["CRWV", "CORZ", "APLD", "IREN"], "chainIds": ["operators_cloud", "transition_miners"]},
    {"name": "Charles Liang", "roleZh": "Supermicro CEO", "categoryZh": "服务器/液冷", "worldviewZh": "Rack-scale 液冷、快速交付和供应链整合是 AI 服务器壁垒。", "signalZh": "验证 SMCI/DELL/VRT/ETN/NVT 等机柜、液冷和服务器链。", "symbols": ["SMCI", "DELL", "VRT", "ETN", "NVT"], "chainIds": ["cooling", "facility_systems"]},
    {"name": "Michael Dell", "roleZh": "Dell Technologies CEO", "categoryZh": "AI 服务器/内存", "worldviewZh": "企业 AI 服务器和内存需求会把传统 IT 栈重新拉升。", "signalZh": "验证 DELL、HBM/DRAM、AI server 订单与企业更新周期。", "symbols": ["DELL", "MU", "DRAM"], "chainIds": ["memory_storage", "cooling"]},
    {"name": "Mark Zuckerberg", "roleZh": "Meta CEO", "categoryZh": "开源 AI/Capex", "worldviewZh": "开源模型、AI 代理、AR 和大规模 capex 是 Meta 下一轮平台战。", "signalZh": "验证 META 的 AI capex、Llama、Nebius/CoreWeave 合同与电力需求。", "symbols": ["META", "NBIS", "CRWV"], "chainIds": ["operators_cloud", "energy_infra"]},
    {"name": "Elon Musk", "roleZh": "Tesla/xAI/SpaceX", "categoryZh": "Physical AI", "worldviewZh": "真实世界数据、机器人、自动驾驶和自研训练集群形成物理 AI 飞轮。", "signalZh": "验证 TSLA、xAI、机器人供应链和边缘推理。", "symbols": ["TSLA", "NVDA"], "chainIds": ["compute", "ai_applications"]},
    {"name": "Matt Murphy", "roleZh": "Marvell CEO", "categoryZh": "定制硅/互联", "worldviewZh": "定制硅、互联、硅光和 CXL 共同组成多元化 AI 基础设施。", "signalZh": "验证 MRVL/ALAB/AVGO/CIEN/COHR 等互联链。", "symbols": ["MRVL", "ALAB", "AVGO", "CIEN", "COHR"], "chainIds": ["network_optical", "compute"]},
    {"name": "C.C. Wei", "roleZh": "TSMC CEO", "categoryZh": "先进封装/CoWoS", "worldviewZh": "AI 算力瓶颈是先进封装、CoWoS 和硅光平台产能。", "signalZh": "验证 TSM/ASML/LRCX/AMAT/KLAC/ALAB/封装 PCB 链。", "symbols": ["TSM", "ASML", "LRCX", "AMAT", "KLAC", "ALAB"], "chainIds": ["packaging_pcb", "network_optical"]},
    {"name": "Stanley Druckenmiller", "roleZh": "Duquesne / 宏观投资人", "categoryZh": "宏观/Top Asset", "worldviewZh": "AI 可能是铁路、电力、互联网级别的 supercycle，核心是选对头马和阶段。", "signalZh": "验证 AI infra 叙事，但提醒估值和周期位置。", "symbols": ["NVDA", "MSFT", "AMZN", "GOOG", "GOOGL"], "chainIds": ["compute", "operators_cloud"]},
    {"name": "Cathie Wood", "roleZh": "ARK Invest", "categoryZh": "破坏式创新", "worldviewZh": "AI/Robotics/Energy Storage/Blockchain/Multi-omics 由指数级成本下降驱动。", "signalZh": "验证 TSLA/PLTR/ROKU/HOOD/COIN 等高 beta 创新资产，但需估值和风控过滤。", "symbols": ["TSLA", "PLTR", "ROKU", "HOOD", "COIN"], "chainIds": ["ai_applications", "generation_power"]},
]

RESEARCH_HEATMAP_DIMS = [
    ("financials", "财务质量", "收入增速、利润率、ROE、负债和现金流"),
    ("valuation", "估值/预期差", "TTM PE、Forward PE、PEG、目标价上行空间"),
    ("technical", "技术面", "30日动量、MA50距离、日内涨跌和VWAP条件"),
    ("catalyst", "新闻/事件", "财报、评级、并购、产品发布和监管催化"),
    ("sentiment", "舆情/聪明钱", "社媒、KOL、基金经理、议员组合和资金拥挤度"),
    ("industry", "产业链位置", "AI产业链层级、需求传导和可替代性"),
    ("risk", "风险/反证", "事件风险、估值过热、拥挤度和数据质量"),
    ("organization", "组织质量", "人才密度、决策效率、Builder文化和使命驱动"),
]


ORG_PATTERN_LIBRARY = {
    "NVDA": {
        "grade": "S",
        "confidence": "HIGH",
        "scores": {
            "talent_density": 9.2,
            "org_flatness": 9.3,
            "transparency": 8.8,
            "deep_thinking": 9.1,
            "builder_led": 9.5,
            "mission_driven": 8.7,
        },
        "summaryZh": "Jensen长期主义、CUDA生态、技术领导层和Builder文化同时成立，适合作为长期核心组织型资产观察。",
    },
    "AMZN": {
        "grade": "A",
        "confidence": "HIGH",
        "scores": {
            "talent_density": 8.1,
            "org_flatness": 8.4,
            "transparency": 8.0,
            "deep_thinking": 9.2,
            "builder_led": 8.5,
            "mission_driven": 9.0,
        },
        "summaryZh": "Working Backwards、六页备忘录、AWS和长期投入文化仍是核心组织优势；需要监控AI capex对现金流的压力。",
    },
    "PLTR": {
        "grade": "A-",
        "confidence": "MEDIUM",
        "scores": {
            "talent_density": 8.0,
            "org_flatness": 7.8,
            "transparency": 7.8,
            "deep_thinking": 8.5,
            "builder_led": 8.7,
            "mission_driven": 8.8,
        },
        "summaryZh": "组织和使命感强，FDE模式带来高上下文密度；核心风险是估值和政府/商业增长节奏的预期差。",
    },
    "ALAB": {
        "grade": "B+",
        "confidence": "LOW",
        "scores": {
            "talent_density": 7.2,
            "org_flatness": 7.5,
            "transparency": 6.5,
            "deep_thinking": 7.3,
            "builder_led": 8.0,
            "mission_driven": 7.4,
        },
        "summaryZh": "AI连接层位置优秀、技术导向明显，但上市历史短，组织质量还需要用访谈、客户集中度和管理层执行记录补证。",
    },
    "MU": {
        "grade": "B",
        "confidence": "MEDIUM",
        "scores": {
            "talent_density": 7.0,
            "org_flatness": 6.4,
            "transparency": 6.6,
            "deep_thinking": 7.0,
            "builder_led": 7.2,
            "mission_driven": 6.8,
        },
        "summaryZh": "更偏周期型制造和资本开支执行，不应按纯组织compounder定价；重点看存储周期、HBM份额和资本纪律。",
    },
    "GOOG": {
        "grade": "A-",
        "confidence": "HIGH",
        "scores": {
            "talent_density": 8.8,
            "org_flatness": 7.0,
            "transparency": 7.5,
            "deep_thinking": 8.6,
            "builder_led": 8.4,
            "mission_driven": 7.8,
        },
        "summaryZh": "人才和技术密度极高，AI/Cloud/搜索资产强；主要折扣来自组织速度、监管和搜索商业模式转型。",
    },
    "GOOGL": {
        "grade": "A-",
        "confidence": "HIGH",
        "scores": {
            "talent_density": 8.8,
            "org_flatness": 7.0,
            "transparency": 7.5,
            "deep_thinking": 8.6,
            "builder_led": 8.4,
            "mission_driven": 7.8,
        },
        "summaryZh": "人才和技术密度极高，AI/Cloud/搜索资产强；主要折扣来自组织速度、监管和搜索商业模式转型。",
    },
}


def _avg_alpha(values: list[Any]) -> float | None:
    nums = [_to_float(value) for value in values]
    nums = [value for value in nums if value is not None and math.isfinite(value)]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _alpha_score_label(score: float | None) -> str:
    if score is None:
        return "缺数据"
    if score >= 80:
        return "强共振"
    if score >= 68:
        return "可推进"
    if score >= 55:
        return "待补证"
    return "低优先级"


def _heat_level(score: float | None) -> int:
    if score is None:
        return 1
    if score >= 80:
        return 5
    if score >= 65:
        return 4
    if score >= 45:
        return 3
    if score >= 25:
        return 2
    return 1


def _heat_label(level: int) -> str:
    return {
        5: "深度掌握",
        4: "覆盖良好",
        3: "表面了解",
        2: "薄弱",
        1: "盲区",
    }.get(level, "盲区")


def _org_pattern(symbol: str) -> dict[str, Any]:
    raw = ORG_PATTERN_LIBRARY.get(str(symbol or "").upper())
    if not raw:
        return {
            "grade": "待研究",
            "confidence": "MISSING",
            "score": None,
            "scores": {},
            "summaryZh": "组织Pattern尚未覆盖；如果标的进入长期持仓候选，需要补创始人/管理层访谈、RPE、研发文化和治理结构。",
        }
    scores = raw.get("scores") or {}
    avg = _avg_alpha(list(scores.values()))
    return {
        **raw,
        "score": _round(avg * 10 if avg is not None else None, 1),
        "dimensions": [
            {"id": key, "score": value, "scorePct": _round(value * 10, 1)}
            for key, value in scores.items()
        ],
    }


def _research_heatmap(row: dict[str, Any]) -> list[dict[str, Any]]:
    factor_scores = row.get("factorScores") or {}
    has_news = bool(row.get("latestNewsTitle") or row.get("eventDate"))
    social_score = None
    if row.get("socialNet") is not None or row.get("managerScore") or row.get("congressScore"):
        social_score = 50 + min(20, max(-20, _money(row.get("socialNet")) * 18)) + min(20, _money(row.get("managerScore")) * 25) + min(10, _money(row.get("congressScore")) * 20)
    org = _org_pattern(str(row.get("symbol") or ""))
    risk_base = factor_scores.get("risk")
    missing = row.get("missingDecisionDataZh") or []
    blocker_count = len(row.get("blockerFlagsZh") or [])
    if risk_base is not None:
        risk_score = max(0.0, min(100.0, float(risk_base) - blocker_count * 8))
    else:
        risk_score = max(15.0, 72.0 - blocker_count * 12 - len(missing) * 2)

    scores = {
        "financials": factor_scores.get("fundamental"),
        "valuation": factor_scores.get("valuation") or row.get("valuationScore"),
        "technical": factor_scores.get("technical"),
        "catalyst": factor_scores.get("catalyst") if has_news or row.get("eventDate") else (40 if row.get("latestNewsTitle") else None),
        "sentiment": social_score,
        "industry": row.get("aiRepricingScore") or (68 if row.get("aiChainPrimaryId") else None),
        "risk": risk_score,
        "organization": org.get("score"),
    }
    notes = {
        "financials": "看收入、利润率、ROE、负债和现金流；缺项会降低研究置信度。",
        "valuation": "Forward PE、TTM PE、PEG和华尔街目标价必须同时看，避免低PE陷阱。",
        "technical": "只决定入场时机，不单独决定是否值得投。",
        "catalyst": "新闻/财报/评级/产品发布解释价格变化，但需要确认来源新鲜度。",
        "sentiment": "社媒、KOL、基金经理和议员交易只做overlay，不能单独触发交易。",
        "industry": "AI产业链位置决定重估弹性，也决定是否只是短期热度。",
        "risk": "看事件窗口、估值过热、拥挤度、数据质量和宏观反转。",
        "organization": "用于长期持有判断；短期交易不强行用组织分做买点。",
    }
    heat = []
    for key, name, _description in RESEARCH_HEATMAP_DIMS:
        score = _to_float(scores.get(key))
        level = _heat_level(score)
        heat.append(
            {
                "id": key,
                "nameZh": name,
                "score": _round(score, 1),
                "level": level,
                "labelZh": _heat_label(level),
                "noteZh": notes.get(key),
            }
        )
    return heat


def _prism_leader_overlay(row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "").upper()
    chain_ids = {
        str(row.get("aiChainPrimaryId") or "")
    } | {
        str(layer.get("id") or "")
        for layer in (row.get("aiChainLayers") or [])
    }
    matches = []
    direct_count = 0
    for leader in PRISM_THOUGHT_LEADERS:
        leader_symbols = {str(item).upper() for item in leader.get("symbols", [])}
        leader_chains = {str(item) for item in leader.get("chainIds", [])}
        direct = symbol in leader_symbols
        chain_match = bool(chain_ids & leader_chains)
        if not direct and not chain_match:
            continue
        if direct:
            direct_count += 1
        matches.append(
            {
                "_rank": 0 if direct else 1,
                "name": leader.get("name"),
                "roleZh": leader.get("roleZh"),
                "categoryZh": leader.get("categoryZh"),
                "worldviewZh": leader.get("worldviewZh"),
                "signalZh": leader.get("signalZh"),
                "matchTypeZh": "直接标的" if direct else "产业链共振",
            }
        )
    score = None
    if matches:
        matches.sort(key=lambda item: (item.get("_rank", 1), item.get("name") or ""))
        direct_matches = [item for item in matches if item.get("_rank") == 0]
        chain_matches = [item for item in matches if item.get("_rank") != 0][:2]
        matches = direct_matches + chain_matches
        score = min(96.0, 36.0 + len(direct_matches) * 15.0 + len(chain_matches) * 5.0)
        if len(direct_matches) >= 4:
            score = min(100.0, score + 4.0)
    for item in matches:
        item.pop("_rank", None)
    return {
        "score": _round(score, 1),
        "directCount": direct_count,
        "matchCount": len(matches),
        "matches": matches[:6],
        "summaryZh": " / ".join(match["name"] for match in matches[:4]) if matches else "暂无认知领袖共振",
    }


def _alpha_workflow_for_row(row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "").upper()
    factor_scores = row.get("factorScores") or {}
    org = _org_pattern(symbol)
    prism = _prism_leader_overlay(row)
    organization_score = org.get("score")
    decision_score = _to_float(row.get("decisionScore"))
    completeness = _money(row.get("decisionDataCompletenessPct"))
    technical = _to_float(factor_scores.get("technical"))
    catalyst = _to_float(factor_scores.get("catalyst"))
    ai_repricing = _to_float(row.get("aiRepricingScore") or factor_scores.get("aiRepricing"))
    fundamental = _to_float(factor_scores.get("fundamental"))
    valuation = _to_float(factor_scores.get("valuation") or row.get("valuationScore"))
    risk = _to_float(factor_scores.get("risk"))
    manager = _money(row.get("managerScore"))
    congress = _money(row.get("congressScore"))
    social_net = _money(row.get("socialNet"))
    sentiment_score = max(0.0, min(100.0, 50 + social_net * 18 + manager * 24 + congress * 18))

    cognitive = _avg_alpha([organization_score, prism.get("score")])
    if cognitive is None and symbol in {"QQQ", "SPY", "SMH", "SOXX"}:
        cognitive = 55.0
    endogenous = _avg_alpha([fundamental, valuation, risk])
    exogenous = _avg_alpha([ai_repricing, catalyst, technical])
    smart_money = sentiment_score if (manager or congress or row.get("socialNet") is not None or row.get("crowdingRisk")) else None

    pillar_scores = {
        "cognitive": cognitive,
        "endogenous": endogenous,
        "exogenous": exogenous,
        "smartMoney": smart_money,
    }
    weighted = 0.0
    used = 0.0
    for key, weight in PRISM_PILLAR_WEIGHTS.items():
        score = _to_float(pillar_scores.get(key))
        if score is None:
            continue
        weighted += score * weight
        used += weight
    alpha_signal = weighted / used if used else decision_score

    heatmap = _research_heatmap(row)
    heat_confidence = _avg_alpha([(item.get("level") or 0) * 20 for item in heatmap])
    research_confidence = _avg_alpha([completeness, heat_confidence])
    if research_confidence is not None and len(row.get("missingDecisionDataZh") or []) >= 5:
        research_confidence = max(0.0, research_confidence - 8)

    blockers = row.get("blockerFlagsZh") or []
    if row.get("positionHeld"):
        stage_id, stage_zh = "portfolio", "已入组合"
    elif blockers and (alpha_signal or 0) < 55:
        stage_id, stage_zh = "killed", "暂不推进"
    elif (alpha_signal or 0) >= 74 and (research_confidence or 0) >= 70 and not blockers:
        stage_id, stage_zh = "ic_decision", "投委待决策"
    elif (alpha_signal or 0) >= 66:
        stage_id, stage_zh = "org_research", "深度研究"
    elif (alpha_signal or 0) >= 55:
        stage_id, stage_zh = "screening", "基本面初筛"
    elif row.get("latestNewsTitle") or abs(_money(row.get("dayChangePct"))) >= 3:
        stage_id, stage_zh = "signal", "新信号"
    else:
        stage_id, stage_zh = "watchlist", "观察池"

    weakest = sorted(heatmap, key=lambda item: (item.get("level") or 0, item.get("score") or 0))[:2]
    if stage_id == "ic_decision":
        next_action = "可进入投委/Playbook草稿：先确认开盘VWAP、VIX/10Y/USD-CNH和新闻是否反转。"
    elif weakest:
        next_action = "优先补证：" + "、".join(item["nameZh"] for item in weakest) + "。补齐后再判断是否进Playbook。"
    else:
        next_action = "继续观察价格、新闻和财报事件。"

    return {
        "alphaSignalScore": _round(alpha_signal, 1),
        "researchConfidence": _round(research_confidence, 1),
        "alphaSignalLabelZh": _alpha_score_label(alpha_signal),
        "workflowStageId": stage_id,
        "workflowStageZh": stage_zh,
        "pillarScores": [
            {
                "id": key,
                "nameZh": name,
                "descriptionZh": description,
                "score": _round(pillar_scores.get(key), 1),
                "labelZh": _alpha_score_label(_to_float(pillar_scores.get(key))),
            }
            for key, name, description in ALPHA_PILLARS
        ],
        "researchHeatmap": heatmap,
        "researchBlindspotsZh": [item["nameZh"] for item in weakest if (item.get("level") or 0) <= 3],
        "orgPattern": org,
        "prismConsensusScore": prism.get("score"),
        "prismLeaderMatches": prism.get("matches", []),
        "prismLeaderSummaryZh": prism.get("summaryZh"),
        "nextResearchActionZh": next_action,
    }


def _alpha_lens_workflow_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stage_meta = [
        ("signal", "新信号", "异动/新闻/舆情触发，先判断值不值得看"),
        ("screening", "基本面初筛", "估值、财务、技术和事件做第一轮过滤"),
        ("org_research", "深度研究", "补组织质量、产业链位置和反方证据"),
        ("ic_decision", "投委待决策", "可生成Top3 pitch或Playbook草稿"),
        ("portfolio", "已入组合", "已有paper持仓，重点看加减仓条件"),
        ("watchlist", "观察池", "持续监控但当前不推进"),
        ("killed", "暂不推进", "风险/估值/数据质量不支持推进"),
    ]
    by_stage = {stage_id: [] for stage_id, _name, _desc in stage_meta}
    for row in rows:
        by_stage.setdefault(str(row.get("workflowStageId") or "watchlist"), []).append(row)

    stages = []
    for stage_id, name, description in stage_meta:
        members = sorted(by_stage.get(stage_id, []), key=lambda item: (_money(item.get("alphaSignalScore")), _money(item.get("researchConfidence"))), reverse=True)
        stages.append(
            {
                "id": stage_id,
                "nameZh": name,
                "descriptionZh": description,
                "count": len(members),
                "symbols": [row.get("symbol") for row in members[:10]],
                "avgAlphaSignal": _round(_avg_alpha([row.get("alphaSignalScore") for row in members]), 1),
                "avgResearchConfidence": _round(_avg_alpha([row.get("researchConfidence") for row in members]), 1),
            }
        )

    candidates = [
        row
        for row in rows
        if str(row.get("symbol") or "").upper() not in STOCK_PITCH_EXCLUDE
        and row.get("alphaSignalScore") is not None
    ]
    candidates.sort(key=lambda row: (_money(row.get("alphaSignalScore")), _money(row.get("researchConfidence")), _money(row.get("decisionScore"))), reverse=True)
    top = []
    for row in candidates[:8]:
        top.append(
            {
                "symbol": row.get("symbol"),
                "alphaSignalScore": row.get("alphaSignalScore"),
                "researchConfidence": row.get("researchConfidence"),
                "stageZh": row.get("workflowStageZh"),
                "pillarScores": row.get("pillarScores", []),
                "blindspotsZh": row.get("researchBlindspotsZh", []),
                "prismConsensusScore": row.get("prismConsensusScore"),
                "prismLeaderMatches": row.get("prismLeaderMatches", []),
                "prismLeaderSummaryZh": row.get("prismLeaderSummaryZh"),
                "nextActionZh": row.get("nextResearchActionZh"),
                "buyConditionZh": row.get("buyConditionZh"),
            }
        )

    blindspots = Counter(
        field
        for row in candidates[:30]
        for field in (row.get("researchBlindspotsZh") or [])
    )
    org_rows = [
        {
            "symbol": row.get("symbol"),
            "grade": (row.get("orgPattern") or {}).get("grade"),
            "score": (row.get("orgPattern") or {}).get("score"),
            "confidence": (row.get("orgPattern") or {}).get("confidence"),
            "summaryZh": (row.get("orgPattern") or {}).get("summaryZh"),
        }
        for row in candidates
        if (row.get("orgPattern") or {}).get("score") is not None
    ]
    org_rows.sort(key=lambda row: _money(row.get("score")), reverse=True)
    return {
        "titleZh": "三步工作流：信号 → 初筛 → 组织Pattern",
        "methodZh": "借鉴 AlphaLens：先看四维信号共振，再看研究热力图补盲区，最后用组织质量判断是否值得长期持有。",
        "policyZh": "Alpha Signal 只决定研究优先级；进入Playbook仍必须经过投委和你的人工批准。",
        "stages": stages,
        "topConvergence": top,
        "commonBlindspots": [{"fieldZh": key, "count": value} for key, value in blindspots.most_common(6)],
        "orgQualityLeaders": org_rows[:8],
        "prismMethodology": {
            "titleZh": "Prism Alpha：六层漏斗 × 四维权重 × 认知领袖校准",
            "sourceUrl": "https://qingli-prism-alpha-11000-to-2.surge.sh/",
            "weights": [
                {"id": "cognitive", "nameZh": "P0 认知/组织", "weightPct": 25, "logicZh": "认知是因，财务是果；用创始人世界观和认知领袖共振校准。"},
                {"id": "endogenous", "nameZh": "P1 内生质量", "weightPct": 30, "logicZh": "财务是最硬反证，ROIC/FCF/毛利率/估值必须能支撑故事。"},
                {"id": "exogenous", "nameZh": "P2 外生叙事", "weightPct": 25, "logicZh": "TAM、技术催化、新闻/宏观/舆情决定赛道顺风。"},
                {"id": "smartMoney", "nameZh": "P3 聪明钱", "weightPct": 20, "logicZh": "13F/基金/议员/战略投资只做验证，不做唯一买入理由。"},
            ],
            "funnelLevels": PRISM_FUNNEL_LEVELS,
            "integrationZh": "已把权重切到 P0/P1/P2/P3 = 25/30/25/20；P0 增加认知领袖共振 overlay，但真实 playbook 仍必须经过投委和人工批准。",
        },
        "thoughtLeaderLibrary": {
            "titleZh": "认知领袖库",
            "summaryZh": "用于校准 P0：当一个标的的世界观同时被多位独立领袖验证，研究优先级上调；当只有叙事没有财务或价格确认，仍只进入观察。",
            "leaders": PRISM_THOUGHT_LEADERS,
        },
        "summaryZh": f"当前覆盖 {len(rows)} 个标的；投委待决策 {len(by_stage.get('ic_decision', []))} 个，深度研究 {len(by_stage.get('org_research', []))} 个，已入组合 {len(by_stage.get('portfolio', []))} 个。",
    }


def _pitch_theme_reason(row: dict[str, Any]) -> dict[str, str]:
    group = str(row.get("group") or "")
    symbol = str(row.get("symbol") or "")
    if "存储" in group or "内存" in group:
        return {
            "title": "主题/基本面：AI 存储周期",
            "detail": f"{symbol} 属于 AI 存储/内存链，受益于训练、推理、HBM/SSD 和数据中心资本开支叙事。",
        }
    if "芯片" in group or "算力" in group:
        return {
            "title": "主题/基本面：AI 算力主线",
            "detail": f"{symbol} 仍在 AI 算力、半导体或先进制造链条里，是当前资金最容易继续跟踪的主线之一。",
        }
    if "平台" in group or "软件" in group:
        return {
            "title": "主题/基本面：AI 软件变现",
            "detail": f"{symbol} 属于 AI 平台/软件层，核心问题是收入增速能否继续证明估值。",
        }
    if "电力" in group or "基础设施" in group:
        return {
            "title": "主题/基本面：AI 物理基础设施",
            "detail": f"{symbol} 对应 AI 数据中心、电力、散热、网络或托管需求，是算力扩张的配套链条。",
        }
    if "Crypto" in group or "Fintech" in group:
        return {
            "title": "主题/基本面：金融科技/支付催化",
            "detail": f"{symbol} 受政策、交易量、稳定币或支付基础设施叙事影响，弹性高但波动也高。",
        }
    return {
        "title": "主题/基本面：组合相关主题",
        "detail": f"{symbol} 在观察池中有明确主题归属，需要用价格确认来验证 thesis。",
    }


def _pitch_score(row: dict[str, Any]) -> float:
    if not row:
        return -999.0
    symbol = str(row.get("symbol") or "").upper()
    if symbol in STOCK_PITCH_EXCLUDE:
        return -999.0

    data_quality = str(row.get("dataQuality") or "")
    decision_quality = str(row.get("decisionDataQuality") or "")
    completeness = _money(row.get("decisionDataCompletenessPct"))
    has_non_price_signal = bool(
        row.get("aiRepricingScore")
        or row.get("latestNewsTitle")
        or row.get("peRatio")
        or row.get("forwardPe")
        or row.get("forwardPE")
        or row.get("priceTarget")
        or row.get("wallStreetTargetPrice")
        or row.get("managerScore")
    )
    if decision_quality == "FAIL" and completeness < 35 and not has_non_price_signal:
        return -999.0

    momentum = _money(row.get("momentum30dPct"))
    day = _money(row.get("dayChangePct"))
    manager_score = _money(row.get("managerScore"))
    social_net = _money(row.get("socialNet"))
    attention = _money(row.get("attentionScore"))
    ai_repricing = _money(row.get("aiRepricingScore"))
    alpha_signal = _money(row.get("alphaSignalScore"))
    research_confidence = _money(row.get("researchConfidence"))
    org_score = _money((row.get("orgPattern") or {}).get("score"))
    prism_score = _money(row.get("prismConsensusScore"))
    activist_score = _money(row.get("activistAlphaScore"))
    activist_core = str(row.get("activistCoreRelevance") or "")
    score = 8.0 + attention * 24.0 + manager_score * 26.0
    score += max(-12.0, min(24.0, momentum * 0.45))
    score += max(-8.0, min(8.0, day * 0.7))
    score += max(-4.0, min(7.0, social_net * 7.0))
    if alpha_signal:
        score += max(-8.0, min(18.0, (alpha_signal - 58.0) * 0.45))
    if research_confidence:
        score += max(-5.0, min(10.0, (research_confidence - 55.0) * 0.25))
    if org_score:
        score += max(0.0, min(5.0, (org_score - 6.5) * 2.0))
    if prism_score:
        score += max(0.0, min(6.0, (prism_score - 58.0) * 0.18))
    if activist_score:
        activist_multiplier = 1.0 if activist_core == "HIGH" else 0.55 if activist_core == "MEDIUM" else 0.25
        score += max(0.0, min(5.0, (activist_score - 35.0) * 0.16 * activist_multiplier))
    if row.get("workflowStageId") == "ic_decision":
        score += 8.0
    elif row.get("workflowStageId") == "org_research":
        score += 4.0
    if len(row.get("researchBlindspotsZh") or []) >= 3:
        score -= 4.0
    if ai_repricing:
        score += max(0.0, min(18.0, (ai_repricing - 50.0) * 0.45))

    if row.get("aboveMa50"):
        score += 14.0
    elif row.get("aboveMa50") is False:
        score -= 12.0
    if row.get("latestNewsTitle"):
        score += 7.0
    if row.get("positionHeld"):
        score += 4.0
    if row.get("crowdingRisk") == "HIGH":
        score -= 4.0
    if str(row.get("eventRisk") or "").upper() == "HIGH":
        score -= 18.0
    if data_quality == "MISSING":
        score -= 10.0
    if decision_quality == "WARN":
        score -= 4.0
    if day > 9:
        score -= 5.0
    return round(score, 2)


def _discovery_pitch_score(row: dict[str, Any]) -> float:
    if not row:
        return -999.0
    symbol = str(row.get("symbol") or "").upper()
    if not symbol or "." in symbol or symbol in DISCOVERY_PITCH_EXCLUDE:
        return -999.0
    if row.get("positionHeld"):
        return -999.0

    data_quality = str(row.get("dataQuality") or "")
    stage = str(row.get("workflowStageId") or "")
    if stage == "killed" or data_quality == "MISSING":
        return -999.0

    score = 8.0
    alpha_signal = _money(row.get("alphaSignalScore"))
    research_confidence = _money(row.get("researchConfidence"))
    valuation_score = _money(row.get("valuationScore"))
    prism_score = _money(row.get("prismConsensusScore"))
    ai_repricing = _money(row.get("aiRepricingScore"))
    activist_score = _money(row.get("activistAlphaScore"))
    activist_core = str(row.get("activistCoreRelevance") or "")
    momentum = _money(row.get("momentum30dPct"))
    day = _money(row.get("dayChangePct"))
    target_upside = _money(row.get("targetUpsidePct"))
    price_to_ma50 = _money(row.get("priceToMa50Pct"))

    score += max(-8.0, min(16.0, (alpha_signal - 55.0) * 0.45))
    score += max(-5.0, min(12.0, (research_confidence - 55.0) * 0.30))
    score += max(-10.0, min(14.0, (valuation_score - 50.0) * 0.45))
    score += max(0.0, min(12.0, (prism_score - 55.0) * 0.26))
    score += max(0.0, min(10.0, (ai_repricing - 50.0) * 0.25))
    if activist_score:
        activist_multiplier = 1.0 if activist_core == "HIGH" else 0.65 if activist_core == "MEDIUM" else 0.35
        score += max(0.0, min(10.0, (activist_score - 32.0) * 0.35 * activist_multiplier))

    chain_ids = {str(item.get("id")) for item in row.get("aiChainLayers", []) if item.get("id")}
    score += min(14.0, sum(DISCOVERY_CHAIN_BONUS.get(item, 0.0) for item in chain_ids))

    if stage == "org_research":
        score += 8.0
    elif stage == "ic_decision":
        score += 7.0
    elif stage == "screening":
        score += 4.0
    elif stage == "signal":
        score += 2.0

    if target_upside >= 20:
        score += 10.0
    elif target_upside >= 8:
        score += 5.0
    elif target_upside < -8:
        score -= 13.0
    elif target_upside < 0:
        score -= 6.0

    if 0 <= price_to_ma50 <= 18:
        score += 8.0
    elif 18 < price_to_ma50 <= 32:
        score += 3.0
    elif price_to_ma50 > 40:
        score -= 10.0
    elif price_to_ma50 < -8:
        score -= 6.0

    if 8 <= momentum <= 55:
        score += 6.0
    elif momentum > 80:
        score -= 5.0
    elif momentum < -10:
        score -= 6.0

    if day > 9:
        score -= 8.0
    elif day <= -10:
        score -= 18.0
    elif day < -8:
        score -= 10.0

    if row.get("latestNewsTitle"):
        score += 3.0
    if len(row.get("researchBlindspotsZh") or []) >= 4:
        score -= 5.0
    return round(score, 2)


def _pitch_target_weight(row: dict[str, Any], score: float, macro: str, cash_pct: float) -> float:
    if str(row.get("eventRisk") or "").upper() == "HIGH":
        return 0.0
    if cash_pct < 15:
        return 1.0
    target = 2.0
    if score >= 45:
        target = 3.0
    if score >= 62:
        target = 4.0
    if row.get("positionHeld"):
        target = min(target, 2.0)
    if macro == "HAWKISH":
        target = min(target, 3.0)
    if row.get("crowdingRisk") == "HIGH":
        target = max(1.0, target - 0.5)
    return round(target, 1)


def _daily_pitch_reasons(row: dict[str, Any]) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    momentum = row.get("momentum30dPct")
    day = row.get("dayChangePct")
    if momentum is not None or day is not None:
        trend_detail = []
        if momentum is not None:
            trend_detail.append(f"30日动量 {float(momentum):.2f}%")
        if day is not None:
            trend_detail.append(f"当日涨跌 {float(day):.2f}%")
        trend_detail.append("站上 MA50" if row.get("aboveMa50") else "未确认 MA50")
        reasons.append({"title": "技术面：趋势和相对强度", "detail": "，".join(trend_detail) + "。"})

    if row.get("alphaSignalScore") is not None:
        pillar_text = " / ".join(
            f"{item.get('nameZh')} {item.get('score')}"
            for item in (row.get("pillarScores") or [])[:4]
            if item.get("score") is not None
        )
        blindspots = "、".join(row.get("researchBlindspotsZh") or [])
        reasons.append(
            {
                "title": "AlphaLens：四维信号共振",
                "detail": (
                    f"Alpha {row.get('alphaSignalScore')}，研究置信 {row.get('researchConfidence')}；"
                    f"{pillar_text or '四维分数待刷新'}。"
                    + (f" 主要盲区：{blindspots}。" if blindspots else "")
                ),
            }
        )

    prism_matches = row.get("prismLeaderMatches") or []
    if prism_matches:
        leaders = " / ".join(str(item.get("name")) for item in prism_matches[:3] if item.get("name"))
        signals = "；".join(str(item.get("signalZh")) for item in prism_matches[:2] if item.get("signalZh"))
        reasons.append(
            {
                "title": "Prism：认知领袖共振",
                "detail": f"共振领袖：{leaders or '待补充'}。{signals or row.get('prismLeaderSummaryZh') or ''}",
            }
        )

    if row.get("aiRepricingScore"):
        ai_reasons = row.get("aiRepricingReasonsZh") or []
        reasons.append(
            {
                "title": f"AI产业链重估：{row.get('aiRepricingStageZh') or '需求传导/架构新节点'}",
                "detail": "；".join(str(item) for item in ai_reasons[:2]) or "符合需求传导时间差、Qual 周期或架构新节点重估框架。",
            }
        )

    activist = row.get("activistAlpha") or {}
    if activist:
        reasons.append(
            {
                "title": "Activist Alpha：13D/治理催化",
                "detail": (
                    f"{activist.get('investor') or 'activist'} / {activist.get('stageZh') or '事件驱动'}；"
                    f"{activist.get('thesisTypeZh') or ''}。触发：{activist.get('catalystZh') or '待核验 SEC 13D/13D-A'}"
                )[:220],
            }
        )

    reasons.append(_pitch_theme_reason(row))

    if row.get("latestNewsTitle"):
        reasons.append(
            {
                "title": "新闻/催化：最新事件可解释价格",
                "detail": str(row.get("latestNewsTitle"))[:160],
            }
        )

    manager_score = _money(row.get("managerScore"))
    if manager_score > 0:
        reasons.append(
            {
                "title": "机构/基金经理：披露持仓 overlay",
                "detail": f"基金经理持仓共识分数 {manager_score:.2f}，只能作为滞后确认，不作为单独买入理由。",
            }
        )

    if row.get("socialLabelZh") or row.get("crowdingRisk"):
        reasons.append(
            {
                "title": "舆情/拥挤度：辅助确认",
                "detail": f"舆情 {row.get('socialLabelZh') or '未知'}，拥挤度 {row.get('crowdingRisk') or 'UNKNOWN'}。",
            }
        )

    return reasons[:3]


def _daily_stock_pitches(
    state: dict[str, Any],
    watchlist: dict[str, Any],
    portfolio: dict[str, Any],
    cash_pct: float,
    committee: dict[str, Any],
) -> dict[str, Any]:
    macro = str(state.get("macro_regime") or "UNKNOWN")
    committee_decision = committee.get("decision") or "UNKNOWN"
    committee_at = committee.get("timestamp")
    rows = []
    for row in watchlist.get("rows", []):
        symbol = str(row.get("symbol") or "").upper()
        if not symbol or symbol in STOCK_PITCH_EXCLUDE:
            continue
        score = _pitch_score(row)
        if score <= -100:
            continue
        rows.append((score, row))

    rows.sort(key=lambda item: item[0], reverse=True)
    pitches = []
    for rank, (score, row) in enumerate(rows[:3], start=1):
        symbol = str(row.get("symbol") or "").upper()
        target_weight = _pitch_target_weight(row, score, macro, cash_pct)
        if target_weight <= 0:
            action = "进入高优先级观察；暂不执行，等财报/事件风险结束后再定价。"
            entry = "不设置买入触发价；先等事件后价格、成交量和新闻确认。"
        elif row.get("positionHeld"):
            action = f"已有 paper 持仓；若明天开盘后 15-30 分钟守住 VWAP，可考虑把该标的增加约 {target_weight:.1f}% paper 权重。"
            entry = "开盘后 15-30 分钟站稳 VWAP，且 QQQ/相关板块没有同步转弱。"
        else:
            action = f"明天作为首仓候选；若开盘后确认强度，可考虑 {target_weight:.1f}% paper starter，不追跳空。"
            entry = "开盘后 15-30 分钟站稳 VWAP 或回踩不破前一日关键位，同时 VIX/10Y/USD-CNH 没有风险反转。"
        invalidation = "跌破前一交易日低点或 VWAP，相关板块转弱，或新闻催化被证伪时取消。"
        pitches.append(
            {
                "rank": rank,
                "symbol": symbol,
                "score": score,
                "group": row.get("group"),
                "last": row.get("last"),
                "dayChangePct": row.get("dayChangePct"),
                "momentum30dPct": row.get("momentum30dPct"),
                "alphaSignalScore": row.get("alphaSignalScore"),
                "researchConfidence": row.get("researchConfidence"),
                "workflowStageZh": row.get("workflowStageZh"),
                "researchBlindspotsZh": row.get("researchBlindspotsZh", []),
                "targetWeightPct": target_weight,
                "stanceZh": "优先候选" if target_weight > 0 else "观察候选",
                "actionZh": action,
                "entryTriggerZh": entry,
                "invalidationZh": invalidation,
                "topReasons": _daily_pitch_reasons(row),
                "latestNewsTitle": row.get("latestNewsTitle"),
                "latestNewsUrl": row.get("latestNewsUrl"),
                "riskNoteZh": "只允许本地 paper trade；不读取真实账户，不提交券商订单。",
                "committeeGateZh": f"投委状态：{committee_decision} / {committee_at or '未生成'}；候选仍需你批准后才进入 playbook。",
            }
        )

    prompt_lines = [
        "我确认要基于今日 Top 3 stock pitch 生成明天的 conditional paper playbook。",
        "请保持 advisory-only / paper-trade only：只写本地 conditional-playbook，不解锁交易，不提交券商订单，不读取或修改真实账户。",
        "",
        "候选如下：",
    ]
    for pitch in pitches:
        prompt_lines.append(
            f"- {pitch['symbol']}：目标 paper 权重 {pitch['targetWeightPct']}%；"
            f"触发：{pitch['entryTriggerZh']}；失效：{pitch['invalidationZh']}；理由："
            + " / ".join(reason["title"] for reason in pitch.get("topReasons", []))
        )
    prompt_lines += [
        "",
        "请为每个候选生成明天美股交易时段可执行的条件场景：valid_after/valid_until、price/VIX/10Y/USD-CNH 条件、最大亏损、失效条件。",
        "如果缺少实时价位，请先生成 APPROVED_PENDING_LEVELS 草案；只有在我明确写“批准执行明日 playbook”后，才允许改成 ARMED/APPROVED。",
        "生成后刷新 conditional_playbook、order_intents、dashboard_snapshot，并发布 dashboard 快照。",
    ]

    return {
        "titleZh": "每日 Top 3 股票 Pitch",
        "methodZh": "综合 AlphaLens 四维信号、研究置信度、行情动量、MA50、估值/目标价、新闻事件、基金经理/议员 overlay、社媒拥挤度、财报风险和当前现金比例打分。",
        "policyZh": "这些是 paper trade 候选，不是实盘订单；你批准后才会写入明日 conditional playbook。",
        "committeeGateZh": f"已接入投委 gate：{committee_decision}。若投委不是 PAPER_REVIEW_READY/WATCH_EQUITY_ONLY，只能观察。",
        "pitches": pitches,
        "batchPlaybookPromptZh": "\n".join(prompt_lines),
    }


def _daily_discovery_pitches(
    watchlist: dict[str, Any],
    cash_pct: float,
    committee: dict[str, Any],
) -> dict[str, Any]:
    committee_decision = committee.get("decision") or "UNKNOWN"
    committee_at = committee.get("timestamp")
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in watchlist.get("rows", []):
        score = _discovery_pitch_score(row)
        if score <= -100:
            continue
        scored.append((score, row))

    scored.sort(key=lambda item: item[0], reverse=True)
    pitches = []
    for rank, (score, row) in enumerate(scored[:3], start=1):
        symbol = str(row.get("symbol") or "").upper()
        day = _money(row.get("dayChangePct"))
        price_to_ma50 = _money(row.get("priceToMa50Pct"))
        target_upside = _money(row.get("targetUpsidePct"))
        if day <= -10:
            target_weight = 0.0
            action = "事件后大跌观察；不进入明天 paper starter。先确认这不是基本面/指引恶化，而不是简单错杀。"
            entry = "至少等待 1-2 个交易日：重新站回 VWAP，成交量不再放大下杀，并且公司/分析师解释供应链、利润率或指引压力可控后再评估。"
        elif day > 9 or price_to_ma50 > 35 or target_upside < 0:
            target_weight = 1.0
            action = "发现型候选；不追高，先写入观察/条件首仓草稿，只有回踩或盘中确认后才允许 1.0% paper starter。"
            entry = "开盘后不直接追；等待回踩 VWAP/前一日关键位不破，或至少 30 分钟横盘消化后重新站上 VWAP。"
        else:
            target_weight = 2.0 if cash_pct >= 15 else 1.0
            action = f"发现型首仓候选；若明天确认强度，可考虑 {target_weight:.1f}% paper starter。"
            entry = "开盘后 15-30 分钟站稳 VWAP，且同赛道没有同步转弱；优先等回踩，不用市价追。"
        invalidation = "跌破前一交易日低点或 VWAP，目标价/新闻催化被证伪，或同赛道热度快速退潮时取消。"
        pitches.append(
            {
                "rank": rank,
                "symbol": symbol,
                "score": score,
                "group": row.get("group"),
                "aiChainPrimaryNameZh": row.get("aiChainPrimaryNameZh"),
                "last": row.get("last"),
                "dayChangePct": row.get("dayChangePct"),
                "momentum30dPct": row.get("momentum30dPct"),
                "priceToMa50Pct": row.get("priceToMa50Pct"),
                "targetUpsidePct": row.get("targetUpsidePct"),
                "valuationScore": row.get("valuationScore"),
                "valuationBucketZh": row.get("valuationBucketZh"),
                "alphaSignalScore": row.get("alphaSignalScore"),
                "researchConfidence": row.get("researchConfidence"),
                "prismConsensusScore": row.get("prismConsensusScore"),
                "prismLeaderSummaryZh": row.get("prismLeaderSummaryZh"),
                "workflowStageZh": row.get("workflowStageZh"),
                "targetWeightPct": target_weight,
                "stanceZh": "发现型候选",
                "actionZh": action,
                "entryTriggerZh": entry,
                "invalidationZh": invalidation,
                "topReasons": _daily_pitch_reasons(row),
                "latestNewsTitle": row.get("latestNewsTitle"),
                "latestNewsUrl": row.get("latestNewsUrl"),
                "riskNoteZh": "发现型候选波动更高；只允许 paper trade，不读取真实账户，不提交券商订单。",
                "committeeGateZh": f"投委状态：{committee_decision} / {committee_at or '未生成'}；发现型候选默认只能先进入 APPROVED_PENDING_LEVELS 草稿。",
            }
        )

    prompt_lines = [
        "请基于我勾选的发现型 Top 3 候选生成明天的 conditional paper playbook 草稿。",
        "只处理下面列出的发现型候选；保持 advisory-only / paper-trade only：只写本地草稿，不解锁交易，不提交券商订单，不读取或修改真实账户。",
        "",
        "候选如下：",
    ]
    for pitch in pitches:
        prompt_lines.append(
            f"- {pitch['symbol']}：目标 paper 权重 {pitch['targetWeightPct']}%；"
            f"触发：{pitch['entryTriggerZh']}；失效：{pitch['invalidationZh']}；理由："
            + " / ".join(reason["title"] for reason in pitch.get("topReasons", []))
        )

    return {
        "titleZh": "发现型 Top 3：next AMD / SNDK / 康宁",
        "methodZh": "强制排除已持仓和 mega-cap，优先寻找 AI 产业链瓶颈、收入高增、目标价空间、Prism/KOL 共振和可验证催化；默认不追高。",
        "policyZh": "发现型候选只进入 paper starter 条件草稿；它们不是确定买入，也不是实盘订单。",
        "committeeGateZh": f"投委状态：{committee_decision}。候选仍需你批准后才写入 playbook。",
        "pitches": pitches,
        "batchDiscoveryPlaybookPromptZh": "\n".join(prompt_lines),
    }


OPTION_PITCH_EXCLUDE = {
    "SNXX",
    "MULL",
    "DRAM",
    "EWY",
    "005930.KS",
    "000660.KS",
    "285A.T",
    "2408.TW",
    "2344.TW",
}

OPTION_PITCH_SYMBOLS = {
    "QQQ",
    "SPY",
    "SMH",
    "SOXX",
    "NVDA",
    "AMD",
    "MU",
    "SNDK",
    "WDC",
    "STX",
    "PLTR",
    "AVGO",
    "AMZN",
    "GOOG",
    "GOOGL",
    "INTC",
    "ALAB",
    "MRVL",
    "LITE",
    "SMCI",
    "DELL",
    "CRWV",
    "BE",
    "CRCL",
    "TSLA",
}


def _option_price(contract: dict[str, Any]) -> float | None:
    for key in ("mid", "ask", "lastPrice", "estimatedPremium"):
        value = _to_float(contract.get(key))
        if value is not None and value > 0:
            return value
    return None


def _round_option_strike(value: float) -> float:
    if value >= 500:
        increment = 10.0
    elif value >= 100:
        increment = 5.0
    elif value >= 25:
        increment = 2.5
    else:
        increment = 1.0
    return round(round(value / increment) * increment, 2)


def _select_option_contract(
    options: dict[str, Any],
    symbol: str,
    side: str,
    spot: float,
) -> dict[str, Any] | None:
    symbol_options = (options.get("symbols") or {}).get(symbol.upper(), {})
    chains = symbol_options.get("chains", {}) if isinstance(symbol_options, dict) else {}
    if not chains or not spot:
        return None

    side_key = "calls" if side == "CALL" else "puts"
    target_strike = spot * (0.99 if side == "CALL" else 1.01)
    candidates: list[tuple[float, dict[str, Any]]] = []
    for expiration, chain in chains.items():
        dte = _to_float(chain.get("dte"))
        if dte is None or dte < 14 or dte > 70:
            continue
        for raw in chain.get(side_key, []) or []:
            strike = _to_float(raw.get("strike"))
            price = _option_price(raw)
            if strike is None or price is None:
                continue
            spread = _to_float(raw.get("spreadPct"))
            if spread is not None and spread > 45:
                continue
            volume = _money(raw.get("volume"))
            open_interest = _money(raw.get("openInterest"))
            liquidity_score = min(12.0, math.log1p(volume + open_interest))
            distance_penalty = abs(strike - target_strike) / spot * 120
            dte_penalty = abs(dte - 35) * 0.18
            spread_penalty = (spread or 30) * 0.08
            score = liquidity_score - distance_penalty - dte_penalty - spread_penalty
            contract = {
                **raw,
                "side": side,
                "expiration": expiration,
                "dte": int(dte),
                "selectedPrice": _round(price, 2),
                "contractCostUsd": _round(price * 100, 2),
                "dataQuality": "REAL_CHAIN",
                "priceSourceZh": "真实期权链 mid/ask/last",
            }
            candidates.append((score, contract))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _estimated_option_contract(symbol: str, side: str, spot: float, momentum: float) -> dict[str, Any]:
    strike = _round_option_strike(spot * (0.99 if side == "CALL" else 1.01))
    base_premium_pct = 0.055 + min(0.035, abs(momentum) / 700)
    if side == "PUT":
        base_premium_pct += 0.005
    premium = max(0.5, spot * base_premium_pct)
    return {
        "contractSymbol": f"{symbol} {side} EST",
        "side": side,
        "expiration": "约 30-45D",
        "dte": 35,
        "strike": _round(strike, 2),
        "selectedPrice": _round(premium, 2),
        "contractCostUsd": _round(premium * 100, 2),
        "bid": None,
        "ask": None,
        "mid": _round(premium, 2),
        "spreadPct": None,
        "volume": None,
        "openInterest": None,
        "impliedVolatility": None,
        "dataQuality": "ESTIMATE",
        "priceSourceZh": "估算价；缺真实 option chain，不能直接下单",
    }


def _option_direction_score(row: dict[str, Any], side: str) -> float:
    momentum = _money(row.get("momentum30dPct"))
    day = _money(row.get("dayChangePct"))
    decision = _money(row.get("decisionScore"))
    valuation = _money(row.get("valuationScore"))
    target_upside = _money(row.get("targetUpsidePct"))
    social = _money(row.get("socialNet"))
    price_to_ma50 = _money(row.get("priceToMa50Pct"))

    if side == "CALL":
        score = 24.0 + decision * 0.28 + valuation * 0.12
        score += max(-14.0, min(24.0, momentum * 0.48))
        score += 10.0 if row.get("aboveMa50") else -10.0
        score += max(-6.0, min(10.0, target_upside * 0.18))
        score += max(-4.0, min(6.0, social * 6.0))
        if row.get("latestNewsTitle"):
            score += 5.0
        if day > 9:
            score -= 10.0
        if price_to_ma50 > 25:
            score -= 12.0
    else:
        score = 18.0 + max(0.0, (65.0 - decision) * 0.25)
        score += max(-8.0, min(18.0, -momentum * 0.55))
        score += 10.0 if row.get("aboveMa50") is False else -6.0
        score += max(-6.0, min(10.0, -target_upside * 0.22))
        score += max(-4.0, min(6.0, -social * 6.0))
        if day < -4:
            score += 5.0
    if row.get("crowdingRisk") == "HIGH":
        score -= 6.0
    return round(max(0.0, min(100.0, score)), 2)


def _option_quality_gate(
    row: dict[str, Any],
    side: str,
    contract: dict[str, Any],
    macro: str,
    earnings_source_gap: bool,
) -> dict[str, Any]:
    """Quality-adjusted option confidence.

    This is intentionally stricter than the equity pitch score.  A directional
    long call/put should not be called high probability unless the thesis,
    catalyst, event calendar, sentiment, and actual option chain all line up.
    """
    missing: list[str] = []
    positives: list[str] = []
    blockers: list[str] = []
    score = 0.0

    real_chain = contract.get("dataQuality") == "REAL_CHAIN"
    if real_chain:
        score += 16
        spread = _to_float(contract.get("spreadPct"))
        if spread is not None and spread <= 25:
            score += 7
            positives.append(f"真实期权链可用，bid/ask spread {spread:.1f}%")
        else:
            blockers.append("真实期权链价差过宽或缺 spread")
        liquidity = _money(contract.get("volume")) + _money(contract.get("openInterest"))
        if liquidity >= 300:
            score += 7
            positives.append(f"期权 volume+OI 约 {liquidity:.0f}，流动性可接受")
        else:
            blockers.append("期权 volume/OI 不足，容易滑点")
    else:
        blockers.append("缺真实 option chain / bid / ask / IV / OI")

    if earnings_source_gap:
        blockers.append("财报日历源存在缺口，不能确认近端财报风险")
    elif row.get("eventDate"):
        blockers.append(f"近端事件/财报窗口：{row.get('eventDate')}")
    else:
        score += 10
        positives.append("未命中已知财报/事件禁开窗口")

    decision = _money(row.get("decisionScore"))
    valuation = _money(row.get("valuationScore"))
    target_upside = _money(row.get("targetUpsidePct"))
    revenue_growth = _to_float(row.get("revenueGrowth"))
    earnings_growth = _to_float(row.get("earningsGrowth"))
    forward_pe = _to_float(row.get("forwardPE"))
    day = _money(row.get("dayChangePct"))
    momentum = _money(row.get("momentum30dPct"))
    price_to_ma50 = _money(row.get("priceToMa50Pct"))

    if row.get("decisionDataQuality") == "PASS" or decision >= 65:
        score += min(14, max(0, (decision - 55) * 0.45))
        positives.append(f"综合选股分 {decision:.1f}")
    else:
        missing.append("综合选股分/基本面质量不足")

    if row.get("valuationDataQuality") == "PASS":
        score += min(10, max(0, (valuation - 50) * 0.25))
        if side == "CALL":
            if target_upside >= 12:
                score += 8
                positives.append(f"华尔街目标价隐含上行 {target_upside:.1f}%")
            elif target_upside < 0:
                blockers.append(f"目标价隐含下行 {target_upside:.1f}%，不支持追 call")
            if forward_pe and 0 < forward_pe <= 60:
                score += 4
                positives.append(f"Forward PE {forward_pe:.1f} 仍可解释")
        else:
            if target_upside <= -8:
                score += 8
                positives.append(f"目标价隐含下行 {target_upside:.1f}%")
            elif target_upside > 15:
                blockers.append(f"目标价隐含上行 {target_upside:.1f}%，不支持 put")
    else:
        missing.append("PE/Forward PE/目标价数据缺口")

    if side == "CALL":
        if revenue_growth is not None and revenue_growth > 0:
            score += 4
            positives.append(f"收入增长 {revenue_growth:.1%}")
        if earnings_growth is not None and earnings_growth > 0:
            score += 4
            positives.append(f"盈利增长 {earnings_growth:.1%}")
        if row.get("aboveMa50") and price_to_ma50 <= 20:
            score += 6
            positives.append("趋势在 MA50 上方且不过度延展")
        elif price_to_ma50 > 25:
            blockers.append(f"距离 MA50 +{price_to_ma50:.1f}%，追 call 风险高")
        if day > 8:
            blockers.append(f"单日已涨 {day:.1f}%，不追高开新 call")
    else:
        if row.get("aboveMa50") is False:
            score += 6
            positives.append("跌破 MA50，put 方向有趋势确认")
        if momentum < -8:
            score += 5
            positives.append(f"30日动量 {momentum:.1f}% 偏弱")
        if day < -4:
            score += 4
            positives.append(f"单日下跌 {day:.1f}%，空头动量确认")

    social_label = row.get("socialLabelZh")
    crowding = row.get("crowdingRisk")
    social_net = _money(row.get("socialNet"))
    if social_label:
        if side == "CALL" and social_net > 0 and crowding != "HIGH":
            score += 8
            positives.append(f"舆情偏正向且拥挤度 {crowding or '未知'}")
        elif side == "PUT" and social_net < 0:
            score += 8
            positives.append("舆情偏负面，支持 put thesis")
        elif crowding == "HIGH":
            blockers.append("舆情拥挤度 HIGH，期权追涨胜率下降")
    else:
        missing.append("标的级社媒舆情缺口")

    if row.get("latestNewsTitle"):
        score += 10
        positives.append(f"近端新闻/催化：{str(row.get('latestNewsTitle'))[:90]}")
    else:
        missing.append("缺近端新闻/催化或预期差")

    if macro == "HAWKISH":
        blockers.append("宏观偏鹰，长久期成长股期权需降级")
    else:
        score += 3

    confidence = max(45.0, min(92.0, 48.0 + score * 0.48 - len(blockers) * 4.0 - len(missing) * 2.0))
    if not real_chain:
        confidence = min(confidence, 67.0)
    if earnings_source_gap:
        confidence = min(confidence, 72.0)
    if not row.get("latestNewsTitle"):
        confidence = min(confidence, 74.0)
    if len(blockers) >= 2:
        confidence = min(confidence, 79.0)

    actionable = confidence >= 80.0 and not blockers
    if actionable:
        gate_zh = "80%+ 高置信候选"
    elif confidence >= 80.0 and blockers:
        gate_zh = "WATCH_ONLY：置信达标但有阻断项"
    else:
        gate_zh = "WATCH_ONLY：置信未达 80%"
    return {
        "confidencePct": _round(confidence, 1),
        "actionable": actionable,
        "positives": positives[:5],
        "missing": missing[:5],
        "blockers": blockers[:5],
        "gateZh": gate_zh,
    }


def _daily_option_reasons(row: dict[str, Any], side: str, contract: dict[str, Any], gate: dict[str, Any] | None = None) -> list[dict[str, str]]:
    direction = "看涨" if side == "CALL" else "看跌"
    gate = gate or {}
    positives = gate.get("positives") or []
    missing = gate.get("missing") or []
    blockers = gate.get("blockers") or []
    reasons = [
        {
            "title": f"综合胜率 gate：{direction}需要基本面/催化/舆情同时确认",
            "detail": "；".join(positives[:2]) or "当前没有足够非技术面证据支持高置信期权。",
        },
        {
            "title": "合约选择：必须有真实链、窄价差和足够 OI",
            "detail": (
                f"{contract.get('expiration')} / {contract.get('strike')} {side}，"
                f"单张成本约 ${contract.get('contractCostUsd')}；"
                f"{contract.get('priceSourceZh')}。"
            ),
        },
    ]
    if blockers:
        reasons.append({"title": "阻断项：置信达标也不能推进", "detail": "；".join(blockers)})
    elif row.get("latestNewsTitle"):
        reasons.append({"title": "催化/新闻：有近期事件驱动", "detail": str(row.get("latestNewsTitle"))[:150]})
    else:
        reasons.append({"title": "缺口：需要补齐后再判断", "detail": "；".join(missing) or "没有近端强催化，必须等开盘价格确认。"})
    return reasons[:3]


def _option_technical_mode(row: dict[str, Any], side: str) -> str:
    day = _money(row.get("dayChangePct"))
    momentum = _money(row.get("momentum30dPct"))
    price_to_ma50 = _money(row.get("priceToMa50Pct"))
    above_ma50 = bool(row.get("aboveMa50"))
    if side == "CALL":
        if above_ma50 and momentum >= 8 and 0 <= price_to_ma50 <= 20 and day >= 0:
            return "bullish_breakout_only"
        if above_ma50 and momentum >= 0 and price_to_ma50 <= 25:
            return "bullish_pullback_watch"
        if day > 8 or price_to_ma50 > 30:
            return "overextended_no_chase"
        return "wait_or_mixed"
    if not above_ma50 and momentum <= -6 and day <= 0:
        return "bearish_breakdown_only"
    if day < -4 and momentum < 0:
        return "bearish_retest_watch"
    return "wait_or_mixed"


def _option_agent_entry(side: str, technical_mode: str) -> str:
    if side == "CALL":
        if technical_mode == "overextended_no_chase":
            return "不追高；只在回踩 VWAP/前一日关键位不破后，且期权价差收窄时才重新评估。"
        return "开盘后 15-30 分钟标的站稳 VWAP，期权 bid/ask spread < 25%，且 QQQ/相关板块没有转弱。"
    return "开盘后 15-30 分钟标的跌破 VWAP 或反弹失败，期权 bid/ask spread < 25%，且大盘没有快速修复。"


def _option_agent_invalidation(side: str) -> str:
    if side == "CALL":
        return "标的跌回 VWAP/前一日低点下方，或期权价较入场价回撤 35%-45%，立即取消/止损。"
    return "标的重新站回 VWAP，或期权价较入场价回撤 35%-45%，立即取消/止损。"


def _option_agent_scan_card(
    row: dict[str, Any],
    side: str,
    contract: dict[str, Any],
    gate: dict[str, Any],
    score: float,
) -> dict[str, Any]:
    technical_mode = _option_technical_mode(row, side)
    blockers = gate.get("blockers", []) or []
    missing = gate.get("missing", []) or []
    positives = gate.get("positives", []) or []
    side_zh = "Call" if side == "CALL" else "Put"
    if gate.get("actionable"):
        conclusion = f"{row.get('symbol')} {side_zh} 已过 80% gate，可进入 paper playbook 候选。"
    elif blockers:
        conclusion = f"{row.get('symbol')} {side_zh} 暂不推进；主要阻断：{blockers[0]}。"
    elif missing:
        conclusion = f"{row.get('symbol')} {side_zh} 暂不推进；需要补齐：{missing[0]}。"
    else:
        conclusion = f"{row.get('symbol')} {side_zh} 接近候选，但仍需开盘价格和期权链确认。"
    return {
        "symbol": str(row.get("symbol") or "").upper(),
        "side": side,
        "sideZh": side_zh,
        "score": score,
        "confidencePct": gate.get("confidencePct"),
        "gateZh": gate.get("gateZh"),
        "technicalMode": technical_mode,
        "underlyingLast": row.get("last"),
        "dayChangePct": row.get("dayChangePct"),
        "priceToMa50Pct": row.get("priceToMa50Pct"),
        "contract": contract,
        "suggestedLimitPrice": contract.get("selectedPrice"),
        "maxLossUsd": contract.get("contractCostUsd"),
        "entryTriggerZh": _option_agent_entry(side, technical_mode),
        "invalidationZh": _option_agent_invalidation(side),
        "scanConclusionZh": conclusion,
        "positivesZh": positives[:4],
        "blockerFlagsZh": blockers[:5],
        "missingDataZh": missing[:5],
        "topReasons": _daily_option_reasons(row, side, contract, gate),
        "dataQuality": contract.get("dataQuality"),
        "dataQualityZh": "真实链" if contract.get("dataQuality") == "REAL_CHAIN" else "估算/需刷新",
    }


def _daily_option_pitches(
    state: dict[str, Any],
    watchlist: dict[str, Any],
    options: dict[str, Any],
    earnings: dict[str, Any],
    cash_pct: float,
    committee: dict[str, Any],
) -> dict[str, Any]:
    macro = str(state.get("macro_regime") or "UNKNOWN")
    committee_decision = committee.get("decision") or "UNKNOWN"
    committee_at = committee.get("timestamp")
    blocked = {str(symbol).upper() for symbol in earnings.get("blocked_option_symbols", []) or []}
    earnings_source_gap = bool(earnings.get("errors")) or (earnings.get("status") in {"FAIL", "MISSING"})
    symbols_with_real_chain = {str(symbol).upper() for symbol in (options.get("symbols") or {}).keys()}
    rows: list[tuple[float, dict[str, Any], str, dict[str, Any], dict[str, Any]]] = []
    watch_only: list[dict[str, Any]] = []

    for row in watchlist.get("rows", []):
        symbol = str(row.get("symbol") or "").upper()
        if not symbol or symbol in OPTION_PITCH_EXCLUDE or "." in symbol or symbol in blocked:
            continue
        if symbol not in OPTION_PITCH_SYMBOLS and symbol not in symbols_with_real_chain:
            continue
        spot = _to_float(row.get("last"))
        if spot is None or spot <= 0:
            continue
        for side in ("CALL", "PUT"):
            score = _option_direction_score(row, side)
            if score < 42:
                continue
            contract = _select_option_contract(options, symbol, side, spot)
            if contract is None:
                contract = _estimated_option_contract(symbol, side, spot, _money(row.get("momentum30dPct")))
                score -= 7.0
            contract_cost = _money(contract.get("contractCostUsd"))
            if contract.get("dataQuality") != "REAL_CHAIN" and contract_cost > 1500:
                continue
            if contract_cost > 900:
                score -= min(16.0, (contract_cost - 900) / 120)
            if macro == "HAWKISH":
                score -= 4.0
            gate = _option_quality_gate(row, side, contract, macro, earnings_source_gap)
            adjusted_score = round((score * 0.45) + (_money(gate.get("confidencePct")) * 0.55), 2)
            if gate.get("actionable"):
                rows.append((adjusted_score, row, side, contract, gate))
            else:
                watch_only.append(_option_agent_scan_card(row, side, contract, gate, adjusted_score))

    rows.sort(key=lambda item: item[0], reverse=True)
    watch_only.sort(key=lambda item: item.get("score", 0), reverse=True)
    pitches = []
    for rank, (score, row, side, contract, gate) in enumerate(rows[:3], start=1):
        symbol = str(row.get("symbol") or "").upper()
        side_zh = "Call" if side == "CALL" else "Put"
        data_quality = contract.get("dataQuality")
        model_win_rate = gate.get("confidencePct")
        limit_price = contract.get("selectedPrice")
        action = (
            f"{symbol} {side_zh} 80%+ 高置信候选：只在触发条件满足后，用单张最大亏损约 "
            f"${contract.get('contractCostUsd')} 的 paper option 预算测试，不追价。"
        )
        if data_quality != "REAL_CHAIN":
            action += " 当前缺真实期权链，价格只是估算，必须刷新真实 bid/ask 后才能写入 order intent。"
        entry = (
            "开盘后 15-30 分钟标的站稳 VWAP，期权 bid/ask spread < 25%，且 QQQ/相关板块没有转弱。"
            if side == "CALL"
            else "开盘后 15-30 分钟标的跌破 VWAP 或反弹失败，期权 bid/ask spread < 25%，且大盘没有快速修复。"
        )
        invalidation = (
            "标的跌回 VWAP/前一日低点下方或期权价较入场价回撤 35%-45% 时取消/止损。"
            if side == "CALL"
            else "标的重新站回 VWAP 或期权价较入场价回撤 35%-45% 时取消/止损。"
        )
        pitches.append(
            {
                "rank": rank,
                "symbol": symbol,
                "side": side,
                "sideZh": side_zh,
                "score": score,
                "modelWinRatePct": _round(model_win_rate, 1),
                "group": row.get("group"),
                "underlyingLast": row.get("last"),
                "technicalMode": _option_technical_mode(row, side),
                "contract": contract,
                "suggestedLimitPrice": limit_price,
                "maxLossUsd": contract.get("contractCostUsd"),
                "actionZh": action,
                "entryTriggerZh": entry,
                "invalidationZh": invalidation,
                "topReasons": _daily_option_reasons(row, side, contract, gate),
                "optionGateZh": gate.get("gateZh"),
                "missingDataZh": gate.get("missing", []),
                "blockerFlagsZh": gate.get("blockers", []),
                "dataQuality": data_quality,
                "dataQualityZh": "真实链" if data_quality == "REAL_CHAIN" else "估算/需刷新",
                "committeeGateZh": f"投委状态：{committee_decision} / {committee_at or '未生成'}；期权必须再过财报/宏观/流动性 gate。",
                "riskNoteZh": "期权只允许 paper idea；必须人工批准，且不能突破最大亏损预算。",
            }
        )

    prompt_lines = [
        "我确认要基于今日 Top 3 option pitch 生成明天的 conditional paper option playbook。",
        "请保持 advisory-only / paper-trade only：只写本地 conditional-playbook / order intent 草案，不解锁交易，不提交券商订单，不读取或修改真实账户。",
        "",
        "候选如下：",
    ]
    for pitch in pitches:
        contract = pitch.get("contract", {})
        prompt_lines.append(
            f"- {pitch['symbol']} {pitch['sideZh']}：{contract.get('expiration')} / strike {contract.get('strike')}；"
            f"建议限价 ${pitch.get('suggestedLimitPrice')}；单张最大亏损约 ${pitch.get('maxLossUsd')}；"
            f"数据质量 {pitch.get('dataQualityZh')}；触发：{pitch['entryTriggerZh']}；失效：{pitch['invalidationZh']}。"
        )
    prompt_lines += [
        "",
        "若任一候选缺真实 option chain 或 bid/ask spread 过宽，请保持为 WATCH_ONLY，不要生成可执行 order intent。",
        "若可以生成 intent，必须写明 option_contract、limit_price、max_risk_pct、valid_after/valid_until、entry_trigger、invalidation。",
    ]

    return {
        "titleZh": "每日 Top 3 期权操作",
        "methodZh": "80%+ 高置信 gate：基本面/营收增长、估值与华尔街预期、近端新闻催化、市场舆情、财报事件、真实期权链流动性、技术确认必须同时通过；缺任一关键项就降级 WATCH_ONLY。",
        "policyZh": "期权天然高风险；这里不硬凑 Top 3。只有模型置信度 >=80 且无阻断项，才生成 paper 候选和条件 playbook。",
        "committeeGateZh": f"已接入投委 gate：{committee_decision}；宏观/财报/真实期权链/新闻催化/舆情任一关键失败就保持 WATCH_ONLY。",
        "optionsDataStatus": options.get("status") or "MISSING",
        "optionsDataTimestamp": options.get("timestamp"),
        "minConfidencePct": 80,
        "noCandidateZh": (
            "当前没有达到 80%+ 高置信门槛的期权操作；下方展示智能体扫描池，方便你看见最接近的候选、阻断项和触发条件。"
            if not pitches
            else ""
        ),
        "watchOnlyCandidates": watch_only[:8],
        "scannerCards": watch_only[:8],
        "pitches": pitches,
        "batchOptionPlaybookPromptZh": "\n".join(prompt_lines),
    }


def _investment_brief(
    state: dict[str, Any],
    watchlist: dict[str, Any],
    orders: list[dict[str, Any]],
    portfolio: dict[str, Any],
    total_worth: float,
    cash_pct: float,
    social: dict[str, Any],
    earnings: dict[str, Any],
    options: dict[str, Any],
    committee: dict[str, Any],
) -> dict[str, Any]:
    rows = watchlist.get("rows", [])
    positions = {str(row.get("symbol")).upper(): row for row in _paper_positions(portfolio)}
    order_by_symbol = {str(order.get("symbol")).upper(): order for order in orders if order.get("symbol")}
    qqq = _watch_row(rows, "QQQ")
    nvda = _watch_row(rows, "NVDA")
    amzn = _watch_row(rows, "AMZN")
    avgo = _watch_row(rows, "AVGO")
    amd = _watch_row(rows, "AMD")
    pltr = _watch_row(rows, "PLTR")
    crcl = _watch_row(rows, "CRCL")
    ebay = _watch_row(rows, "EBAY")
    gme = _watch_row(rows, "GME")

    active_exposure = max(0.0, 100 - cash_pct)
    pending_qqq = order_by_symbol.get("QQQ", {})
    macro = state.get("macro_regime") or "UNKNOWN"
    crowding = social.get("marketMood", {}).get("crowdingRisk") or "UNKNOWN"

    candidates: list[dict[str, Any]] = []
    if pending_qqq and "PAPER_FILLED" not in str(pending_qqq.get("status")):
        candidates.append(
            {
                "symbol": "QQQ",
                "stanceZh": "可行动：等待你批准",
                "actionZh": "批准已有 QQQ 10% paper starter，作为核心科技 ETF 首批敞口。",
                "targetWeightPct": pending_qqq.get("targetWeightPct"),
                "confidenceZh": "中高",
                "riskControlZh": "只在开盘后 15-30 分钟守住 VWAP 且没有 VIX/利率风险反转时执行；跌破前一交易日低点则取消。",
                "topReasons": _action_reasons(
                    qqq,
                    [
                        {"title": "现金过高", "detail": f"当前现金约 {cash_pct:.1f}%，组合需要核心 beta 起步，而不是继续零新增敞口。"},
                        {"title": "比单名更稳", "detail": f"宏观为 {macro}，优先 ETF 分散风险，而不是直接追高波动个股。"},
                    ],
                    include_news=False,
                ),
            }
        )

    if nvda:
        candidates.append(
            {
                "symbol": "NVDA",
                "stanceZh": "持有 / 条件加仓",
                "actionZh": "已有小仓可继续持有；若半导体板块开盘后仍强，可考虑把 paper 权重从约 3% 提到 5%。",
                "targetWeightPct": 5.0,
                "confidenceZh": "中",
                "riskControlZh": "不追开盘跳涨；若跌回 MA50 或半导体热力转弱，暂停加仓。",
                "topReasons": _action_reasons(
                    nvda,
                    [{"title": "已验证持仓", "detail": "本地 paper portfolio 已有 NVDA，优先管理已有赢家而不是开太多新名字。"}],
                ),
            }
        )

    avgo_or_amzn = avgo if _money(avgo.get("attentionScore")) >= _money(amzn.get("attentionScore")) else amzn
    if avgo_or_amzn:
        candidates.append(
            {
                "symbol": avgo_or_amzn.get("symbol"),
                "stanceZh": "候选首仓 / 回踩买",
                "actionZh": f"{avgo_or_amzn.get('symbol')} 趋势强，但不建议追高；如果回踩不破 VWAP/MA50，可考虑 2-3% paper starter。",
                "targetWeightPct": 3.0,
                "confidenceZh": "中",
                "riskControlZh": "只用股票 paper starter，不用裸期权；若新闻催化消退或大盘转弱，取消。",
                "topReasons": _action_reasons(avgo_or_amzn),
            }
        )

    for row, action, stance in [
        (amd, "AMD 财报窗口不新开 call/put；等财报后价格方向和成交量确认，再评估 2% 小仓。", "暂缓：财报后再定"),
        (pltr, "PLTR 估值/评级压力未消化，暂不主动建仓；若守住 MA50 且负面新闻被吸收，再复核。", "暂缓：估值争议"),
        (crcl, "CRCL 有政策催化和大涨，但单日波动过大；加入高优先级观察，不追涨建仓。", "观察：政策催化"),
        (ebay, "EBAY/GME 是并购事件交易，先等董事会回应和价差稳定；不作为核心仓位。", "观察：事件交易"),
        (gme, "GME 单日大跌且并购可行性不确定，不接飞刀。", "回避：事件风险"),
    ]:
        if row:
            candidates.append(
                {
                    "symbol": row.get("symbol"),
                    "stanceZh": stance,
                    "actionZh": action,
                    "targetWeightPct": None,
                    "confidenceZh": "中",
                    "riskControlZh": row.get("stanceZh") or "只观察，不触发自动交易。",
                    "topReasons": _action_reasons(row),
                }
            )

    daily_pitch = _daily_stock_pitches(state, watchlist, portfolio, cash_pct, committee)
    daily_discovery_pitch = _daily_discovery_pitches(watchlist, cash_pct, committee)
    daily_option_pitch = _daily_option_pitches(state, watchlist, options, earnings, cash_pct, committee)
    alpha_lens_workflow = watchlist.get("alphaLensWorkflow") or _alpha_lens_workflow_summary(rows)

    return {
        "headlineZh": "每天固定产出 Top 3 股票 pitch：先找最值得投的标的，再由你决定是否推进明日 playbook。",
        "summaryZh": (
            f"本地 paper 组合当前约 {active_exposure:.1f}% 已部署/非现金，现金约 {cash_pct:.1f}%。"
            f"高现金不是因为没有机会，而是因为宏观为 {macro}、社媒拥挤度 {crowding}，且 AMD/PLTR/CRCL/GME 这类事件波动很高。"
        ),
        "cashTakeawayZh": "目标不是继续躺现金，而是每天从 Top 3 pitch 里挑 1-3 个高质量候选，隔天用条件触发执行 paper trade。",
        "whyNoFullDeploymentZh": [
            "宏观仍偏鹰，不能因为账户现金多就一次性打满高估值科技股。",
            "AMD/PLTR 处在财报、估值和期权波动窗口，裸 call/put 的胜率不清晰。",
            "CRCL、GME/EBAY 是事件驱动，适合进雷达，不适合当成核心仓位直接追。",
        ],
        "dailyStockPitch": daily_pitch,
        "dailyDiscoveryPitch": daily_discovery_pitch,
        "alphaLensWorkflow": alpha_lens_workflow,
        "topStockPitches": daily_pitch.get("pitches", []),
        "topDiscoveryPitches": daily_discovery_pitch.get("pitches", []),
        "batchDiscoveryPlaybookPromptZh": daily_discovery_pitch.get("batchDiscoveryPlaybookPromptZh"),
        "batchPlaybookPromptZh": daily_pitch.get("batchPlaybookPromptZh"),
        "dailyOptionPitch": daily_option_pitch,
        "topOptionPitches": daily_option_pitch.get("pitches", []),
        "batchOptionPlaybookPromptZh": daily_option_pitch.get("batchOptionPlaybookPromptZh"),
        "actionCandidates": candidates[:8],
        "portfolioStats": {
            "totalWorth": round(total_worth, 2),
            "cashPct": round(cash_pct, 2),
            "activeExposurePct": round(active_exposure, 2),
            "positionSymbols": sorted(positions.keys()),
        },
    }


def _social_risk_level(social: dict[str, Any]) -> str:
    if social.get("status") != "PASS":
        return "WARN"
    risk = social.get("marketMood", {}).get("crowdingRisk")
    if risk == "HIGH":
        return "WARN"
    return "PASS"


def _money(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _load_paper_portfolio() -> dict[str, Any]:
    return _load(PAPER_PORTFOLIO_PATH, {})


def _paper_nav_history(portfolio: dict[str, Any], start_capital: float, latest_nav: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if PAPER_NAV_PATH.exists():
        try:
            for line in PAPER_NAV_PATH.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                nav = _round(item.get("nav"))
                if nav is not None:
                    rows.append({"time": item.get("timestamp") or "paper", "value": nav})
        except (json.JSONDecodeError, OSError):
            rows = []

    if not rows:
        for item in portfolio.get("nav_history", []):
            nav = _round(item.get("nav"))
            if nav is not None:
                rows.append({"time": item.get("timestamp") or "paper", "value": nav})

    if not rows:
        rows = [
            {"time": "start", "value": round(start_capital, 2)},
            {"time": "latest", "value": round(latest_nav, 2)},
        ]
    elif len(rows) == 1:
        rows.insert(0, {"time": "start", "value": round(start_capital, 2)})

    return rows[-80:]


def _paper_positions(portfolio: dict[str, Any]) -> list[dict[str, Any]]:
    positions = []
    for row in portfolio.get("positions", []):
        symbol = row.get("symbol")
        if not symbol:
            continue
        positions.append(
            {
                "symbol": symbol,
                "quantity": row.get("quantity"),
                "avgCost": _round(row.get("avgCost"), 4),
                "lastPrice": _round(row.get("lastPrice"), 4),
                "marketValue": _round(row.get("marketValue")),
                "costBasis": _round(row.get("costBasis")),
                "pnl": _round(row.get("pnl")),
                "pnlPct": _round(row.get("pnlPct")),
                "weightPct": _round(row.get("weightPct")),
                "priceSource": row.get("priceSource"),
                "sourceIntentIds": row.get("sourceIntentIds", []),
                "updatedAt": row.get("updatedAt"),
            }
        )
    return sorted(positions, key=lambda item: item.get("marketValue") or 0, reverse=True)


def _account_summary(state: dict[str, Any], orders: list[dict[str, Any]]) -> dict[str, Any]:
    portfolio = _load_paper_portfolio()
    start_capital = _money(state.get("start_capital"))
    realized = _money(portfolio.get("realized_pnl", state.get("total_realized_pnl")))
    unrealized = _money(portfolio.get("unrealized_pnl", state.get("total_unrealized_pnl")))
    total_worth = _money(portfolio.get("nav")) or (start_capital + realized + unrealized)
    cash = _money(portfolio.get("cash")) if portfolio else total_worth
    positions = _paper_positions(portfolio)
    open_orders = [
        order
        for order in orders
        if "PAPER_FILLED" not in str(order.get("status", "")) and "REJECTED" not in str(order.get("status", ""))
    ]
    staged_weight = sum(_money(order.get("targetWeightPct")) for order in open_orders) / 100
    staged_value = total_worth * staged_weight
    has_paper_portfolio = bool(portfolio)
    return {
        "source": "local_paper_portfolio" if has_paper_portfolio else "local_paper_state",
        "sourceLabelZh": "本地 paper portfolio；未读取券商账户" if has_paper_portfolio else "本地模拟账户；未读取券商账户",
        "totalWorth": round(total_worth, 2),
        "cashEstimate": round(cash, 2),
        "realizedPnl": round(realized, 2),
        "unrealizedPnl": round(unrealized, 2),
        "totalPnl": round(realized + unrealized, 2),
        "totalPnlPct": round(((realized + unrealized) / start_capital * 100), 2) if start_capital else 0,
        "stagedExposurePct": round(staged_weight * 100, 2),
        "stagedExposureValue": round(staged_value, 2),
        "positions": positions,
        "positionNoteZh": "持仓来自本地 paper fill engine；没有读取真实券商账户。" if positions else "当前没有已确认 paper 持仓，也没有读取真实券商持仓。",
        "navSeries": _paper_nav_history(portfolio, start_capital, total_worth),
        "navDataQuality": "PAPER_FILLED" if positions else "FLAT_UNTIL_FILLS",
        "navNoteZh": "NAV 由本地 paper portfolio 估算，基于最新只读行情 mark-to-market；仍不代表真实账户净值。" if positions else "NAV 曲线目前只有本地模拟本金和盈亏状态；接入 paper fill 后会变成模拟组合曲线。",
        "filledIntentIds": portfolio.get("filled_intent_ids", []),
        "paperPolicy": portfolio.get("policy", {}),
    }


def _market_refresh_status(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "openbb_15m_snapshot",
        "modeLabelZh": "OpenBB 任务刷新；不是券商实盘行情流",
        "dashboardPollSeconds": 60,
        "quoteRefreshCadenceZh": "盘中自动化每 15 分钟刷新 OpenBB market_snapshot、paper portfolio 和 dashboard_snapshot；页面每 60 秒重新读取最新快照。",
        "lastCheck": state.get("last_check"),
        "suggestedNextStepZh": "上线后建议用 Firebase Hosting + Firestore 做私有实时看板；GitHub Pages 只发布脱敏内容。",
    }


def _count_errors(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_count_errors(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_errors(item) for item in value)
    return 1 if value not in (None, "", [], {}) else 0


def _freshness_level(status: Any) -> str:
    text = str(status or "").upper()
    if not text or text in {"MISSING", "UNKNOWN", "FAIL", "FAILED"}:
        return "FAIL"
    if "WARN" in text or "STALE" in text or "PARTIAL" in text:
        return "WARN"
    if "PASS" in text or "READY" in text or "COMPLETED" in text:
        return "PASS"
    return "WARN"


def _freshness_card(
    key: str,
    title_zh: str,
    status: Any,
    updated_at: Any,
    primary_zh: str,
    detail_zh: str,
    source_zh: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "titleZh": title_zh,
        "status": status or "MISSING",
        "level": _freshness_level(status),
        "updatedAt": updated_at,
        "primaryZh": primary_zh,
        "detailZh": detail_zh,
        "sourceZh": source_zh,
    }


def _freshness_age_hours(value: Any) -> float | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600, 2)


def _data_freshness_payload(
    market: dict[str, Any],
    valuation: dict[str, Any],
    network: dict[str, Any],
    openbb_smoke: dict[str, Any],
    social: dict[str, Any],
    fund: dict[str, Any],
    congress: dict[str, Any],
    earnings: dict[str, Any],
    intel: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    watch_rows = market.get("watchSymbols", []) or []
    priced_rows = [row for row in watch_rows if _effective_last(row) is not None]
    valuation_rows = valuation.get("rows", []) or []
    if isinstance(valuation_rows, dict):
        valuation_rows = list(valuation_rows.values())
    valuation_pass = sum(1 for row in valuation_rows if row.get("dataQuality") == "PASS")
    valuation_requested = valuation.get("symbolsRequested") or len(valuation_rows)
    valuation_with_data = valuation.get("symbolsWithData") or valuation_pass
    social_items = len(social.get("topItems", []) or [])
    event_items = len(social.get("eventRadar", []) or [])
    fund_ok = fund.get("successful_source_count")
    fund_total = fund.get("source_count")
    congress_count = len(congress.get("signals", []) or [])
    earnings_count = len(earnings.get("events", []) or earnings.get("calendar", []) or [])
    option_symbols = options.get("symbols") or {}
    option_chain_count = len(option_symbols) if isinstance(option_symbols, dict) else 0
    market_errors = _count_errors(market.get("errors", {}))
    valuation_errors = _count_errors(valuation.get("errors", {}))
    network_checks = network.get("checks", []) or []
    failed_network = [row for row in network_checks if not row.get("ok")]
    social_age = social.get("sourceRun", {}).get("ageHours")
    if social_age is None:
        social_age = _freshness_age_hours(social.get("timestamp"))
    intel_age = _freshness_age_hours(intel.get("timestamp"))

    market_status = market.get("status") or "MISSING"
    if watch_rows and not priced_rows:
        market_status = "FAIL"
    elif market_errors:
        market_status = "WARN"

    valuation_status = valuation.get("status") or "MISSING"
    if valuation_requested and valuation_with_data and valuation_with_data < valuation_requested:
        valuation_status = "WARN" if valuation_status == "PASS" else valuation_status

    social_status = social.get("status") or "MISSING"
    if social_age is not None and social_age > 6 and social_status == "PASS":
        social_status = "STALE"

    intel_status = "PASS" if intel.get("highlights") else "MISSING"
    if intel_age is not None and intel_age > 6 and intel_status == "PASS":
        intel_status = "STALE"

    option_status = options.get("status") or "MISSING"
    option_age = _freshness_age_hours(options.get("timestamp"))
    if option_age is not None and option_age > 12 and option_status == "PASS":
        option_status = "STALE"

    cards = [
        _freshness_card(
            "market",
            "行情快照",
            market_status,
            market.get("timestamp"),
            f"{len(priced_rows)}/{len(watch_rows)} 个观察标的有价格",
            f"错误 {market_errors} 个；失败时会保留上一份有效 latest，不再把页面刷空。",
            market.get("source") or "OpenBB / Yahoo / public fallback",
        ),
        _freshness_card(
            "valuation",
            "估值与目标价",
            valuation_status,
            valuation.get("timestamp"),
            f"{valuation_with_data}/{valuation_requested} 个标的有 PE 或目标价",
            f"错误 {valuation_errors} 个；PE/Forward PE/华尔街目标价来自估值快照。",
            valuation.get("source") or "OpenBB yfinance fundamentals + estimates",
        ),
        _freshness_card(
            "network",
            "网络预检",
            network.get("status") or "MISSING",
            network.get("timestamp"),
            (
                f"Yahoo {'可用' if network.get('marketDataReady') else '不可用'} / "
                f"FRED {'可用' if network.get('macroDataReady') else '不可用'} / "
                f"Firebase {'可用' if network.get('firebaseReady') else '不可用'}"
            ),
            f"{len(failed_network)} 个网络检查失败；宏观源失败会单独标黄，不阻塞行情刷新。",
            "network_preflight",
        ),
        _freshness_card(
            "openbb",
            "OpenBB",
            openbb_smoke.get("status") or "MISSING",
            openbb_smoke.get("timestamp"),
            "import 可用" if openbb_smoke.get("diagnostics", {}).get("available") else "import/路径需检查",
            f"版本 {openbb_smoke.get('diagnostics', {}).get('version') or '未知'}；WARN 通常代表部分 provider 缺失。",
            "openbb_smoke",
        ),
        _freshness_card(
            "sentiment",
            "新闻与社媒",
            social_status,
            social.get("timestamp"),
            f"{social_items} 条重点来源 / {event_items} 条事件雷达",
            f"上游 intel 年龄 {social_age if social_age is not None else '未知'} 小时；X/XHS 需 OpenCLI 补充。",
            "social_sentiment_feed",
        ),
        _freshness_card(
            "fund_congress",
            "基金与议员",
            "PASS" if (fund_ok or congress_count) else "MISSING",
            fund.get("timestamp") or congress.get("timestamp"),
            f"基金源 {fund_ok or 0}/{fund_total or 0}；议员信号 {congress_count} 条",
            "基金/议员披露是低频滞后信号，只做灵感和确认，不单独触发交易。",
            "fund_holdings_tracker + congress_trades_tracker",
        ),
        _freshness_card(
            "events",
            "财报与事件",
            earnings.get("status") or ("PASS" if earnings_count else "MISSING"),
            earnings.get("timestamp"),
            f"{earnings_count} 条事件；期权黑名单 {len(earnings.get('blocked_option_symbols', []) or [])} 个",
            "财报窗口会限制 call/put 新开仓，除非 playbook 已明确最大亏损。",
            "earnings_event_risk",
        ),
        _freshness_card(
            "options",
            "期权链",
            option_status,
            options.get("timestamp"),
            f"{option_chain_count}/{len(options.get('symbolsRequested', []) or [])} 个标的有真实期权链",
            "Top 3 Option 优先用真实 bid/ask/mid；缺链时只显示估算价，不能直接执行。",
            options.get("source") or "Yahoo Finance optionChain REST",
        ),
        _freshness_card(
            "intel",
            "突发新闻",
            intel_status,
            intel.get("timestamp"),
            f"{len(intel.get('highlights', []) or [])} 条高亮",
            f"上游新闻年龄 {intel_age if intel_age is not None else '未知'} 小时；用于捕捉 CRCL/GME/INTC 这类异动。",
            "intel_monitor",
        ),
    ]

    return {
        "summaryZh": "数据源透明度：绿色可直接用，黄色可参考但要看缺口，红色不要据此下判断。",
        "cards": cards,
        "lastMarketAt": market.get("timestamp"),
        "lastValuationAt": valuation.get("timestamp"),
        "network": {
            "marketDataReady": bool(network.get("marketDataReady")),
            "macroDataReady": bool(network.get("macroDataReady")),
            "firebaseReady": bool(network.get("firebaseReady")),
        },
        "gaps": [card for card in cards if card["level"] != "PASS"],
    }


def _visualization_payload(
    state: dict[str, Any],
    health: dict[str, Any],
    committee: dict[str, Any],
    earnings: dict[str, Any],
    orders: list[dict[str, Any]],
    manager_ideas: list[dict[str, Any]],
    congress: dict[str, Any],
    market: dict[str, Any],
    intel: dict[str, Any],
    social: dict[str, Any],
    options: dict[str, Any],
    data_freshness: dict[str, Any],
) -> dict[str, Any]:
    portfolio = _load_paper_portfolio()
    positions = _paper_positions(portfolio)
    total_worth = _money(portfolio.get("nav")) or (
        _money(state.get("start_capital"))
        + _money(state.get("total_realized_pnl"))
        + _money(state.get("total_unrealized_pnl"))
    )
    filled_intent_ids = {str(item) for item in portfolio.get("filled_intent_ids", [])}
    committed_orders = [
        order
        for order in orders
        if "COMMITTED" in str(order.get("status", "")) and str(order.get("intentId")) not in filled_intent_ids
    ]
    review_orders = [
        order
        for order in orders
        if str(order.get("status", "")).startswith("STAGED") and "REJECTED" not in str(order.get("status", ""))
    ]
    filled_usd = sum(_money(position.get("marketValue")) for position in positions)
    filled_pct = (filled_usd / total_worth * 100) if total_worth else 0
    committed_pct = sum(_money(order.get("targetWeightPct")) for order in committed_orders)
    review_pct = sum(_money(order.get("targetWeightPct")) for order in review_orders)
    cash_pct = (
        _money(portfolio.get("cash")) / total_worth * 100
        if portfolio and total_worth
        else max(0, 100 - committed_pct - review_pct)
    )

    allocation = []
    if filled_pct > 0:
        allocation.append(
            {
                "label": "已成交 paper 持仓",
                "kind": "filled",
                "valuePct": round(filled_pct, 2),
                "valueUsd": round(filled_usd, 2),
                "tooltip": "已经由本地 paper fill engine 转成模拟持仓；没有提交券商订单。",
            }
        )
    if committed_pct > 0:
        allocation.append(
            {
                "label": "已批准待成交",
                "kind": "committed",
                "valuePct": round(committed_pct, 2),
                "valueUsd": round(total_worth * committed_pct / 100, 2),
                "tooltip": "已经被你批准并写入本地 advisory commit，但还未生成 paper fill。",
            }
        )
    if review_pct > 0 and not positions:
        allocation.append(
            {
                "label": "待复核模拟敞口",
                "kind": "review",
                "valuePct": round(review_pct, 2),
                "valueUsd": round(total_worth * review_pct / 100, 2),
                "tooltip": "还没有被你批准或拒绝的本地 paper intent。",
            }
        )
    allocation.append(
        {
            "label": "现金/未部署",
            "kind": "cash",
            "valuePct": round(cash_pct, 2),
            "valueUsd": round(total_worth * cash_pct / 100, 2),
            "tooltip": "按本地 paper portfolio 估算，未读取真实券商现金。",
        }
    )

    manager_scores = {row.get("symbol"): _money(row.get("score")) for row in manager_ideas}
    congress_scores = {row.get("symbol"): _money(row.get("net_score")) for row in congress.get("signals", [])}
    social_by_symbol = _social_symbol_map(social)
    social_scores = {symbol: _money(row.get("netSentiment")) for symbol, row in social_by_symbol.items()}
    blocked_options = set(earnings.get("blocked_option_symbols", []))
    symbols = sorted(
        {
            *(row.get("symbol") for row in manager_ideas[:12]),
            *(order.get("symbol") for order in orders),
            *(position.get("symbol") for position in positions),
            *blocked_options,
            *congress_scores.keys(),
            *social_scores.keys(),
        }
        - {None}
    )
    heatmap = []
    for symbol in symbols:
        order = next((item for item in orders if item.get("symbol") == symbol), {})
        target = _money(order.get("targetWeightPct"))
        event_risk = 1 if symbol in blocked_options else 0
        congress_score = congress_scores.get(symbol, 0)
        manager_score = manager_scores.get(symbol, 0)
        social_score = social_scores.get(symbol, 0)
        social_signal = social_by_symbol.get(symbol, {})
        intensity = min(
            1,
            (manager_score * 0.44)
            + (target / 20 * 0.28)
            + (event_risk * 0.13)
            + (abs(congress_score) * 0.06)
            + (abs(social_score) * 0.09),
        )
        heatmap.append(
            {
                "symbol": symbol,
                "managerScore": round(manager_score, 3),
                "targetWeightPct": round(target, 2),
                "decisionStatus": order.get("status") or "WATCH",
                "eventRisk": "HIGH" if event_risk else "NORMAL",
                "congressScore": round(congress_score, 3),
                "socialSentiment": round(social_score, 3),
                "socialCrowding": social_signal.get("crowdingRisk"),
                "intensity": round(intensity, 3),
                "tooltip": (
                    f"{symbol}: manager={manager_score:.3f}, target={target:.2f}%, "
                    f"event={'HIGH' if event_risk else 'NORMAL'}, congress={congress_score:.3f}, "
                    f"social={social_score:.3f}/{social_signal.get('crowdingRisk', 'n/a')}"
                ),
            }
        )
    heatmap.sort(key=lambda row: row["intensity"], reverse=True)

    risk_matrix = [
        {
            "name": "宏观",
            "status": state.get("macro_regime") or "UNKNOWN",
            "level": "WARN" if state.get("macro_regime") == "HAWKISH" else "PASS",
            "description": "偏鹰宏观环境限制总敞口，并关闭新期权风险。",
        },
        {
            "name": "健康检查",
            "status": health.get("status") or "UNKNOWN",
            "level": health.get("status") or "UNKNOWN",
            "description": "数据源、OpenD、JSON、待复核意图的综合状态。",
        },
        {
            "name": "财报期权",
            "status": f"{len(blocked_options)} blocked",
            "level": "WARN" if blocked_options else "PASS",
            "description": "处在财报窗口的标的禁止新开 call/put 类风险。",
        },
        {
            "name": "社媒舆情",
            "status": social.get("marketMood", {}).get("labelZh") or social.get("status", "MISSING"),
            "level": _social_risk_level(social),
            "description": (
                f"拥挤风险 {social.get('marketMood', {}).get('crowdingRisk', 'UNKNOWN')}；"
                "只作为置信度/风险 overlay，不能单独触发交易。"
            ),
        },
        {
            "name": "执行安全",
            "status": state.get("execution_mode") or "UNKNOWN",
            "level": "PASS" if not state.get("allow_order_placement") else "FAIL",
            "description": "当前只允许本地 advisory 记录，不允许券商订单。",
        },
    ]

    timeline = []
    for row in sorted(_earnings_events(earnings), key=lambda item: (item.get("daysUntil") is None, item.get("daysUntil") or 999)):
        timeline.append(
            {
                "symbol": row.get("symbol"),
                "date": row.get("date"),
                "daysUntil": row.get("daysUntil"),
                "riskLevel": row.get("riskLevel"),
                "label": f"{row.get('symbol')} 财报",
                "tooltip": f"{row.get('company')} / {row.get('date')} / {row.get('riskLevel')}",
            }
        )

    watchlist_coverage = _watchlist_coverage(market, earnings, manager_ideas, congress, social, intel, orders, positions)
    event_insight_sections = _event_insight_sections(social, intel)
    event_radar = _event_radar_with_implications(social, event_insight_sections)
    investment_brief = _investment_brief(state, watchlist_coverage, orders, portfolio, total_worth, cash_pct, social, earnings, options, committee)
    indices = [_normalize_market_row(row) for row in market.get("indices", [])]
    true_macro = [_normalize_market_row(row) for row in market.get("trueMacroSeries", [])]
    macro_proxies = [_normalize_market_row(row) for row in market.get("macroProxies", [])]
    sector_etfs = [_normalize_market_row(row) for row in market.get("sectorEtfs", [])]
    watch_sparklines = [_normalize_market_row(row) for row in market.get("watchSymbols", [])]
    valuation_gap_count = sum(
        1
        for row in watchlist_coverage.get("rows", [])
        if row.get("valuationDataQuality") != "PASS"
    )
    valuation_gaps = []
    if valuation_gap_count:
        valuation_gaps.append({
            "name": "估值/目标价",
            "status": f"{valuation_gap_count} 个标的缺 PE 或华尔街目标价",
            "currentProxy": "不使用替代估值",
            "nextStep": "运行 valuation_snapshot；如 Yahoo/OpenBB 暂时不可用，先在 valuation_overrides.json 补关键标的。",
        })

    return {
        "allocation": allocation,
        "symbolHeatmap": heatmap,
        "riskMatrix": risk_matrix,
        "eventTimeline": timeline,
        "marketStatus": market.get("status", "MISSING"),
        "marketCards": (indices + true_macro + macro_proxies),
        "indexCharts": indices,
        "sectorHeatmap": sector_etfs,
        "watchSparklines": watch_sparklines,
        "watchlistCoverage": watchlist_coverage,
        "missingMarketSeries": market.get("missingTrueSeries", []) + valuation_gaps,
        "dataFreshness": data_freshness,
        "investmentBrief": investment_brief,
        "strategyBacktest": _latest_strategy_compare(),
        "researchRadar": {
            "fundManagerTop": manager_ideas[:8],
            "congressSignals": _congress_signals(congress),
            "newsHighlights": intel.get("highlights", [])[:6],
            "newsStatus": "PASS" if intel.get("highlights") else "MISSING_OR_NOT_RUN",
            "socialStatus": social.get("status", "MISSING"),
            "socialMood": social.get("marketMood", {}),
            "socialSymbols": social.get("symbolSignals", [])[:8],
            "socialThemes": social.get("themeSignals", [])[:6],
            "socialTopItems": social.get("topItems", [])[:6],
            "socialDisagreements": social.get("disagreementRisks", [])[:6],
            "eventRadar": event_radar,
            "eventInsightSections": event_insight_sections,
        },
        "recommendedWidgets": [
            "账户总览: KPI card + NAV line chart + allocation stacked bar",
            "组合持仓: table + hover risk detail + sector/position heatmap",
            "市场脉冲: index cards + breadth heatmap + VIX/rates/FX trend chart",
            "事件风险: earnings/FOMC timeline + option blackout badges",
            "AI 决策: decision queue + guard checklist + one-click prompt generation",
            "研究来源: manager consensus heatmap + congress lag signal cards",
        ],
    }


def build_dashboard_snapshot() -> dict[str, Any]:
    state = _load(ROOT / "state.json", {})
    macro = _load(ROOT / "macro-regime.json", {})
    health = _load(ROOT / "data" / "health" / "latest.json", {})
    committee = _load(ROOT / "data" / "research_committee" / "latest.json", {})
    staged = _load(ROOT / "data" / "trading" / "staged-orders.json", {"orders": []})
    earnings = _load(ROOT / "data" / "events" / "earnings_latest.json", {})
    fund = _load(ROOT / "data" / "fund_holdings" / "latest.json", {})
    congress = _load(ROOT / "data" / "congress_trades" / "latest.json", {})
    market = _load(ROOT / "data" / "market" / "latest.json", {})
    valuation = _load(ROOT / "data" / "market" / "valuation_latest.json", {})
    network_preflight = _load(ROOT / "data" / "health" / "network_preflight_latest.json", {})
    openbb_smoke = _load(ROOT / "data" / "openbb" / "latest_smoke.json", {})
    intel = _latest_json(ROOT / "data" / "intelligence", "*_intel.json")
    social = _load(ROOT / "data" / "social_sentiment" / "latest.json", {})
    options = _load(ROOT / "data" / "options" / "options_latest.json", {})
    conditional_playbook = _load(CONDITIONAL_PLAYBOOK_PATH, {})

    orders = _staged_orders(staged)
    _enrich_order_reasons(orders, fund, social, earnings, market)
    manager_ideas = _manager_ideas(fund)
    idea_symbols = {idea["symbol"] for idea in manager_ideas[:10]} | {order["symbol"] for order in orders if order.get("symbol")}
    holding_sources = _top_holding_sources(fund, idea_symbols)
    blocked_options = earnings.get("blocked_option_symbols", [])
    health_status = health.get("status", "UNKNOWN")
    committee_decision = committee.get("decision", "UNKNOWN")
    data_freshness = _data_freshness_payload(
        market,
        valuation,
        network_preflight,
        openbb_smoke,
        social,
        fund,
        congress,
        earnings,
        intel,
        options,
    )
    event_insight_sections = _event_insight_sections(social, intel)
    event_radar = _event_radar_with_implications(social, event_insight_sections)

    next_actions = [
        "美股开盘前复核已暂存的股票/ETF启动组合；所有项目仍然只是 paper trade 建议。",
        "AMD 和 PLTR 处在财报风险窗口，除非写出明确最大亏损的模拟 thesis，否则不要新开期权风险。",
        "基金经理和议员披露只作为灵感覆盖；任何 idea 都必须再通过确定性风控管线。",
        "社媒舆情只用于判断叙事变化和拥挤风险；不能单独生成买卖或期权动作。",
    ]
    if health_status == "WARN":
        next_actions.append("当前健康检查为 WARN，使用期权提示前需要人工核验财报日历来源。")
    if state.get("allow_order_placement"):
        next_actions.append("检测到下单开关异常打开；执行任何可成交流程前必须重新确认安全状态。")

    snapshot = {
        "generatedAt": now_iso(),
        "source": "local-files",
        "account": {
            "mode": state.get("mode"),
            "executionMode": state.get("execution_mode"),
            "allowOrderPlacement": state.get("allow_order_placement"),
            "startCapital": state.get("start_capital"),
            "realizedPnl": state.get("total_realized_pnl"),
            "unrealizedPnl": state.get("total_unrealized_pnl"),
            "summary": _account_summary(state, orders),
        },
        "market": {
            "state": state.get("market_state"),
            "macroRegime": state.get("macro_regime"),
            "fedFundsTargetRange": macro.get("current", {}).get("fed_funds_target_range"),
            "macroNotes": macro.get("current", {}).get("notes"),
            "refresh": _market_refresh_status(state),
            "openbb": {
                "preferred": True,
                "available": openbb_smoke.get("diagnostics", {}).get("available"),
                "status": openbb_smoke.get("status"),
                "version": openbb_smoke.get("diagnostics", {}).get("version"),
                "error": openbb_smoke.get("diagnostics", {}).get("error"),
                "lastSmokeAt": openbb_smoke.get("timestamp"),
                "fallbackActive": not bool(openbb_smoke.get("diagnostics", {}).get("available")),
            },
        },
        "health": {
            "status": health_status,
            "sourceChecks": health.get("source_checks", []),
            "openReviewIntents": health.get("open_review_intents", []),
            "dataFreshness": data_freshness,
        },
        "committee": {
            "decision": committee_decision,
            "agents": committee.get("agents", []),
        },
        "decisions": {
            "ready": committee_decision == "PAPER_REVIEW_READY" and health_status != "FAIL",
            "stagedOrderCount": len(orders),
            "blockedOptionSymbols": blocked_options,
            "nextActions": next_actions,
        },
        "conditionalPlaybook": _conditional_playbook_summary(conditional_playbook),
        "orders": orders,
        "earnings": {
            "timestamp": earnings.get("timestamp"),
            "events": _earnings_events(earnings),
            "blockedOptionSymbols": blocked_options,
            "errors": earnings.get("errors", {}),
        },
        "options": {
            "timestamp": options.get("timestamp"),
            "status": options.get("status", "MISSING"),
            "symbolsWithChains": options.get("symbolsWithChains"),
            "symbolsRequested": options.get("symbolsRequested", []),
            "errors": options.get("errors", {}),
        },
        "fundManagers": {
            "timestamp": fund.get("timestamp"),
            "sourceCount": fund.get("source_count"),
            "successfulSourceCount": fund.get("successful_source_count"),
            "managerIdeas": manager_ideas,
            "holdingSources": holding_sources,
            "assumptions": fund.get("assumptions", {}),
        },
        "congress": {
            "timestamp": congress.get("timestamp"),
            "signals": _congress_signals(congress),
            "assumptions": congress.get("assumptions", {}),
        },
        "socialSentiment": {
            "timestamp": social.get("timestamp"),
            "status": social.get("status", "MISSING"),
            "marketMood": social.get("marketMood", {}),
            "sentimentSummary": social.get("sentimentSummary", {}),
            "symbolSignals": social.get("symbolSignals", []),
            "themeSignals": social.get("themeSignals", []),
            "disagreementRisks": social.get("disagreementRisks", []),
            "catalystWatch": social.get("catalystWatch", []),
            "eventRadar": event_radar,
            "topItems": social.get("topItems", []),
            "sourceRun": social.get("sourceRun", {}),
            "judgementModel": social.get("judgementModel", {}),
            "dashboard": social.get("dashboard", {}),
        },
        "audit": _recent_trade_log(),
        "dataFreshness": data_freshness,
        "visualizations": _visualization_payload(
            state,
            health,
            committee,
            earnings,
            orders,
            manager_ideas,
            congress,
            market,
            intel,
            social,
            options,
            data_freshness,
        ),
    }
    return snapshot


def write_snapshot() -> Path:
    snapshot = json_safe(build_dashboard_snapshot())
    write_json(SNAPSHOT_PATH, snapshot)
    SNAPSHOT_JS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_JS_PATH.write_text(
        "window.__AGENTIC_DASHBOARD_SNAPSHOT__ = "
        + json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False)
        + ";\n",
        encoding="utf-8",
    )
    return SNAPSHOT_PATH


def main() -> int:
    path = write_snapshot()
    print(json.dumps({"task": "dashboard_snapshot", "status": "completed", "snapshot": str(path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
