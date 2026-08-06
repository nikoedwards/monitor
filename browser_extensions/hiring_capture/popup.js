const serverInput = document.querySelector("#serverUrl");
const connectButton = document.querySelector("#connect");
const brandSelect = document.querySelector("#brand");
const captureButton = document.querySelector("#capture");
const statusBox = document.querySelector("#status");

function setStatus(message, tone = "") {
  statusBox.textContent = message;
  statusBox.className = tone;
}

function normalizeServer(value) {
  const url = new URL((value || "").trim());
  if (!/^https?:$/.test(url.protocol)) throw new Error("Monitor 地址必须使用 http 或 https。");
  return url.origin + url.pathname.replace(/\/$/, "");
}

async function ensureServerPermission(serverUrl) {
  const origin = `${new URL(serverUrl).origin}/*`;
  if (await chrome.permissions.contains({ origins: [origin] })) return;
  if (!(await chrome.permissions.request({ origins: [origin] }))) {
    throw new Error("需要允许扩展访问该 Monitor 地址，才能发送采集结果。");
  }
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function loadBrands() {
  const serverUrl = normalizeServer(serverInput.value);
  await ensureServerPermission(serverUrl);
  setStatus("正在连接 Monitor…");
  const response = await fetch(`${serverUrl}/api/brands`);
  if (!response.ok) throw new Error(`Monitor 连接失败：HTTP ${response.status}`);
  const data = await response.json();
  const brands = data.brands || [];
  brandSelect.innerHTML = brands.length
    ? brands.map((brand) => `<option value="${brand.id}">${escapeHtml(brand.name)}</option>`).join("")
    : '<option value="">Monitor 中还没有品牌</option>';
  brandSelect.disabled = !brands.length;
  captureButton.disabled = !brands.length;
  const saved = await chrome.storage.local.get(["brandId"]);
  if (saved.brandId && brands.some((brand) => brand.id === saved.brandId)) brandSelect.value = saved.brandId;
  await chrome.storage.local.set({ serverUrl });
  setStatus(brands.length ? `已连接，发现 ${brands.length} 个品牌。` : "已连接，但请先在 Monitor 中创建品牌。", brands.length ? "success" : "");
}

async function collectCurrentPage() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url) throw new Error("没有找到当前页面。");
  const host = new URL(tab.url).hostname.toLowerCase();
  if (!(host === "zhipin.com" || host.endsWith(".zhipin.com") || host === "linkedin.com" || host.endsWith(".linkedin.com"))) {
    throw new Error("请先打开 BOSS 直聘或 LinkedIn 的职位页面。");
  }
  const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractHiringPage });
  return result;
}

async function submitCapture() {
  if (!brandSelect.value) throw new Error("请选择归属品牌。");
  const serverUrl = normalizeServer(serverInput.value);
  await ensureServerPermission(serverUrl);
  setStatus("正在读取当前页面…");
  const capture = await collectCurrentPage();
  capture.brand_id = brandSelect.value;
  await chrome.storage.local.set({ serverUrl, brandId: brandSelect.value });
  setStatus(capture.page_status === "blocked" ? "页面处于登录/验证状态，正在记录原因…" : `识别到 ${capture.jobs.length} 个职位，正在入库…`);
  const response = await fetch(`${serverUrl}/api/hiring/browser-capture`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(capture),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `入库失败：HTTP ${response.status}`);
  if (data.status === "blocked") {
    setStatus("检测到登录或安全验证。请在页面中人工完成验证，页面恢复正常后再点一次采集。", "error");
    return;
  }
  setStatus(`采集完成：入库 ${data.captured} 个，变化 ${data.changed} 个，下线 ${data.closed} 个。`, data.captured ? "success" : "error");
}

