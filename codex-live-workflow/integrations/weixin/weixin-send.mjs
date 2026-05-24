import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const home = os.homedir();
const stateDir = path.join(home, ".openclaw", "openclaw-weixin");
const bridgeDir = path.join(home, ".codex", "weixin-bridge");
const latestTargetPath = path.join(bridgeDir, "latest-target.json");
const workspace = process.env.CODEX_WORKSPACE || path.join(home, "Documents", "Codex");
const pluginRoot = path.join(
  home,
  ".openclaw",
  "npm",
  "node_modules",
  "@tencent-weixin",
  "openclaw-weixin",
  "dist",
  "src",
);

function readJson(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf8");
}

function parseArgs(argv) {
  const args = [...argv];
  let to = "";
  const rest = [];
  while (args.length) {
    const item = args.shift();
    if (item === "--to") {
      to = args.shift() || "";
    } else {
      rest.push(item);
    }
  }
  return { to, text: rest.join(" ").trim() };
}

function resolveAccount() {
  const accounts = readJson(path.join(stateDir, "accounts.json"), []);
  const accountId = accounts[0];
  if (!accountId) {
    throw new Error("No Weixin account is logged in.");
  }

  const accountPath = path.join(stateDir, "accounts", `${accountId}.json`);
  const account = readJson(accountPath, null);
  if (!account?.token) {
    throw new Error(`Missing token in ${accountPath}`);
  }

  return {
    accountId,
    token: account.token,
    baseUrl: account.baseUrl || "https://ilinkai.weixin.qq.com",
    userId: account.userId || "",
  };
}

function resolveLatestTarget() {
  const latest = readJson(latestTargetPath, null);
  if (!latest?.to) {
    return null;
  }
  return {
    to: latest.to,
    contextToken: latest.contextToken || undefined,
  };
}

function resolveTarget(account, explicitTo) {
  const contextPath = path.join(stateDir, "accounts", `${account.accountId}.context-tokens.json`);
  const contexts = readJson(contextPath, {});
  if (explicitTo) {
    return { to: explicitTo, contextToken: contexts[explicitTo] };
  }

  const latestTarget = resolveLatestTarget();
  if (latestTarget) {
    return latestTarget;
  }

  const to = Object.keys(contexts)[0] || "";
  if (!to) {
    throw new Error("No recent Weixin target found. Send the bot a message once, or pass --to <user_id>.");
  }
  throw new Error(
    `No recent Weixin target found at ${latestTargetPath}. Send the bot a message once to refresh the reply context, or pass --to ${to}.`,
  );
}

export async function sendWeixinText(text, options = {}) {
  if (!text.trim()) {
    throw new Error("Message text is empty.");
  }

  const account = resolveAccount();
  const target = resolveTarget(account, options.to || "");
  const { sendMessageWeixin } = await import(pathToFileURL(path.join(pluginRoot, "messaging", "send.js")).href);

  await sendMessageWeixin({
    to: target.to,
    text,
    opts: {
      baseUrl: account.baseUrl,
      token: account.token,
      contextToken: target.contextToken,
      timeoutMs: 15_000,
    },
  });

  return target.to;
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const parsed = parseArgs(process.argv.slice(2));
  const stdin = process.stdin.isTTY ? "" : await readStdin();
  const text = (parsed.text || stdin).trim();
  const to = await sendWeixinText(text, { to: parsed.to });
  process.stdout.write(`Sent to ${to}\n`);
}

export { workspace };
