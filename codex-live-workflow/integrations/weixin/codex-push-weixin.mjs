import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { pathToFileURL } from "node:url";
import { sendWeixinText, workspace } from "./weixin-send.mjs";

const bridgeDir = path.join(os.homedir(), ".codex", "weixin-bridge");
fs.mkdirSync(bridgeDir, { recursive: true });

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf8");
}

function stripAnsi(text) {
  return String(text || "").replace(/\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, "");
}

function compactDiagnostic(text) {
  return stripAnsi(text)
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&amp;/gi, "&")
    .replace(/\s+/g, " ")
    .trim();
}

function summarizeCodexFailure({ code, stderr, stdout, timedOut }) {
  const raw = stripAnsi(`${stderr || ""}\n${stdout || ""}`);

  if (timedOut || /Codex timed out/i.test(raw)) {
    return "Codex 定时任务超时了：这次任务超过 20 分钟还没有产出结果，我已停止等待。";
  }

  if (/backend-api\/plugins\/list|startup remote plugin sync failed|403 Forbidden|<html\b|Cloudflare/i.test(raw)) {
    return "Codex 定时任务没有产出结果；启动时插件同步被网络或 Cloudflare 拦截。我已隐藏原始网页日志，推送脚本现在会跳过插件同步。";
  }

  const compact = compactDiagnostic(raw).slice(0, 500);
  return `Codex 定时任务失败：${compact || `退出码 ${code}`}`;
}

function runCodex(prompt) {
  return new Promise((resolve) => {
    const outFile = path.join(bridgeDir, `codex-push-${Date.now()}.txt`);
    const child = spawn("codex.exe", [
      "--disable",
      "plugins",
      "--cd",
      workspace,
      "--sandbox",
      "workspace-write",
      "-a",
      "never",
      "exec",
      "--skip-git-repo-check",
      "--output-last-message",
      outFile,
      prompt,
    ], {
      cwd: workspace,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      stderr += "\nCodex timed out after 20 minutes.";
      child.kill();
    }, 20 * 60_000);

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });

    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });

    child.on("close", (code) => {
      clearTimeout(timeout);

      let answer = "";
      try {
        answer = fs.readFileSync(outFile, "utf8").trim();
      } catch {
        answer = "";
      }

      if (!answer) {
        answer = code === 0
          ? "Codex 没有返回可推送的内容。"
          : summarizeCodexFailure({ code, stderr, stdout, timedOut });
      }

      resolve(answer.slice(0, 3500));
    });
  });
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const prompt = (process.argv.slice(2).join(" ") || (process.stdin.isTTY ? "" : await readStdin())).trim();
  if (!prompt) {
    throw new Error("Usage: node codex-push-weixin.mjs <codex prompt>");
  }

  const answer = await runCodex(prompt);
  const to = await sendWeixinText(answer);
  process.stdout.write(`Codex result sent to ${to}\n`);
}