function extractHiringPage() {
  const clean = (value, limit = 20000) => String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
  const firstText = (selectors, root = document) => {
    if (!root || typeof root.querySelector !== "function") return "";
    for (const selector of selectors) {
      const node = root.querySelector(selector);
      const value = clean(node?.innerText || node?.textContent || "");
      if (value) return value;
    }
    return "";
  };
  const host = location.hostname.toLowerCase();
  const platform = host === "zhipin.com" || host.endsWith(".zhipin.com") ? "boss" : "linkedin";
  const bodyText = clean(document.body?.innerText || "", 50000);
  const titleText = clean(document.title, 500);
  const blockedMarkers = platform === "boss"
    ? ["BOSS直聘注册登录", "boss直聘在线注册登录", "登录BOSS直聘", "安全验证", "访问验证", "完成验证", "验证码"]
    : ["Sign in", "Join LinkedIn", "Security verification", "Let’s do a quick security check", "登录领英"];
  const blocked = blockedMarkers.some((marker) => `${titleText}\n${bodyText}`.toLowerCase().includes(marker.toLowerCase()));
  const path = location.pathname.toLowerCase();
  const loginPath = platform === "boss"
    ? ["/web/user/", "/login", "/register", "/security-check", "/safe-check"].some((marker) => path.includes(marker))
    : ["/login", "/checkpoint/"].some((marker) => path.includes(marker));
  if (blocked || loginPath) {
    return {
      platform,
      source_url: location.href,
      source_title: titleText,
      page_status: "blocked",
      page_error: "当前标签页显示登录或安全验证页面。",
      jobs: [],
    };
  }

  const closedMarkers = ["职位已下线", "停止招聘", "职位不存在", "已结束", "已关闭", "No longer accepting applications"];
  const isOpen = !closedMarkers.some((marker) => bodyText.toLowerCase().includes(marker.toLowerCase()));
  const jobs = [];
  const seen = new Set();
  const addJob = (job) => {
    try {
      const parsed = new URL(job.url, location.href);
      parsed.search = "";
      parsed.hash = "";
      const url = parsed.toString();
      if (seen.has(url)) return;
      seen.add(url);
      jobs.push({
        ...job,
        url,
        title: clean(job.title, 500),
        city: clean(job.city, 200),
        department: clean(job.department, 300),
        jd_text: clean(job.jd_text, 20000),
      });
    } catch {
      // Ignore malformed page links.
    }
  };

  if (platform === "boss") {
    if (path.includes("/job_detail/")) {
      addJob({
        url: location.href,
        title: firstText(["h1", ".job-name", "[class*='job-name']"]),
        city: firstText([".location-address", ".job-address", "[class*='job-address']", "[class*='job-location']"]),
        department: firstText(["[class*='job-category']", "[class*='department']"]),
        jd_text: firstText([".job-sec-text", ".job-detail", ".job-detail-section", "[class*='job-detail']"]),
        is_open: isOpen,
        raw: { capture_type: "detail" },
      });
    } else {
      document.querySelectorAll("a[href*='/job_detail/']").forEach((anchor) => {
        const card = anchor.closest("li, .job-card-wrapper, [class*='job-card'], [class*='job-list']") || anchor.parentElement;
        addJob({
          url: anchor.href,
          title: firstText([".job-name", "[class*='job-name']", "h3"], card) || clean(anchor.textContent, 500),
          city: firstText([".job-area", "[class*='job-area']", "[class*='location']"], card),
          department: firstText(["[class*='job-category']", "[class*='department']"], card),
          jd_text: "",
          is_open: true,
          raw: { capture_type: "list", card_text: clean(card?.innerText, 2000) },
        });
      });
    }
  } else if (path.includes("/jobs/view/")) {
    addJob({
      url: location.href,
      title: firstText(["h1", ".job-details-jobs-unified-top-card__job-title", "[class*='job-title']"]),
      city: firstText([".job-details-jobs-unified-top-card__primary-description-container", "[class*='primary-description']"]),
      department: "",
      jd_text: firstText(["#job-details", ".jobs-description__content", "[class*='jobs-description']"]),
      is_open: isOpen,
      raw: { capture_type: "detail" },
    });
  } else {
    document.querySelectorAll("a[href*='/jobs/view/']").forEach((anchor) => {
      const card = anchor.closest("li, [class*='job-card'], [class*='jobs-search-results__list-item']") || anchor.parentElement;
      addJob({
        url: anchor.href,
        title: firstText(["[class*='job-card-list__title']", "[class*='job-title']", "strong"], card) || clean(anchor.textContent, 500),
        city: firstText(["[class*='job-card-container__metadata']", "[class*='job-location']"], card),
        department: "",
        jd_text: "",
        is_open: true,
        raw: { capture_type: "list", card_text: clean(card?.innerText, 2000) },
      });
    });
  }

  return {
    platform,
    source_url: location.href,
    source_title: titleText,
    page_status: "ok",
    page_error: "",
    jobs: jobs.slice(0, 200),
  };
}

connectButton.addEventListener("click", () => loadBrands().catch((error) => setStatus(error.message, "error")));
captureButton.addEventListener("click", () => submitCapture().catch((error) => setStatus(error.message, "error")));
brandSelect.addEventListener("change", () => chrome.storage.local.set({ brandId: brandSelect.value }));
chrome.storage.local.get(["serverUrl"]).then((saved) => { if (saved.serverUrl) serverInput.value = saved.serverUrl; });
