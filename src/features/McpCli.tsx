/// <reference types="vite/client" />
import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Copy, RefreshCw } from "lucide-react";
import { Button, Card, Input, SectionTitle, SegmentGroup, Select } from "../components/ui";

const localHosts = ["localhost", "127.0.0.1", "[::1]"];
const local = localHosts.includes(window.location.hostname);
const endpoint = import.meta.env.DEV && local ? "http://127.0.0.1:8790" : window.location.origin;
const muted = "text-[13px] text-[var(--mute)]";

async function checkAccess({ signal }: { signal: AbortSignal }) {
  const response = await fetch("/api/integrations/manifest", {
    credentials: "omit",
    cache: "no-store",
    signal: AbortSignal.any([signal, AbortSignal.timeout(8000)]),
  });
  if (response.status === 401) return "protected";
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const manifest = await response.json();
  if (manifest.name !== "MonitorHub" || manifest.read_only !== true) throw new Error("无效的接口响应");
  return "open";
}

function CodeBlock({ title, code, ready = true }: { title: string; code: string; ready?: boolean }) {
  const content = useRef<HTMLElement>(null);
  const [feedback, setFeedback] = useState("");
  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setFeedback("已复制");
    } catch {
      const selection = window.getSelection();
      if (!selection || !content.current) {
        setFeedback("请手动选中并复制");
        return;
      }
      const range = document.createRange();
      range.selectNodeContents(content.current);
      selection.removeAllRanges();
      selection.addRange(range);
      try {
        if (document.execCommand("copy")) {
          selection.removeAllRanges();
          setFeedback("已复制");
          return;
        }
      } catch { /* Keep the selection for manual copying. */ }
      setFeedback("已选中，请手动复制");
    }
  }
  return (
    <div className="min-w-0 overflow-hidden rounded-md border border-[var(--hairline)] bg-[var(--bg-soft)]">
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 border-b border-[var(--hairline)]">
        <span className="text-[13px] font-medium">{title}</span>
        <div className="flex items-center gap-2">
          <span role="status" className={muted}>{feedback}</span>
          <Button size="sm" disabled={!ready} onClick={copy} aria-label={`复制${title}`}><Copy size={13} />复制</Button>
        </div>
      </div>
      <pre className="overflow-x-auto p-3 font-mono text-[12px] leading-5"><code ref={content}>{code}</code></pre>
    </div>
  );
}

type Client = "Codex" | "Claude Desktop" | "Claude Code";
function McpGuide({ needsCode }: { needsCode: boolean }) {
  const [client, setClient] = useState<Client>("Codex");
  const [directory, setDirectory] = useState("");
  const path = directory.trim().replace(/\\/g, "/");
  const ready = /^(?:[A-Za-z]:\/|\/)/.test(path);
  const script = (ready ? path.replace(/\/+$/, "") : "<项目绝对路径>") + "/tools/monitor_mcp.py";
  const args = JSON.stringify([script]);
  let config: string;
  if (client === "Codex") {
    config = `[mcp_servers.monitorhub]\ncommand = "python"\nargs = ${args}\n` +
      (needsCode ? 'env_vars = ["MONITOR_ACCESS_CODE"]\n' : "") +
      `\n[mcp_servers.monitorhub.env]\nMONITOR_BASE_URL = ${JSON.stringify(endpoint)}` + (needsCode ? "" : '\nMONITOR_ACCESS_CODE = ""');
  } else {
    const env: Record<string, string> = { MONITOR_BASE_URL: endpoint };
    env.MONITOR_ACCESS_CODE = needsCode ? (client === "Claude Code" ? "${MONITOR_ACCESS_CODE}" : "<在本机填写访问口令>") : "";
    config = JSON.stringify({ mcpServers: { monitorhub: { command: "python", args: [script], env } } }, null, 2);
  }
  const destination = { Codex: "~/.codex/config.toml", "Claude Desktop": "claude_desktop_config.json", "Claude Code": "项目根目录的 .mcp.json" }[client];
  return (
    <div className="space-y-4">
      <Card className="space-y-4">
        <h3 className="font-semibold">配置 MCP 客户端</h3>
        <div className="flex flex-wrap items-end gap-3">
          <label className="grid gap-1.5 text-sm">客户端
            <Select aria-label="客户端" value={client} onChange={e => setClient(e.target.value as Client)}>
              {(["Codex", "Claude Desktop", "Claude Code"] as const).map(name => <option key={name}>{name}</option>)}
            </Select>
          </label>
          <label className="grid min-w-0 flex-auto gap-1.5 text-sm">本机 Monitor 项目目录
            <Input value={directory} onChange={e => setDirectory(e.target.value)} placeholder="包含 tools/monitor_mcp.py 的绝对路径" />
          </label>
        </div>
        {directory && !ready && <p className={muted}>请输入绝对路径，例如 C:/work/monitor 或 /home/me/monitor。</p>}
        <p className={muted}>将以下配置合并到 {destination}。如果客户端找不到 python，请把 command 改为 Python 解释器的绝对路径。</p>
        {needsCode && <p className={muted}>{client === "Claude Desktop" ? "在本机的客户端配置文件中替换口令占位符。" : "启动客户端前，在它的运行环境中设置 MONITOR_ACCESS_CODE，值为该实例的访问口令。"}</p>}
        <CodeBlock key={config} title={client + " 配置"} code={config} ready={ready} />
      </Card>
      <Card className="space-y-3">
        <h3 className="font-semibold">验证 MCP 查询</h3>
        <p className={muted}>重新加载客户端的 MCP 配置后，在新对话中发送下面的问题。返回品牌数和记录数就说明查询已接通。</p>
        <CodeBlock title="验证问题" code="请调用 monitor_overview，告诉我当前 Monitor 中有多少个品牌和多少条记录。" />
      </Card>
    </div>
  );
}

