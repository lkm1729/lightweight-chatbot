/* Easy Chatbox 前端逻辑。
 *
 * 后端端点：协议清单 / 推理档位、配置读写、会话 CRUD、流式对话。SSE 用
 * fetch + ReadableStream 手动解析（而非 EventSource），因为对话是 POST 请求。
 *
 * 配置是三层结构：协议 → 供应商 → 模型。输入框右侧的模型抽屉把所有供应商的
 * 模型摊平成一张列表，选中某个模型即同时确定了协议、供应商与底层模型 ID。
 *
 * 会话存在后端 SQLite 里，前端只保留「当前会话」的消息副本用于拼上下文；
 * 每轮问答结束后把两条消息追加到后端，因此刷新或切换会话都不丢历史。
 */

const $ = (sel) => document.querySelector(sel);

// --- 全局状态 ---

const state = {
  providers: [],   // [{name, label}]
  efforts: [],     // [{key, label, label_en, boosted, note}]
  config: { version: 2, providers: {}, selection: {} },  // api_key 为掩码值
  selection: { provider: "", vendor: "", model: "", effort: "medium" },
  conversations: [],   // [{id, title, updated_at, message_count}]
  activeId: null,      // 当前会话 id
  messages: [],        // 当前会话的 [{role, content, attachments}]，只用于拼上下文
  attachments: [],     // 待发送附件的元数据 [{id, name, mime, size}]
  controller: null,    // 进行中请求的 AbortController
  search: false        // 联网搜索开关
};

function newId(prefix) {
  return `${prefix}_${Math.random().toString(16).slice(2, 12)}`;
}

// --- 通用请求 ---

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(data?.detail || `请求失败（${response.status}）`);
  }
  return data;
}

// --- 配置读取助手 ---

function vendorsOf(providerName) {
  return state.config.providers?.[providerName]?.vendors || [];
}

function providerLabel(name) {
  return state.providers.find((p) => p.name === name)?.label || name;
}

function vendorLabel(vendor) {
  return vendor.name || "未命名供应商";
}

/** 摊平成 [{provider, vendor, model}]，供模型抽屉与选中态校验使用。 */
function modelChoices() {
  const out = [];
  for (const p of state.providers) {
    for (const vendor of vendorsOf(p.name)) {
      for (const model of vendor.models || []) {
        out.push({ provider: p.name, vendor, model });
      }
    }
  }
  return out;
}

function currentChoice() {
  const { provider, vendor, model } = state.selection;
  return (
    modelChoices().find(
      (c) => c.provider === provider && c.vendor.id === vendor && c.model.id === model
    ) || null
  );
}

function currentEffort() {
  return state.efforts.find((e) => e.key === state.selection.effort) || state.efforts[0];
}

/** 选中态会因为改配置而失效（删掉了供应商或模型），这时回落到第一个可用模型。 */
function ensureSelection() {
  if (!state.efforts.some((e) => e.key === state.selection.effort)) {
    state.selection.effort = "medium";
  }
  if (currentChoice()) return;
  const first = modelChoices()[0];
  Object.assign(
    state.selection,
    first
      ? { provider: first.provider, vendor: first.vendor.id, model: first.model.id }
      : { provider: "", vendor: "", model: "" }
  );
}

// --- 初始化 ---

async function init() {
  bindEvents();
  try {
    [state.providers, state.efforts, state.config] = await Promise.all([
      api("/api/providers"),
      api("/api/providers/reasoning"),
      api("/api/providers/config")
    ]);
  } catch (err) {
    setStatus(`加载配置失败：${err.message}`, true);
    return;
  }
  Object.assign(state.selection, state.config.selection || {});
  ensureSelection();
  renderProviderForms();
  renderModelPicker();
  renderEffortPicker();
  await loadConversations();
}

function setStatus(text, isError = false) {
  const el = $("#status");
  el.textContent = text;
  el.classList.toggle("err", isError);
}

// --- 会话列表 ---

/** 拉会话列表；首次进来没有任何会话就自动建一个，省得用户对着空界面发懵。 */
async function loadConversations({ keepActive = true } = {}) {
  try {
    state.conversations = await api("/api/conversations");
  } catch (err) {
    setStatus(`加载对话列表失败：${err.message}`, true);
    return;
  }

  if (!state.conversations.length) {
    await createConversation();
    return;
  }
  const stillThere = state.conversations.some((c) => c.id === state.activeId);
  renderConversations();
  if (!keepActive || !stillThere) await openConversation(state.conversations[0].id);
}

function renderConversations() {
  const list = $("#conv-list");
  list.innerHTML = "";

  for (const conv of state.conversations) {
    const node = $("#tpl-conv-item").content.cloneNode(true);
    const item = node.querySelector(".conv-item");
    item.dataset.id = conv.id;
    item.classList.toggle("active", conv.id === state.activeId);

    const titleEl = item.querySelector(".ci-title");
    titleEl.textContent = conv.title;
    titleEl.title = `${conv.title}（${conv.message_count} 条消息）`;

    titleEl.addEventListener("click", () => openConversation(conv.id));
    item.querySelector(".ci-rename").addEventListener("click", (e) => {
      e.stopPropagation();
      startRename(item, conv);
    });
    item.querySelector(".ci-del").addEventListener("click", (e) => {
      e.stopPropagation();
      removeConversation(conv);
    });

    list.appendChild(node);
  }
  $("#conv-empty").classList.toggle("hidden", state.conversations.length > 0);
}

