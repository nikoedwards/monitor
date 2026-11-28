/// <reference types="vite/client" />
import { useRef, useState } from "react";
import type { ReactNode } from "react";
import { Check, Copy, Download } from "lucide-react";
import { Button, Card, SectionTitle, Select } from "../components/ui";

const download = "https://raw.githubusercontent.com/nikoedwards/monitor/568576806e4d53e566a3d622a4627342723599b7/downloads/monitor-connect-v1.zip";
const clients = { codex: "Codex", cursor: "Cursor", claude: "Claude Code", desktop: "Claude Desktop", cli: "命令行 CLI" };
type Client = keyof typeof clients;
type System = "Windows" | "macOS" | "Linux";
const hint = "text-[13px] leading-6 text-[var(--mute)]";

function Step({ number, title, children }: { number: number; title: string; children: ReactNode }) {
  return (
    <li>
      <Card className="flex items-start gap-3 sm:gap-4">
        <span aria-hidden="true" className="grid size-7 shrink-0 place-items-center rounded-full bg-[var(--bg-soft-2)] text-sm font-semibold">{number}</span>
        <div className="min-w-0 flex-1 space-y-3">
          <h3 className="font-semibold leading-7"><span className="sr-only">第 {number} 步，</span>{title}</h3>
          {children}
        </div>
      </Card>
    </li>
  );
}

function Snippet({ value, label, config = false, ready = true }: { value: string; label: string; config?: boolean; ready?: boolean }) {
  const field = useRef<HTMLTextAreaElement>(null);
  const [result, setResult] = useState<"copied" | "manual" | null>(null);
  async function copy() {
    try {
      if (!navigator.clipboard?.writeText) throw new Error();
      await navigator.clipboard.writeText(value);
      setResult("copied");
    } catch {
      const input = field.current;
      if (!input) return;
      input.focus();
      input.select();
      input.setSelectionRange(0, value.length);
      try {
        if (document.execCommand("copy")) {
          setResult("copied");
          return;
        }
      } catch { /* Leave the full text selected for the keyboard or touch menu. */ }
      setResult("manual");
    }
  }
  return (
    <div className={config ? "rounded-lg bg-zinc-900 p-4 text-zinc-100" : "rounded-lg border border-[var(--hairline)] bg-[var(--bg-soft)] p-3"}>
      <textarea ref={field} aria-label={label} readOnly value={value} rows={config ? value.split("\n").length + 1 : 2} spellCheck={false}
        className="block w-full resize-y bg-transparent font-mono text-[13px] leading-5" />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button onClick={copy} size="sm" disabled={!ready}>{result === "copied" ? <Check size={14} /> : <Copy size={14} />}{`复制${label}`}</Button>
        <span role="status" className={config ? "text-sm text-zinc-300" : hint}>{result === "copied" ? "已复制" : result === "manual" ? "文字已选中。按 Ctrl+C（Mac 按 ⌘C），手机长按后选「复制」。" : ""}</span>
      </div>
    </div>
  );
}

const terminalInstructions: Record<System, string> = {
  Windows: "右键 ZIP 文件，选「全部解压缩」。打开能看到 monitor.cmd 的文件夹，在文件夹地址栏输入 powershell 并按回车。",
  macOS: "双击 ZIP 文件解压。在访达中右键包含 monitor.pyz 的文件夹，选「服务 → 新建位于文件夹位置的终端窗口」。",
  Linux: "解压 ZIP 文件，在包含 monitor.pyz 的文件夹内右键，选择「在终端中打开」。",
};