function CliGuide({ needsCode }: { needsCode: boolean }) {
  const [shell, setShell] = useState(navigator.userAgent.includes("Windows") ? "PowerShell" : "Bash");
  const setup = shell === "PowerShell"
    ? [`$env:MONITOR_BASE_URL = '${endpoint}'`, ...(needsCode ? ["$code = Read-Host 'Monitor 访问口令' -AsSecureString", "$env:MONITOR_ACCESS_CODE = [System.Net.NetworkCredential]::new('', $code).Password"] : ["Remove-Item Env:MONITOR_ACCESS_CODE -ErrorAction SilentlyContinue"])]
    : [`export MONITOR_BASE_URL='${endpoint}'`, ...(needsCode ? ["IFS= read -rs -p 'Monitor 访问口令: ' MONITOR_ACCESS_CODE", "echo", "export MONITOR_ACCESS_CODE"] : ["unset MONITOR_ACCESS_CODE"])];
  return (
    <Card className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-semibold">从终端查询</h3>
        <Select aria-label="终端类型" value={shell} onChange={e => setShell(e.target.value)}>
          <option>PowerShell</option><option>Bash</option>
        </Select>
      </div>
      <p className={muted}>在包含 tools/monitor_cli.py 的项目目录打开终端，依次执行以下命令。</p>
      <CodeBlock key={setup.join("\n")} title="配置当前终端" code={setup.join("\n")} />
      <CodeBlock title="读取概览" code="python -m tools.monitor_cli --json overview" />
      <CodeBlock title="搜索最近 7 天的记录" code="python -m tools.monitor_cli --json records --q pricing --days 7 --limit 20" />
    </Card>
  );
}

export default function McpCli() {
  const [mode, setMode] = useState<"mcp" | "cli">("mcp");
  const access = useQuery({ queryKey: ["monitor-integration-access"], queryFn: checkAccess, retry: false, networkMode: "always" });
  const [manualAuth, setManualAuth] = useState(true);
  const unknown = access.isError || !access.data;
  const needsCode = unknown ? manualAuth : access.data === "protected";
  const status = access.isFetching ? "正在检查只读 API…" : access.isError ? "无法确认访问方式，请检查后端后重试。" :
    needsCode ? "只读 API 需要访问口令。外部工具需要单独配置口令。" : "只读 API 可匿名访问。";
  return (
    <div className="mx-auto max-w-[900px] space-y-5">
      <SectionTitle title="MCP / CLI 接入" subtitle="使用本机的 Python 客户端查询当前 Monitor 实例中的品牌与记录。" />
      <Card className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-semibold">Monitor 服务地址</h3>
          <Button size="sm" disabled={access.isFetching} onClick={() => void access.refetch()}><RefreshCw size={13} />检查 API</Button>
        </div>
        <div className="font-mono break-all text-sm">{endpoint}</div>
        <p role="status" className={muted}>{status}</p>
        {access.isError && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={manualAuth} onChange={e => setManualAuth(e.target.checked)} />配置中使用访问口令</label>}
        {local && <p className={muted}>请在运行此实例的电脑上使用配置。本地服务未启动时，在项目目录运行 python -m server.app。</p>}
        {!local && needsCode && window.location.protocol === "http:" && <p className={muted}>当前地址使用 HTTP。在公网传输口令前，请通过 HTTPS 或受信任的隧道访问实例。</p>}
      </Card>
      <SegmentGroup value={mode} onChange={setMode} options={[{ value: "mcp", label: "MCP 客户端" }, { value: "cli", label: "命令行 CLI" }]} />
      {!access.isPending && (mode === "mcp" ? <McpGuide needsCode={needsCode} /> : <CliGuide needsCode={needsCode} />)}
    </div>
  );
}