/** 就地把标题换成输入框；Enter / 失焦提交，Esc 放弃。 */
function startRename(item, conv) {
  const titleEl = item.querySelector(".ci-title");
  const input = item.querySelector(".ci-input");
  input.value = conv.title;
  titleEl.classList.add("hidden");
  input.classList.remove("hidden");

  let settled = false;
  // 只有真正拿到过焦点才认失焦提交：窗口本身没焦点时 focus() 会立刻回弹一个
  // blur，照单全收就会让输入框刚打开就消失
  let focused = false;

  const finish = async (commit) => {
    if (settled) return;
    settled = true;
    input.classList.add("hidden");
    titleEl.classList.remove("hidden");
    const next = input.value.trim();
    if (!commit || !next || next === conv.title) return;
    try {
      await api(`/api/conversations/${conv.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title: next })
      });
      conv.title = next;
      titleEl.textContent = next;
      if (conv.id === state.activeId) $("#conv-title").textContent = next;
    } catch (err) {
      setStatus(`重命名失败：${err.message}`, true);
    }
  };

  input.addEventListener("focus", () => {
    focused = true;
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      finish(true);
    } else if (e.key === "Escape") {
      e.preventDefault();
      finish(false);
    }
  });
  input.addEventListener("blur", () => {
    if (focused) finish(true);
  });

  input.focus();
  input.select();
}

async function createConversation() {
  try {
    const conv = await api("/api/conversations", {
      method: "POST",
      body: JSON.stringify({})
    });
    state.conversations.unshift(conv);
    state.activeId = conv.id;
    state.messages = [];
    renderConversations();
    renderMessages([]);
    $("#conv-title").textContent = conv.title;
    setStatus("");
  } catch (err) {
    setStatus(`新建对话失败：${err.message}`, true);
  }
}

// --- 确认框 ---

/** 应用内确认框，返回 Promise<boolean>。
 *
 * 不用 window.confirm：浏览器的「阻止此页面创建其他对话框」一旦勾上，之后每次
 * 调用都静默返回 false，删除按钮看着就像坏了；打包进 pywebview 之类的宿主时
 * JS 弹窗也可能整体被禁。自己画一个既能保证行为一致，也跟着暗色主题走。
 */
function confirmDialog(message, { okLabel = "删除" } = {}) {
  const modal = $("#confirm");
  const ok = $("#confirm-ok");
  const cancel = $("#confirm-cancel");
  $("#confirm-text").textContent = message;
  ok.textContent = okLabel;
  modal.classList.remove("hidden");

  return new Promise((resolve) => {
    const finish = (answer) => {
      modal.classList.add("hidden");
      ok.removeEventListener("click", onOk);
      cancel.removeEventListener("click", onCancel);
      modal.removeEventListener("click", onBackdrop);
      document.removeEventListener("keydown", onKey);
      resolve(answer);
    };
    const onOk = () => finish(true);
    const onCancel = () => finish(false);
    const onBackdrop = (e) => {
      if (e.target === modal) finish(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") finish(false);
      else if (e.key === "Enter") finish(true);
    };

    ok.addEventListener("click", onOk);
    cancel.addEventListener("click", onCancel);
    modal.addEventListener("click", onBackdrop);
    document.addEventListener("keydown", onKey);
    ok.focus();
  });
}

async function removeConversation(conv) {
  const yes = await confirmDialog(`删除「${conv.title}」？该对话的消息会一并删除。`);
  if (!yes) return;
  try {
    await api(`/api/conversations/${conv.id}`, { method: "DELETE" });
  } catch (err) {
    setStatus(`删除失败：${err.message}`, true);
    return;
  }
  // 删掉的正是当前会话时，让 loadConversations 重新挑一个打开
  if (conv.id === state.activeId) state.activeId = null;
  await loadConversations();
}

async function openConversation(id) {
  if (state.controller) return;  // 生成中先别切，免得流写进别的会话
  try {
    const detail = await api(`/api/conversations/${id}`);
    state.activeId = detail.id;
    // id 必须带上：编辑与重新生成靠它定位截断点，缺了就只截断后端与 DOM，
    // 内存里这份上下文会留着已删的尾巴，下一轮把幽灵消息发给模型
    state.messages = detail.messages.map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
      attachments: m.attachments || []
    }));
    $("#conv-title").textContent = detail.title;
    renderConversations();
    renderMessages(detail.messages);
    setStatus("");
  } catch (err) {
    setStatus(`打开对话失败：${err.message}`, true);
  }
}

/** 把会话挪到列表最前（后端按 updated_at 排序，前端跟上即可）。 */
function bumpActiveConversation() {
  const index = state.conversations.findIndex((c) => c.id === state.activeId);
  if (index < 0) return;
  const [conv] = state.conversations.splice(index, 1);
  conv.message_count += 1;
  state.conversations.unshift(conv);
  renderConversations();
}

/** 把一条消息落库；失败只提示，不影响已经渲染出来的内容。
 *
 * 返回落库后的消息（带 id）——编辑与重新生成要靠 id 定位截断点。
 */
async function persistMessage(role, content, thinking, attachments, origin) {
  if (!state.activeId || !content) return null;
  try {
    const stored = await api(`/api/conversations/${state.activeId}/messages`, {
      method: "POST",
      body: JSON.stringify({
        role,
        content,
        thinking: thinking || null,
        attachments: attachments || [],
        origin: origin || null
      })
    });
    bumpActiveConversation();
    return stored;
  } catch (err) {
    setStatus(`保存消息失败：${err.message}`, true);
    return null;
  }
}

// --- 附件 ---

const MAX_FILE_BYTES = 10 * 1024 * 1024;   // 与后端 attachments.MAX_FILE_BYTES 一致
const MAX_TOTAL_BYTES = 25 * 1024 * 1024;  // 与后端 attachments.MAX_TOTAL_BYTES 一致

function formatBytes(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function isImage(meta) {
  return (meta.mime || "").startsWith("image/") && meta.mime !== "image/svg+xml";
}

/** 读成不带 ``data:`` 前缀的 base64。 */
function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("读取文件失败"));
    reader.onload = () => {
      const result = String(reader.result);
      resolve(result.slice(result.indexOf(",") + 1));
    };
    reader.readAsDataURL(file);
  });
}

/** 选中的文件逐个上传；上传期间先摆一个灰着的占位 chip。 */
async function addFiles(files) {
  for (const file of files) {
    if (file.size > MAX_FILE_BYTES) {
      setStatus(`「${file.name}」超过 ${formatBytes(MAX_FILE_BYTES)}，已跳过`, true);
      continue;
    }
    const total = state.attachments.reduce((sum, a) => sum + (a.size || 0), 0);
    if (total + file.size > MAX_TOTAL_BYTES) {
      setStatus(`附件合计不能超过 ${formatBytes(MAX_TOTAL_BYTES)}，「${file.name}」已跳过`, true);
      continue;
    }

    // 占位项让用户马上看到反应，上传完再替换成真元数据
    const pending = { pending: true, name: file.name, mime: file.type, size: file.size };
    state.attachments.push(pending);
    renderAttachTray();

    try {
      const meta = await api("/api/attachments", {
        method: "POST",
        body: JSON.stringify({
          name: file.name,
          mime: file.type || "application/octet-stream",
          data: await readAsBase64(file)
        })
      });
      Object.assign(pending, meta, { pending: false });
      setStatus("");
    } catch (err) {
      state.attachments = state.attachments.filter((a) => a !== pending);
      setStatus(`上传「${file.name}」失败：${err.message}`, true);
    }
    renderAttachTray();
  }
}

function removeAttachment(meta) {
  state.attachments = state.attachments.filter((a) => a !== meta);
  renderAttachTray();
}

function clearAttachments() {
  state.attachments = [];
  renderAttachTray();
}

function renderAttachTray() {
  const tray = $("#attach-tray");
  tray.innerHTML = "";
  tray.classList.toggle("hidden", !state.attachments.length);

  for (const meta of state.attachments) {
    const chip = document.createElement("div");
    chip.className = meta.pending ? "att-chip uploading" : "att-chip";

    if (isImage(meta) && meta.id) {
      const img = document.createElement("img");
      img.className = "att-thumb";
      img.src = `/api/attachments/${meta.id}`;
      img.alt = meta.name;
      chip.appendChild(img);
    } else {
      const icon = document.createElement("span");
      icon.className = "att-icon";
      icon.textContent = isImage(meta) ? "🖼" : "📄";
      chip.appendChild(icon);
    }

    const box = document.createElement("span");
    box.className = "att-meta";
    const name = document.createElement("span");
    name.className = "att-name";
    name.textContent = meta.name;
    name.title = meta.name;
    const size = document.createElement("span");
    size.className = "att-size";
    size.textContent = meta.pending ? "上传中…" : formatBytes(meta.size || 0);
    box.append(name, size);
    chip.appendChild(box);

    const del = document.createElement("button");
    del.type = "button";
    del.className = "att-del";
    del.textContent = "✕";
    del.title = "移除";
    del.addEventListener("click", () => removeAttachment(meta));
    chip.appendChild(del);

    tray.appendChild(chip);
  }
}

/** 把一条消息的附件渲染进气泡：图片直接显示，其它给个可点开的文件卡片。 */
function renderMessageAttachments(container, items) {
  container.innerHTML = "";
  const list = items || [];
  container.classList.toggle("hidden", !list.length);

  for (const meta of list) {
    const href = `/api/attachments/${meta.id}`;
    if (isImage(meta)) {
      const link = document.createElement("a");
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener";
      const img = document.createElement("img");
      img.className = "msg-att-image";
      img.src = href;
      img.alt = meta.name;
      img.title = meta.name;
      link.appendChild(img);
      container.appendChild(link);
    } else {
      const link = document.createElement("a");
      link.className = "msg-att-file";
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = `📄 ${meta.name}（${formatBytes(meta.size || 0)}）`;
      container.appendChild(link);
    }
  }
}

// --- 模型抽屉（输入框右侧） ---

function renderModelPicker() {
  const choice = currentChoice();
  const label = choice
    ? choice.model.name
    : modelChoices().length
      ? "未选择模型"
      : "未配置模型";
  $("#model-name").textContent = label;
  $("#btn-model").title = choice
    ? `${choice.model.name} · ${vendorLabel(choice.vendor)} · ${providerLabel(choice.provider)}`
    : "选择模型";
  renderModelDrawer();
}

function renderModelDrawer() {
  const drawer = $("#model-drawer");
  drawer.innerHTML = "";

  const choices = modelChoices();
  if (!choices.length) {
    const empty = document.createElement("div");
    empty.className = "drawer-empty";
    empty.textContent = "还没有可用模型，请到「设置」里添加供应商与模型。";
    drawer.appendChild(empty);
    return;
  }

  let lastGroup = "";
  for (const c of choices) {
    const key = `${c.provider}/${c.vendor.id}`;
    if (key !== lastGroup) {
      lastGroup = key;
      const head = document.createElement("div");
      head.className = "drawer-group";
      head.textContent = `${vendorLabel(c.vendor)} · ${providerLabel(c.provider)}`;
      drawer.appendChild(head);
    }

    const item = document.createElement("button");
    item.type = "button";
    item.className = "drawer-item";
    item.setAttribute("role", "option");
    // 抽屉里只出现对外显示名，底层 ID 不暴露给使用者
    item.textContent = c.model.name;
    const active =
      c.provider === state.selection.provider &&
      c.vendor.id === state.selection.vendor &&
      c.model.id === state.selection.model;
    item.classList.toggle("active", active);
    item.setAttribute("aria-selected", String(active));
    item.addEventListener("click", () => {
      Object.assign(state.selection, {
        provider: c.provider,
        vendor: c.vendor.id,
        model: c.model.id
      });
      closeDrawers();
      renderModelPicker();
      setStatus("");
      persistSelection();
    });
    drawer.appendChild(item);
  }
}

// --- 推理强度抽屉 ---

function renderEffortPicker() {
  const effort = currentEffort();
  // 左边紧邻的就是模型胶囊，两枚拼起来读作「Gemini 3.1 Pro │ 极高」
  $("#effort-name").textContent = effort?.label || "中";
  $("#btn-effort").title = effort?.note
    ? `推理强度：${effort.label}（${effort.label_en}）—— ${effort.note}`
    : `推理强度：${effort?.label}（${effort?.label_en}）`;
  renderEffortDrawer();
}

function renderEffortDrawer() {
  const drawer = $("#effort-drawer");
  drawer.innerHTML = "";

  const head = document.createElement("div");
  head.className = "drawer-group";
  head.textContent = "推理强度";
  drawer.appendChild(head);

  for (const effort of state.efforts) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "drawer-item effort-item";
    item.setAttribute("role", "option");

    const name = document.createElement("span");
    name.className = "ei-name";
    name.textContent = effort.label;
    const en = document.createElement("span");
    en.className = "ei-en";
    en.textContent = effort.label_en;
    item.append(name, en);

    if (effort.note) {
      const note = document.createElement("span");
      note.className = "ei-note";
      note.textContent = effort.note;
      item.appendChild(note);
    }

    const active = effort.key === state.selection.effort;
    item.classList.toggle("active", active);
    item.setAttribute("aria-selected", String(active));
    item.addEventListener("click", () => {
      state.selection.effort = effort.key;
      closeDrawers();
      renderEffortPicker();
      persistSelection();
    });
    drawer.appendChild(item);
  }
}

// --- 抽屉开合 ---

function toggleDrawer(drawerSel, buttonSel) {
  const drawer = $(drawerSel);
  const willOpen = drawer.classList.contains("hidden");
  closeDrawers();
  drawer.classList.toggle("hidden", !willOpen);
  $(buttonSel).setAttribute("aria-expanded", String(willOpen));
}

function closeDrawers() {
  for (const [drawerSel, buttonSel] of [
    ["#model-drawer", "#btn-model"],
    ["#effort-drawer", "#btn-effort"],
    ["#attach-drawer", "#btn-attach"]
  ]) {
    $(drawerSel).classList.add("hidden");
    $(buttonSel).setAttribute("aria-expanded", "false");
  }
}

/** 把选中的模型与推理档位记进配置文件。密钥是掩码值，后端会回落到明文。 */
async function persistSelection() {
  state.config.selection = { ...state.selection };
  try {
    await api("/api/providers/config", {
      method: "POST",
      body: JSON.stringify({ config: state.config })
    });
  } catch {
    // 记住选择只是便利功能，失败不值得打扰用户
  }
}

// --- 设置弹窗：协议 → 供应商 → 模型 ---

function renderProviderForms() {
  const box = $("#provider-forms");
  box.innerHTML = "";

  for (const p of state.providers) {
    const node = $("#tpl-provider-group").content.cloneNode(true);
    const group = node.querySelector(".provider-group");
    group.dataset.provider = p.name;
    group.querySelector(".pg-label").textContent = p.label;

    const list = group.querySelector(".pg-vendors");
    for (const vendor of vendorsOf(p.name)) list.appendChild(buildVendor(p.name, vendor));

    group.querySelector(".pg-add").addEventListener("click", () => {
      const card = buildVendor(p.name, { id: newId("v"), models: [] });
      list.appendChild(card);
      syncEmptyHints();
      card.querySelector(".vd-name").focus();
    });

    box.appendChild(node);
  }

  // 填充搜索配置表单
  const search = state.config.search || {};
  $("#search-type").value = search.type || "";
  $("#search-name").value = search.name || "";
  $("#search-base-url").value = search.base_url || "";
  $("#search-api-key").value = search.api_key || "";
  $("#search-max-results").value = search.max_results || 5;

  syncEmptyHints();
}

/** 造一张供应商卡片。增删都直接操作 DOM，保存时再由 collectConfig 读回来。 */
function buildVendor(providerName, vendor) {
  const node = $("#tpl-vendor").content.cloneNode(true);
  const card = node.querySelector(".vendor");
  card.dataset.vendorId = vendor.id || newId("v");
  card.querySelector(".vd-name").value = vendor.name || "";
  card.querySelector(".vd-base-url").value = vendor.base_url || "";
  card.querySelector(".vd-api-key").value = vendor.api_key || "";

  const list = card.querySelector(".vd-model-list");
  for (const model of vendor.models || []) list.appendChild(buildModelRow(model));

  card.querySelector(".vd-model-add").addEventListener("click", () => {
    const row = buildModelRow({});
    list.appendChild(row);
    syncEmptyHints();
    row.querySelector(".mr-id").focus();
  });
  card.querySelector(".vd-del").addEventListener("click", () => {
    card.remove();
    syncEmptyHints();
  });
  card.querySelector(".vd-probe").addEventListener("click", () => probe(providerName, card));
  return card;
}

/** 一行模型：底层 ID + 对外显示名。 */
function buildModelRow(model) {
  const node = $("#tpl-model-row").content.cloneNode(true);
  const row = node.querySelector(".model-row");
  row.querySelector(".mr-id").value = model.id || "";
  row.querySelector(".mr-name").value = model.name || "";
  row.querySelector(".mr-del").addEventListener("click", () => {
    row.remove();
    syncEmptyHints();
  });
  return row;
}

/** 供应商 / 模型列表为空时才显示占位提示。 */
function syncEmptyHints() {
  for (const group of document.querySelectorAll(".provider-group")) {
    group.querySelector(".pg-empty").classList.toggle("hidden", !!group.querySelector(".vendor"));
  }
  for (const card of document.querySelectorAll(".vendor")) {
    card
      .querySelector(".vd-model-empty")
      .classList.toggle("hidden", !!card.querySelector(".model-row"));
  }
}

/** 从弹窗表单收集配置。api_key 原样提交，后端识别掩码值后会回落到明文。 */
function collectConfig() {
  const providers = {};
  for (const group of document.querySelectorAll(".provider-group")) {
    providers[group.dataset.provider] = {
      vendors: [...group.querySelectorAll(".vendor")].map((card) => ({
        id: card.dataset.vendorId,
        name: card.querySelector(".vd-name").value.trim(),
        base_url: card.querySelector(".vd-base-url").value.trim(),
        api_key: card.querySelector(".vd-api-key").value.trim(),
        models: [...card.querySelectorAll(".model-row")]
          .map((row) => ({
            id: row.querySelector(".mr-id").value.trim(),
            name: row.querySelector(".mr-name").value.trim()
          }))
          // 只填了显示名却没底层 ID 的行是半成品，丢掉（后端也会这么做）
          .filter((m) => m.id)
      }))
    };
  }

  const search = {
    type: $("#search-type").value,
    name: $("#search-name").value.trim(),
    base_url: $("#search-base-url").value.trim(),
    api_key: $("#search-api-key").value.trim(),
    max_results: parseInt($("#search-max-results").value) || 5
  };

  return { version: 2, providers, selection: { ...state.selection }, search };
}

async function saveConfig() {
  const msg = $("#save-msg");
  msg.className = "save-msg";
  msg.textContent = "保存中…";
  try {
    await api("/api/providers/config", {
      method: "POST",
      body: JSON.stringify({ config: collectConfig() })
    });
    // 重新拉一次，拿到后端掩码后的密钥，避免本地留着明文
    state.config = await api("/api/providers/config");
    ensureSelection();
    renderProviderForms();
    renderModelPicker();
    msg.className = "save-msg ok";
    msg.textContent = "已保存";
  } catch (err) {
    msg.className = "save-msg err";
    msg.textContent = err.message;
  }
}

// --- 连通性测试 ---

async function probe(providerName, card) {
  const result = card.querySelector(".vd-result");
  result.className = "vd-result";

  // 拿该供应商的第一个模型当探针，省得再让用户选一次
  const model = [...card.querySelectorAll(".mr-id")].map((el) => el.value.trim()).find(Boolean);
  if (!model) {
    result.className = "vd-result err";
    result.textContent = "请先添加一个模型（填底层 ID）";
    return;
  }
  result.textContent = `测试中（${model}）…`;

  try {
    const data = await api("/api/providers/probe", {
      method: "POST",
      body: JSON.stringify({
        provider: providerName,
        vendor_id: card.dataset.vendorId,
        base_url: card.querySelector(".vd-base-url").value.trim(),
        api_key: card.querySelector(".vd-api-key").value.trim(),
        model
      })
    });

    if (data.ok) {
      result.className = data.warnings?.length ? "vd-result warn" : "vd-result ok";
      result.textContent = data.warnings?.length
        ? `✓ 连通（${data.warnings.join("；")}）`
        : `✓ 连通 → ${data.endpoint}`;
    } else {
      result.className = "vd-result err";
      const status = data.status ? `[${data.status}] ` : "";
      result.textContent = `✗ ${status}${data.error}`;
    }
  } catch (err) {
    result.className = "vd-result err";
    result.textContent = `✗ ${err.message}`;
  }
}

// --- 消息渲染 ---

const ROLE_LABEL = { user: "我", assistant: "AI" };

const EMPTY_HINT =
  '<div class="empty-hint"><h2>开始一段对话</h2>' +
  "<p>先在「设置」里添加供应商与模型，再从输入框右侧选择模型。</p></div>";

/** 追加一个消息气泡，返回操作它的句柄。
 *
 * @param {string} role - "user" 或 "assistant"
 * @param {object|null} origin - 只对 assistant 有意义：{model, vendor, provider}
 */
function addBubble(role, origin = null) {
  const hint = $(".empty-hint");
  if (hint) hint.remove();

  const node = $("#tpl-message").content.cloneNode(true);
  const msg = node.querySelector(".msg");
  msg.classList.add(role);

  // 填充头像图标
  const avatarIcon = msg.querySelector(".avatar-icon");
  if (role === "user") {
    // 用户头像：人形图标
    avatarIcon.innerHTML = `
      <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"
            fill="currentColor"/>
    `;
  } else {
    // AI头像：芯片/电路图标
    avatarIcon.innerHTML = `
      <rect x="6" y="6" width="12" height="12" rx="1" stroke="currentColor" stroke-width="1.5" fill="none"/>
      <path d="M9 9h6M9 12h6M9 15h4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
      <path d="M6 10h-2M6 14h-2M18 10h2M18 14h2M10 6v-2M14 6v-2M10 18v2M14 18v2"
            stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    `;
  }

  const identity = msg.querySelector(".msg-identity");
  if (role === "user") {
    identity.textContent = "用户";
  } else {
    // assistant：有 origin 就显示「模型名 / 供应商 · 协议」，否则只写「AI」
    if (origin && origin.model && origin.vendor) {
      const parts = [origin.model, origin.vendor];
      if (origin.provider) parts.push(origin.provider);
      // 例：Gemini 3.7 Flash / A6API · OpenAI Chat Completions
      identity.textContent =
        parts.slice(0, 2).join(" / ") +
        (parts[2] ? " · " + parts[2] : "");
    } else {
      identity.textContent = "AI";
    }
  }

  const box = $("#messages");
  box.appendChild(node);
  const el = box.lastElementChild;

  return {
    el,
    role,
    identity: el.querySelector(".msg-identity"),
    text: el.querySelector(".msg-text"),
    thinking: el.querySelector(".msg-thinking"),
    thinkingText: el.querySelector(".msg-thinking-text"),
    attachments: el.querySelector(".msg-attachments"),
    note: el.querySelector(".msg-note"),
    meta: el.querySelector(".msg-meta"),
    actions: el.querySelector(".msg-actions")
  };
}

/** 把正文写进气泡。
 *
 * assistant 的回答走 Markdown 渲染——模型默认就用 Markdown 说话，不渲染的话
 * `**你好**` 会原样显示。用户消息保持纯文本：输入的就该是字面值，也少一条把
 * 不可信文本喂进 innerHTML 的路径。
 */
function setBubbleText(bubble, content) {
  bubble.raw = content;
  if (bubble.role === "assistant" && !bubble.el.classList.contains("error")) {
    bubble.text.classList.add("markdown");
    let html = Markdown.render(content);

    // 如果有搜索结果，转换 [1], [2] 等为可点击的角标链接
    if (bubble.searchResults && bubble.searchResults.length) {
      html = html.replace(/\[(\d+)\]/g, (match, num) => {
        const idx = parseInt(num) - 1;
        if (idx >= 0 && idx < bubble.searchResults.length) {
          const result = bubble.searchResults[idx];
          return `<a href="${result.url}" class="citation" target="_blank" rel="noopener" title="${result.title}">[${num}]</a>`;
        }
        return match;
      });
    }

    bubble.text.innerHTML = html;
  } else {
    bubble.text.textContent = content;
  }
}

/** 底部来源列表：黄色总结 + 编号链接。DOM 拼装，标题/URL 来自三方 API，用 textContent 防注入。 */
function renderSearchSources(noteEl, query, results) {
  noteEl.classList.remove("hidden");
  noteEl.classList.add("search-summary");
  noteEl.innerHTML = "";

  const summary = document.createElement("div");
  summary.textContent = `🔍 已搜索「${query}」，找到 ${results.length} 条结果`;
  summary.style.marginBottom = "8px";
  noteEl.appendChild(summary);

  for (let i = 0; i < results.length; i++) {
    const r = results[i];
    const line = document.createElement("div");
    line.style.marginBottom = "4px";

    const num = document.createElement("span");
    num.textContent = `[${i + 1}] `;
    num.style.color = "var(--accent)";
    num.style.fontWeight = "600";
    line.appendChild(num);

    const title = document.createElement("span");
    title.textContent = r.title || "无标题";
    line.appendChild(title);

    const sep = document.createElement("span");
    sep.textContent = " - ";
    sep.style.color = "var(--text-dim)";
    line.appendChild(sep);

    const link = document.createElement("a");
    link.href = r.url;
    link.textContent = r.url;
    link.target = "_blank";
    link.rel = "noopener";
    link.style.color = "var(--accent)";
    link.style.textDecoration = "none";
    link.addEventListener("mouseenter", () => (link.style.textDecoration = "underline"));
    link.addEventListener("mouseleave", () => (link.style.textDecoration = "none"));
    line.appendChild(link);

    noteEl.appendChild(line);
  }
}

/** 流式输出期间节流重渲染：一帧最多渲一次，避免整篇 Markdown 每个 delta 重解析。 */
function scheduleBubbleText(bubble, content) {
  bubble.raw = content;
  if (bubble.pendingFrame) return;
  bubble.pendingFrame = requestAnimationFrame(() => {
    bubble.pendingFrame = null;
    setBubbleText(bubble, bubble.raw);
    scrollToBottom();
  });
}

/** 打开会话时把历史消息一次性铺出来。 */
function renderMessages(messages) {
  const box = $("#messages");
  box.innerHTML = "";
  if (!messages.length) {
    box.innerHTML = EMPTY_HINT;
    return;
  }
  for (const m of messages) {
    const bubble = addBubble(m.role, m.origin);
    bubble.el.dataset.messageId = String(m.id);
    setBubbleText(bubble, m.content);
    if (m.thinking) {
      bubble.thinking.classList.remove("hidden");
      bubble.thinkingText.textContent = m.thinking;
    }
    if (m.attachments?.length) {
      renderMessageAttachments(bubble.attachments, m.attachments);
    }
    renderActions(bubble, m);
  }
  scrollToBottom();
}

function scrollToBottom() {
  const box = $("#messages");
  box.scrollTop = box.scrollHeight;
}

/** 上游报错/警告独立成一条提示气泡，不混进正文。 */
function addNotice(kind, text) {
  const bubble = addBubble("assistant");
  bubble.el.classList.add(kind);
  bubble.text.textContent = (kind === "error" ? "⚠ " : "ℹ ") + text;
  scrollToBottom();
}

// --- 消息操作：编辑提问 / 重新生成 / 复制 ---

/** 复制到剪贴板。
 *
 * 127.0.0.1 属于安全上下文，navigator.clipboard 可用；打包进 pywebview 之类的
 * 宿主时不一定，所以留一条 execCommand 的回退路径。
 */
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const scratch = document.createElement("textarea");
      scratch.value = text;
      scratch.setAttribute("readonly", "");
      scratch.style.position = "fixed";
      scratch.style.opacity = "0";
      document.body.appendChild(scratch);
      scratch.select();
      const ok = document.execCommand("copy");
      scratch.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

function actionButton(label, title, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "msg-action";
  button.textContent = label;
  button.title = title;
  button.addEventListener("click", handler);
  return button;
}

/** 给气泡挂上操作按钮。message 需要带 id，没有 id（还没落库）就不挂。 */
function renderActions(bubble, message) {
  bubble.actions.innerHTML = "";
  if (!message?.id) {
    bubble.actions.classList.add("hidden");
    return;
  }
  bubble.actions.classList.remove("hidden");
  bubble.message = message;

  if (bubble.role === "user") {
    bubble.actions.appendChild(
      actionButton("✎ 编辑", "修改这条提问并重新发送", () => startEdit(bubble, message))
    );
    return;
  }

  const copy = actionButton("⧉ 复制", "复制这条回答的原文", async () => {
    // 复制 Markdown 原文而不是渲染后的纯文本，粘到别处才能保留格式
    const ok = await copyText(bubble.raw ?? message.content);
    copy.textContent = ok ? "✓ 已复制" : "✗ 复制失败";
    setTimeout(() => {
      copy.textContent = "⧉ 复制";
    }, 1600);
  });
  bubble.actions.appendChild(copy);

  bubble.actions.appendChild(
    actionButton("⟳ 重新生成", "让模型重新回答一次", () => regenerate(message))
  );
}

/** 就地把回答换成 textarea，改完重新发送。 */
function startEdit(bubble, message) {
  if (state.controller) return;
  if (bubble.el.querySelector(".msg-editor")) return;

  const editor = document.createElement("div");
  editor.className = "msg-editor";
  const box = document.createElement("textarea");
  box.className = "msg-editor-text";
  box.value = bubble.raw ?? message.content;
  const bar = document.createElement("div");
  bar.className = "msg-editor-bar";

  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "ghost-btn";
  cancel.textContent = "取消";
  const submit = document.createElement("button");
  submit.type = "button";
  submit.className = "primary-btn";
  submit.textContent = "重新发送";

  bar.append(cancel, submit);
  editor.append(box, bar);

  bubble.text.classList.add("hidden");
  bubble.actions.classList.add("hidden");
  bubble.text.after(editor);

  const close = () => {
    editor.remove();
    bubble.text.classList.remove("hidden");
    bubble.actions.classList.remove("hidden");
  };
  const commit = () => {
    const next = box.value.trim();
    if (!next) return;
    close();
    resend(message, next);
  };

  cancel.addEventListener("click", close);
  submit.addEventListener("click", commit);
  box.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      commit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    }
  });

  autoResize(box);
  box.addEventListener("input", () => autoResize(box));
  box.focus();
  box.select();
}


/** 把这条消息及其之后的历史砍掉——后端、前端状态、DOM 三处同步。 */
async function truncateFrom(message) {
  try {
    await api(`/api/conversations/${state.activeId}/messages/${message.id}`, {
      method: "DELETE"
    });
  } catch (err) {
    setStatus(`修改历史失败：${err.message}`, true);
    return false;
  }

  const index = state.messages.findIndex((m) => m.id === message.id);
  if (index >= 0) state.messages.splice(index);

  // DOM 里从对应气泡起，把它和后面的全部移走
  const bubbles = [...document.querySelectorAll("#messages .msg")];
  const start = bubbles.findIndex((el) => el.dataset.messageId === String(message.id));
  if (start >= 0) bubbles.slice(start).forEach((el) => el.remove());
  return true;
}

/** 编辑后重新发送：砍掉这条及之后，再把新内容当一条新提问发出去。 */
async function resend(message, content) {
  if (state.controller) return;
  const choice = requireChoice();
  if (!choice) return;
  // 原附件跟着新内容一起重发，用户改的是文字而不是附件
  const attached = message.attachments || [];
  if (!(await truncateFrom(message))) return;

  await pushUserMessage(content, attached);
  await runAssistantTurn(choice);
}

/** 重新生成：砍掉这条回答及之后，用现有上下文再流一次。 */
async function regenerate(message) {
  if (state.controller) return;
  const choice = requireChoice();
  if (!choice) return;
  if (!(await truncateFrom(message))) return;
  if (!state.messages.some((m) => m.role === "user")) {
    setStatus("这条回答前面没有提问，无法重新生成", true);
    return;
  }
  await runAssistantTurn(choice);
}

// --- 发送与流式接收 ---

/** 发送前的公共校验。通过则返回选中的模型，否则给出提示并返回 null。 */
function requireChoice() {
  const choice = currentChoice();
  if (!choice) {
    setStatus("请先在设置里添加供应商与模型", true);
    return null;
  }
  if (!choice.vendor.base_url) {
    setStatus(`供应商「${vendorLabel(choice.vendor)}」还没填 Base URL`, true);
    return null;
  }
  return choice;
}

/** 渲染并落库一条用户消息，返回带 id 的那份（供编辑 / 截断使用）。 */
async function pushUserMessage(content, attached) {
  const bubble = addBubble("user");
  setBubbleText(bubble, content);
  if (attached.length) renderMessageAttachments(bubble.attachments, attached);
  scrollToBottom();

  const stored = await persistMessage("user", content, null, attached);
  const message = { id: stored?.id, role: "user", content, attachments: attached };
  state.messages.push(message);
  if (stored?.id) {
    bubble.el.dataset.messageId = String(stored.id);
    renderActions(bubble, message);
  }
  return message;
}

/** 流一轮回答：建气泡、接流、落库。发送 / 重新生成 / 编辑重发三条路共用。 */
async function runAssistantTurn(choice) {
  const origin = {
    model: choice.model.name,
    vendor: choice.vendor.name,
    provider: choice.provider
  };
  const bubble = addBubble("assistant", origin);

  // 先显示「思考中…」，收到第一个正文 delta 再撤
  bubble.text.textContent = "思考中…";
  bubble.text.classList.add("thinking-indicator");
  bubble.text.classList.add("streaming");
  setSending(true);

  let result = { answer: "", thinking: "" };
  try {
    result = await streamChat(
      {
        provider: choice.provider,
        vendor_id: choice.vendor.id,
        // 发给上游的是底层 ID，显示名只活在界面里
        model: choice.model.id,
        base_url: choice.vendor.base_url,
        reasoning: state.selection.effort
      },
      bubble
    );
  } catch (err) {
    if (err.name !== "AbortError") addNotice("error", err.message);
  } finally {
    state.controller = null;
    if (bubble.pendingFrame) cancelAnimationFrame(bubble.pendingFrame);
    bubble.pendingFrame = null;
    bubble.text.classList.remove("streaming", "thinking-indicator");
    setSending(false);
  }

  // 中途停止也把已生成的部分存下来，否则这半段答案刷新就没了
  if (result.answer) {
    setBubbleText(bubble, result.answer);
    const stored = await persistMessage("assistant", result.answer, result.thinking, null, origin);
    const message = { id: stored?.id, role: "assistant", content: result.answer, origin };
    state.messages.push(message);
    if (stored?.id) {
      bubble.el.dataset.messageId = String(stored.id);
      renderActions(bubble, message);
    }
  }
}

async function send() {
  const input = $("#inp-text");
  const content = input.value.trim();
  // 光有附件也算一次有效提问（「这张图是什么」可以靠附件本身表达）
  const attached = state.attachments.filter((a) => !a.pending && a.id);
  if ((!content && !attached.length) || state.controller) return;
  if (state.attachments.some((a) => a.pending)) {
    setStatus("附件还在上传，稍等一下", true);
    return;
  }

  const choice = requireChoice();
  if (!choice) return;
  if (!state.activeId) await createConversation();

  input.value = "";
  autoResize(input);
  clearAttachments();

  await pushUserMessage(content, attached);
  await runAssistantTurn(choice);
}

function setSending(sending) {
  $("#btn-send").disabled = sending;
  $("#btn-send").classList.toggle("hidden", sending);
  $("#btn-stop").classList.toggle("hidden", !sending);
  setStatus(sending ? "生成中…" : "");
}

/** 发起流式请求并把统一事件渲染到气泡上，返回落库需要的正文与思考。 */
async function streamChat({ provider, vendor_id, model, base_url, reasoning }, bubble) {
  state.controller = new AbortController();

  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal: state.controller.signal,
    body: JSON.stringify({
      provider,
      vendor_id,
      base_url,
      // 密钥留空，后端按 provider + vendor_id 从本地配置取明文
      api_key: "",
      model,
      reasoning,
      messages: state.messages,
      search: state.search
    })
  });

  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(data?.detail || `请求失败（${response.status}）`);
  }

  const result = { answer: "", thinking: "" };
  const notes = [];
  // 用量分两次到：Anthropic 的 input 在 message_start、output 在 message_delta，
  // 直接覆盖会把先到的那半丢掉，所以按字段合并。
  const usage = { input_tokens: null, output_tokens: null };
  let gotFirstText = false;

  try {
    for await (const event of readSSE(response)) {
      switch (event.type) {
        case "search":
          // 搜索成功：存下结果供正文里的 [1][2] 转成角标，并在底部列出来源
          if (event.results && event.results.length) {
            bubble.searchResults = event.results;
            renderSearchSources(bubble.note, event.query, event.results);
          }
          break;
        case "text":
          // 收到第一个正文 delta，撤掉「思考中…」
          if (!gotFirstText) {
            gotFirstText = true;
            bubble.text.classList.remove("thinking-indicator");
            bubble.text.textContent = "";
          }
          result.answer += event.text;
          // 节流重渲染：整篇 Markdown 每个 delta 都重解析太贵，一帧最多渲一次
          scheduleBubbleText(bubble, result.answer);
          break;
        case "thinking":
          // 只收到思维链 delta 时继续显示「思考中…」——那正是模型还在想的阶段
          result.thinking += event.text;
          bubble.thinking.classList.remove("hidden");
          bubble.thinkingText.textContent = result.thinking;
          scrollToBottom();
          break;
        case "usage":
          if (event.input_tokens != null) usage.input_tokens = event.input_tokens;
          if (event.output_tokens != null) usage.output_tokens = event.output_tokens;
          bubble.meta.textContent = formatUsage(usage);
          break;
        case "warning":
          // 挂在本条回答下方而不是单独成泡：档位被压缩这类提示每轮都会来，
          // 每次都插一个气泡会把对话冲散
          notes.push(event.message);
          bubble.note.classList.remove("hidden");
          bubble.note.textContent = notes.map((n) => `ℹ ${n}`).join("\n");
          break;
        case "error":
          addNotice("error", event.status ? `[${event.status}] ${event.message}` : event.message);
          break;
      }
    }
  } catch (err) {
    // 点「停止」会在这里抛 AbortError，但已收到的正文仍要留下
    if (err.name !== "AbortError") throw err;
  }
  return result;
}

/** 逐块读取响应体，按空行切分 SSE 消息，产出解析后的事件对象。 */
async function* readSSE(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // 最后一段可能不完整，留在 buffer 里等下一个 chunk
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop();

    for (const block of blocks) {
      const event = parseSSEBlock(block);
      if (event) yield event;
    }
  }

  const tail = parseSSEBlock(buffer);
  if (tail) yield tail;
}

function parseSSEBlock(block) {
  const data = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).replace(/^ /, ""))
    .join("\n");
  if (!data) return null;
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}

function formatUsage(event) {
  const parts = [];
  if (event.input_tokens != null) parts.push(`输入 ${event.input_tokens}`);
  if (event.output_tokens != null) parts.push(`输出 ${event.output_tokens}`);
  return parts.length ? `${parts.join(" · ")} tokens` : "";
}

// --- 交互绑定 ---

function autoResize(el) {
  el.style.height = "auto";
  el.style.height = `${el.scrollHeight}px`;
}

function bindEvents() {
  $("#btn-send").addEventListener("click", send);
  $("#btn-stop").addEventListener("click", () => state.controller?.abort());
  $("#btn-new").addEventListener("click", createConversation);
  $("#btn-settings").addEventListener("click", () => $("#modal").classList.remove("hidden"));
  $("#btn-close").addEventListener("click", () => $("#modal").classList.add("hidden"));
  $("#btn-save").addEventListener("click", saveConfig);
  $("#btn-search-probe").addEventListener("click", async () => {
    const btn = $("#btn-search-probe");
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "测试中…";
    try {
      const result = await api("/api/providers/search/probe", {
        method: "POST",
        body: JSON.stringify({
          type: $("#search-type").value,
          base_url: $("#search-base-url").value.trim(),
          api_key: $("#search-api-key").value.trim()
        })
      });
      if (result.ok) {
        btn.textContent = "✓ 成功";
        setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 2000);
      } else {
        btn.textContent = "✗ 失败";
        setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 2000);
        console.error("搜索测试失败：", result.error);
      }
    } catch (err) {
      btn.textContent = "✗ 失败";
      setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 2000);
      console.error("搜索测试错误：", err);
    }
  });

  $("#btn-model").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleDrawer("#model-drawer", "#btn-model");
  });
  $("#btn-effort").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleDrawer("#effort-drawer", "#btn-effort");
  });
  $("#btn-attach").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleDrawer("#attach-drawer", "#btn-attach");
  });

  $("#btn-search").addEventListener("click", () => {
    state.search = !state.search;
    const btn = $("#btn-search");
    btn.classList.toggle("active", state.search);
    btn.title = state.search ? "联网搜索（开启）" : "联网搜索（关闭）";
  });

  // 抽屉里的两项各点开一个隐藏的 file input
  for (const [optionSel, inputSel] of [
    ["#opt-image", "#inp-image"],
    ["#opt-file", "#inp-file"]
  ]) {
    $(optionSel).addEventListener("click", () => {
      closeDrawers();
      $(inputSel).click();
    });
  }
  for (const inputSel of ["#inp-image", "#inp-file"]) {
    $(inputSel).addEventListener("change", async (e) => {
      const files = [...e.target.files];
      // 清空 value，同一个文件连选两次也能再触发 change
      e.target.value = "";
      await addFiles(files);
    });
  }

  // 点抽屉以外的任何地方都收起来
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".picker")) closeDrawers();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawers();
  });

  const input = $("#inp-text");
  input.addEventListener("input", () => autoResize(input));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send();
    }
  });

  // 点遮罩关闭弹窗
  $("#modal").addEventListener("click", (e) => {
    if (e.target === $("#modal")) $("#modal").classList.add("hidden");
  });

  // 密钥可见性切换（事件委托，因为供应商卡片是动态创建的）
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".toggle-visibility");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();

    const container = btn.closest(".input-with-toggle");
    const input = container.querySelector("input");
    if (!input) return;

    // 切换输入框类型
    if (input.type === "password") {
      input.type = "text";
      btn.classList.add("visible");
      btn.title = "隐藏密钥";
    } else {
      input.type = "password";
      btn.classList.remove("visible");
      btn.title = "显示密钥";
    }
  });

}

init();