export default function McpCli() {
  const [client, setClient] = useState<Client>("codex");
  const [issued, setIssued] = useState<{ key: string | null; id: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [system, setSystem] = useState<System>(() => {
    if (navigator.userAgent.includes("Windows")) return "Windows";
    return navigator.userAgent.includes("Linux") ? "Linux" : "macOS";
  });
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
  const endpoint = import.meta.env.DEV && local ? "http://127.0.0.1:8790" : location.origin;
  const launcher = system === "Windows" ? ".\\monitor.cmd" : "python3 ./monitor.pyz";
  const cli = client === "cli";
  const remote = client === "codex" || client === "claude" || client === "cursor";
  const remoteUrl = new URL("/mcp", endpoint);
  if (!local) remoteUrl.protocol = "https:";
  const securePage = local || location.protocol === "https:";
  const token = issued ? issued.key : "YOUR_MONITOR_KEY";
  const headers = token ? { Authorization: `Bearer ${token}` } : undefined;
  const config = client === "codex"
    ? [`[mcp_servers.monitorhub]`, `url = "${remoteUrl.href}"`,
       ...(headers ? [`http_headers = { Authorization = "${headers.Authorization}" }`] : [])].join("\n")
    : JSON.stringify({ mcpServers: { monitorhub: { type: client === "claude" ? "http" : undefined, url: remoteUrl.href, headers } } }, null, 2);
  async function changeKey(revoke = false) {
    setError("");
    setBusy(true);
    try {
      const path = "/api/integrations/mcp-keys" + (revoke ? `/${issued?.id}` : "");
      const response = await fetch(path, { method: revoke ? "DELETE" : "POST", cache: "no-store" });
      if (response.status === 401) throw new Error("登录已过期，请刷新页面并重新登录。");
      if (!response.ok) throw new Error("操作失败，请重试。");
      setIssued(revoke ? null : await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "连接失败，请重试。");
    } finally {
      setBusy(false);
    }
  }
  const setup = `${launcher} connect ${client} "${endpoint}"`;
  const firstQuery = cli
    ? `${launcher} query "${endpoint}" --json overview`
    : "请调用刚接入的 Monitor 服务的 monitor_overview，告诉我有多少个品牌和多少条记录。";
  return (
    <div className="mx-auto max-w-[800px] space-y-4">
      <SectionTitle title="MCP / CLI 接入" subtitle="连接 Monitor，查询品牌和内容记录。" />
      <div className="flex flex-wrap items-end gap-3">
        <label className="grid gap-1 text-sm">我要在哪里查数据
          <Select aria-label="查询工具" value={client} onChange={e => setClient(e.target.value as Client)}>
            {Object.entries(clients).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
          </Select>
        </label>
        {!remote && <label className="grid gap-1 text-sm">我的电脑
          <Select aria-label="操作系统" value={system} onChange={e => setSystem(e.target.value as System)}>
            {(["Windows", "macOS", "Linux"] as const).map(os => <option key={os}>{os}</option>)}
          </Select>
        </label>}
      </div>
      {remote ? (
        <ol className="space-y-3">
          <Step number={1} title="生成 MCP 密钥">
            {securePage ? <>
              <p className={hint}>{issued
                ? issued.key ? "密钥已填入下面的配置。离开本页前请复制保存。" : "此实例已开放查询，可以直接复制配置。"
                : "生成一把只读密钥，用来查询 Monitor 数据。"}</p>
              {!issued && <Button variant="primary" disabled={busy} onClick={() => changeKey()}>{busy ? "生成中…" : "生成密钥"}</Button>}
              {issued?.id && <Button size="sm" disabled={busy} onClick={() => changeKey(true)}>撤销这把密钥</Button>}
              {error && <p role="alert" className="text-sm text-[var(--danger)]">{error}</p>}
            </> : <a className="underline" href={new URL(location.pathname, remoteUrl.origin).href}>打开 HTTPS 接入页生成密钥</a>}
          </Step>
          <Step number={2} title={`复制到 ${clients[client]} 配置`}>
            <p className={hint}>{client === "codex"
              ? "打开用户目录下的 .codex/config.toml，粘贴下面这段配置。已有 [mcp_servers.monitorhub] 时，替换原来的这一段。"
              : `打开${client === "cursor" ? "用户目录下的 .cursor/mcp.json" : "项目目录下的 .mcp.json"}。新文件可直接粘贴；已有配置时，将 monitorhub 条目加入 mcpServers。请将含密钥的文件保存在本机。`}</p>
            <Snippet key={config} value={config} label="MCP 配置" config ready={!!issued} />
          </Step>
          <Step number={3} title="向模型提问">
            <p className={hint}>保存配置，重新打开客户端，在新对话中发送下面的问题。</p>
            <Snippet value={firstQuery} label="验证问题" />
          </Step>
        </ol>
      ) : <>
      <ol className="space-y-3">
        <Step number={1} title="下载接入包，在解压后的文件夹打开终端">
          <a href={download} download="monitor-connect-v1.zip"
            className="inline-flex items-center gap-2 rounded-md bg-[var(--ink)] px-4 py-2 text-sm font-medium text-[var(--bg)]">
            <Download size={15} />下载接入包 (.zip)
          </a>
          <p className={hint}>{terminalInstructions[system]}</p>
        </Step>
        <Step number={2} title={`在终端连接${cli ? " Monitor" : " " + clients[client]}`}>
          <p className={hint}>把下面一行粘贴到刚打开的终端，按回车。如果提示输入口令，使用登录 Monitor 网站的口令，输完按回车。输入时屏幕会隐藏字符。</p>
          <Snippet key={setup} value={setup} label="连接命令" />
          <p className={hint}>看到「连接成功」和品牌、记录数量后，继续第 3 步。助手会将连接信息和访问口令保存在当前用户的 .monitorhub 文件夹。</p>
        </Step>
        <Step number={3} title={cli ? "在同一终端查询数据" : `回到 ${clients[client]}，发起第一次查询`}>
          <p className={hint}>{cli ? "粘贴下面的命令并按回车。" : `完全退出并重新打开 ${clients[client]}，新建对话，把下面的问题粘贴到聊天框并发送。`}</p>
          <Snippet key={firstQuery} value={firstQuery} label={cli ? "查询命令" : "验证问题"} />
          <p className={hint}>{cli ? "输出中 counts.brands 是品牌数，counts.records 是记录数。" : "成功时，回复会显示 Monitor 返回的品牌数和记录数，可与第 2 步的结果核对。"}</p>
        </Step>
      </ol>
      <details className="rounded-lg border border-[var(--hairline)] p-4 text-sm">
        <summary className="cursor-pointer font-medium">命令没有运行？</summary>
        <ul className={`mt-2 list-disc space-y-1 pl-5 ${hint}`}>
          <li>提示找不到 monitor.cmd 或 monitor.pyz 时，回到第 1 步，在解压后的文件夹打开终端。</li>
          <li>提示找不到 Python 时，安装 Python 3.9 或更高版本，重新打开终端再运行。</li>
          <li>口令错误时，重新执行第 2 步。当前连接地址为 <code className="break-all">{endpoint}</code>。</li>
        </ul>
      </details>
      </>}
    </div>
  );
}
