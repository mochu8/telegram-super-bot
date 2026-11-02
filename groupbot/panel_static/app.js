const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
}

const main = document.querySelector("main");
const nav = document.querySelector("#nav");
const groupSelect = document.querySelector("#group");
const subtitle = document.querySelector("#subtitle");

const pages = [
  ["dash", "概览"],
  ["modules", "开关"],
  ["words", "屏蔽词"],
  ["regex", "正则"],
  ["links", "白名单"],
  ["flood", "防刷警告"],
  ["verify", "入群验证"],
  ["welcome", "欢迎语"],
  ["replies", "自动回复"],
  ["announce", "定时公告"],
  ["rules", "群规"],
  ["points", "积分"],
  ["rank", "排行"],
  ["log", "记录"],
  ["channel", "日志频道"],
];

let groups = [];
let chatId = null;
let settings = null;
let page = "dash";

function el(tag, props, children) {
  const node = document.createElement(tag);
  Object.entries(props || {}).forEach(([key, value]) => {
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  });
  (children || []).forEach((child) => node.append(child));
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function field(label, control) {
  return el("label", { class: "card" }, [el("div", { text: label, class: "hint" }), control]);
}

function button(text, className, onClick) {
  const node = el("button", { text, class: className || "", type: "button" });
  node.addEventListener("click", onClick);
  return node;
}

async function api(path, method, body) {
  const headers = { "X-Telegram-Init-Data": tg ? tg.initData : "" };
  const options = { method: method || "GET", headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

function showError(error) {
  clear(main);
  main.append(el("div", { class: "card error", text: error.message || "请求失败" }));
}

function selectedGroup() {
  return groups.find((item) => String(item.chat_id) === String(chatId));
}

async function loadGroups() {
  const data = await api("/api/groups");
  groups = data.groups;
  clear(groupSelect);
  if (!groups.length) {
    groupSelect.append(el("option", { text: "没有可管理的群", value: "" }));
    chatId = null;
    return;
  }
  groups.forEach((item) => {
    groupSelect.append(el("option", { value: String(item.chat_id), text: item.title }));
  });
  const params = new URLSearchParams(location.search);
  const hinted = params.get("chat");
  const start = tg && tg.initDataUnsafe ? tg.initDataUnsafe.start_param : "";
  const fromStart = start && start.startsWith("g") ? start.slice(1) : "";
  const preferred = hinted || fromStart;
  const known = groups.some((item) => String(item.chat_id) === String(preferred));
  chatId = known ? Number(preferred) : groups[0].chat_id;
  groupSelect.value = String(chatId);
}

async function loadSettings() {
  if (!chatId) {
    settings = null;
    return;
  }
  const data = await api(`/api/groups/${chatId}`);
  settings = data.settings;
  const group = selectedGroup();
  subtitle.textContent = group ? `${group.title} · ${data.member_count ?? "?"} 人` : "";
  document.querySelector("#title").textContent = "群管面板";
}

function renderNav() {
  clear(nav);
  pages.forEach(([id, label]) => {
    const node = button(label, id === page ? "active" : "", () => {
      page = id;
      renderNav();
      renderPage();
    });
    nav.append(node);
  });
}

function card(title, children) {
  return el("section", { class: "card" }, [el("strong", { text: title }), ...children]);
}

function switchRow(key, label) {
  const input = el("input", { type: "checkbox" });
  input.checked = Boolean(settings[key]);
  input.addEventListener("change", async () => {
    try {
      const data = await api(`/api/groups/${chatId}/toggles`, "POST", { [key]: input.checked });
      settings = data.settings;
    } catch (error) {
      input.checked = !input.checked;
      showError(error);
    }
  });
  return el("label", { class: "switch" }, [el("span", { text: label }), input]);
}

function numberInput(key) {
  const input = el("input", { type: "number", value: String(settings[key]) });
  return field(key, input);
}

async function saveNumbers(keys) {
  const body = {};
  keys.forEach((key) => {
    const input = document.querySelector(`[data-key="${key}"]`);
    body[key] = Number(input.value);
  });
  const data = await api(`/api/groups/${chatId}/numbers`, "POST", body);
  settings = data.settings;
}

function numberField(key, label) {
  const input = el("input", { type: "number", value: String(settings[key]) });
  input.dataset.key = key;
  return field(label, input);
}

function renderDash(data) {
  return [
    card("群概况", [
      el("div", { text: `成员：${data.member_count ?? "未知"}` }),
      el("div", { text: `已记录成员：${data.tracked_members}` }),
      el("div", { text: `消息记录：${data.messages}` }),
      el("div", { text: `管理记录：${data.actions}` }),
    ]),
  ];
}

function renderModules() {
  const labels = [
    ["anti_ad_enabled", "广告过滤"],
    ["ban_links", "拦截外部链接"],
    ["ban_channel_messages", "禁止频道身份发言"],
    ["ban_forwards", "禁止转发"],
    ["flood_enabled", "防刷屏"],
    ["verify_enabled", "入群验证"],
    ["welcome_enabled", "欢迎语"],
    ["auto_reply_enabled", "关键词自动回复"],
    ["announce_enabled", "定时公告"],
    ["rules_enabled", "群规"],
    ["points_enabled", "积分签到"],
    ["delete_service", "删除进群提示"],
    ["delete_leave", "删除退群提示"],
  ];
  return [card("功能开关", labels.map(([key, label]) => switchRow(key, label)))];
}

function listEditor(items, onAdd, onRemove, placeholder) {
  const search = el("input", { type: "search", placeholder: "搜索" });
  const box = el("div");
  const input = el("input", { type: "text", placeholder });
  function draw() {
    clear(box);
    const query = search.value.trim().toLowerCase();
    items.filter((item) => item.toLowerCase().includes(query)).forEach((item) => {
      box.append(el("div", { class: "item" }, [
        el("span", { text: item }),
        button("删除", "", async () => {
          try {
            await onRemove(item);
          } catch (error) {
            showError(error);
          }
        }),
      ]));
    });
  }
  search.addEventListener("input", draw);
  draw();
  return card(placeholder, [
    search,
    box,
    input,
    button("添加", "primary", async () => {
      try {
        await onAdd(input.value);
      } catch (error) {
        showError(error);
      }
    }),
  ]);
}

function renderWords() {
  return [listEditor(settings.keywords, async (word) => {
    const data = await api(`/api/groups/${chatId}/keywords`, "POST", { word, remove: false });
    settings = data.settings;
    renderPage();
  }, async (word) => {
    const data = await api(`/api/groups/${chatId}/keywords`, "POST", { word, remove: true });
    settings = data.settings;
    renderPage();
  }, "屏蔽词")];
}

function renderRegex() {
  const sample = el("textarea", { placeholder: "贴一段消息，看看会不会被拦截" });
  const result = el("div", { class: "hint", text: "" });
  const editor = listEditor(settings.regex_patterns, async (pattern) => {
    const data = await api(`/api/groups/${chatId}/patterns`, "POST", { pattern, remove: false });
    settings = data.settings;
    renderPage();
  }, async (pattern) => {
    const data = await api(`/api/groups/${chatId}/patterns`, "POST", { pattern, remove: true });
    settings = data.settings;
    renderPage();
  }, "正则");
  editor.append(
    sample,
    button("测试这段文字", "primary", async () => {
      try {
        const data = await api(`/api/groups/${chatId}/test`, "POST", { text: sample.value });
        const parts = [];
        parts.push(data.violation ? `会拦截：${data.kind || ""}` : "不会拦截");
        if (data.reply) parts.push(`自动回复：${data.reply}`);
        result.textContent = parts.join("。");
        result.className = data.violation ? "error" : "ok";
      } catch (error) {
        showError(error);
      }
    }),
    result,
  );
  return [editor];
}

function renderLinks() {
  return [listEditor(settings.whitelist_domains, async (domain) => {
    const data = await api(`/api/groups/${chatId}/whitelist`, "POST", { domain, remove: false });
    settings = data.settings;
    renderPage();
  }, async (domain) => {
    const data = await api(`/api/groups/${chatId}/whitelist`, "POST", { domain, remove: true });
    settings = data.settings;
    renderPage();
  }, "允许的域名")];
}

function renderFlood() {
  const keys = [
    ["flood_max", "窗口内允许条数"],
    ["flood_window", "窗口秒数"],
    ["flood_mute", "刷屏禁言秒数"],
    ["warn_threshold", "警告上限"],
    ["mute_duration", "达上限后的禁言秒数，0 为永久"],
    ["new_user_restrict_minutes", "新成员限制发链接的分钟数"],
  ];
  return [card("防刷和警告", [
    ...keys.map(([key, label]) => numberField(key, label)),
    button("保存", "primary", async () => {
      try {
        await saveNumbers(keys.map(([key]) => key));
        renderPage();
      } catch (error) {
        showError(error);
      }
    }),
  ])];
}

function renderVerify() {
  const mode = el("select");
  [["button", "按钮"], ["captcha", "算术题"]].forEach(([value, label]) => {
    const option = el("option", { value, text: label });
    if (settings.verify_mode === value) option.selected = true;
    mode.append(option);
  });
  const timeout = el("input", { type: "number", value: String(settings.verify_timeout) });
  return [card("入群验证", [
    switchRow("verify_enabled", "开启验证"),
    field("验证方式", mode),
    field("超时秒数", timeout),
    button("保存方式和超时", "primary", async () => {
      try {
        const numbers = await api(`/api/groups/${chatId}/numbers`, "POST", {
          verify_timeout: Number(timeout.value),
        });
        settings = numbers.settings;
        const flags = await api(`/api/groups/${chatId}/toggles`, "POST", { verify_mode: mode.value });
        settings = flags.settings;
        renderPage();
      } catch (error) {
        showError(error);
      }
    }),
  ])];
}

function renderWelcome() {
  const text = el("textarea");
  text.value = settings.welcome_message;
  const list = el("div");
  const buttons = settings.welcome_buttons.map((item) => ({ ...item }));
  function draw() {
    clear(list);
    buttons.forEach((item, index) => {
      list.append(el("div", { class: "item" }, [
        el("span", { text: `${item.text} · ${item.action === "rules" ? "群规" : item.url}` }),
        button("删除", "", () => {
          buttons.splice(index, 1);
          draw();
        }),
      ]));
    });
  }
  draw();
  const label = el("input", { type: "text", placeholder: "按钮文字" });
  const url = el("input", { type: "url", placeholder: "https:// 链接，群规按钮可留空" });
  const rules = el("input", { type: "checkbox" });
  return [card("欢迎语", [
    el("div", { class: "hint", text: "可用 {name} {username} {id} {chat}" }),
    text,
    switchRow("welcome_enabled", "发送欢迎语"),
    list,
    label,
    url,
    el("label", { class: "switch" }, [el("span", { text: "这是群规按钮" }), rules]),
    button("加入按钮", "", () => {
      buttons.push({
        text: label.value,
        action: rules.checked ? "rules" : "url",
        url: rules.checked ? "" : url.value,
      });
      label.value = "";
      url.value = "";
      rules.checked = false;
      draw();
    }),
    button("保存", "primary", async () => {
      try {
        const data = await api(`/api/groups/${chatId}/welcome`, "POST", {
          text: text.value,
          buttons,
        });
        settings = data.settings;
        renderPage();
      } catch (error) {
        showError(error);
      }
    }),
  ])];
}

function renderReplies() {
  const items = settings.auto_replies.map((item) => ({ ...item }));
  const list = el("div");
  function draw() {
    clear(list);
    items.forEach((item, index) => {
      list.append(el("div", { class: "item" }, [
        el("span", { text: `${item.keyword} → ${item.response}` }),
        button("删除", "", () => {
          items.splice(index, 1);
          draw();
        }),
      ]));
    });
  }
  draw();
  const keyword = el("input", { type: "text", placeholder: "关键词" });
  const response = el("textarea", { placeholder: "回复内容" });
  const mode = el("select");
  mode.append(el("option", { value: "contains", text: "包含" }));
  mode.append(el("option", { value: "exact", text: "完全一致" }));
  return [card("自动回复", [
    switchRow("auto_reply_enabled", "启用自动回复"),
    list,
    keyword,
    response,
    mode,
    button("添加", "", () => {
      items.push({ keyword: keyword.value, response: response.value, mode: mode.value });
      keyword.value = "";
      response.value = "";
      draw();
    }),
    button("保存", "primary", async () => {
      try {
        const data = await api(`/api/groups/${chatId}/replies`, "POST", { replies: items });
        settings = data.settings;
        renderPage();
      } catch (error) {
        showError(error);
      }
    }),
  ])];
}

function renderAnnounce() {
  const items = settings.announcements.map((item) => ({ ...item }));
  const list = el("div");
  function draw() {
    clear(list);
    items.forEach((item, index) => {
      const row = el("div", { class: "item" }, [
        el("span", { text: `${item.enabled ? "开" : "关"} · 每 ${item.interval_minutes} 分钟 · ${item.text}` }),
      ]);
      row.append(button("立即发送", "", async () => {
        try {
          await api(`/api/groups/${chatId}/announcements/send`, "POST", { id: item.id });
        } catch (error) {
          showError(error);
        }
      }));
      row.append(button("删除", "", () => {
        items.splice(index, 1);
        draw();
      }));
      list.append(row);
    });
  }
  draw();
  const text = el("textarea", { placeholder: "公告内容" });
  const interval = el("input", { type: "number", value: "1440" });
  return [card("定时公告", [
    switchRow("announce_enabled", "启用定时公告"),
    el("div", { class: "hint", text: "间隔至少 15 分钟。保存后从下个间隔开始发送。" }),
    list,
    text,
    field("间隔分钟", interval),
    button("添加", "", () => {
      items.push({
        text: text.value,
        interval_minutes: Number(interval.value),
        enabled: true,
      });
      text.value = "";
      draw();
    }),
    button("保存", "primary", async () => {
      try {
        const data = await api(`/api/groups/${chatId}/announcements`, "POST", { announcements: items });
        settings = data.settings;
        renderPage();
      } catch (error) {
        showError(error);
      }
    }),
  ])];
}

function renderRules() {
  const text = el("textarea");
  text.value = settings.rules_text;
  return [card("群规", [
    switchRow("rules_enabled", "允许成员查看群规"),
    text,
    button("保存", "primary", async () => {
      try {
        const data = await api(`/api/groups/${chatId}/rules`, "POST", {
          text: text.value,
          enabled: settings.rules_enabled,
        });
        settings = data.settings;
      } catch (error) {
        showError(error);
      }
    }),
  ])];
}

function renderPoints() {
  const keys = [
    ["sign_daily", "每日签到积分"],
    ["msg_per_point", "多少条消息得 1 分"],
    ["max_points_per_day", "每天发言积分上限"],
    ["dice_cost", "骰子消耗"],
    ["dice_reward", "骰子奖励"],
    ["dice_reward_threshold", "获奖最低点数"],
  ];
  const bonus = el("input", { type: "text", value: settings.sign_bonus.join(",") });
  return [card("积分", [
    switchRow("points_enabled", "启用积分"),
    ...keys.map(([key, label]) => numberField(key, label)),
    field("连续签到奖励天数，逗号分隔", bonus),
    button("保存", "primary", async () => {
      try {
        const body = {};
        keys.forEach(([key]) => {
          body[key] = Number(document.querySelector(`[data-key="${key}"]`).value);
        });
        body.sign_bonus = bonus.value.split(",").map((item) => Number(item.trim())).filter((item) => item);
        const data = await api(`/api/groups/${chatId}/numbers`, "POST", body);
        settings = data.settings;
        renderPage();
      } catch (error) {
        showError(error);
      }
    }),
  ])];
}

async function renderRank() {
  const data = await api(`/api/groups/${chatId}/ranking`);
  const list = el("div");
  data.ranking.forEach((item, index) => {
    list.append(el("div", { class: "item" }, [
      el("span", { text: `${index + 1}. ${item.name}` }),
      el("span", { text: `${item.points} 分 / ${item.messages} 条` }),
    ]));
  });
  return [card("积分排行", [list])];
}

async function renderLog() {
  const scope = el("select");
  [["all", "全部"], ["violations", "违规"], ["audit", "管理日志"]].forEach(([value, label]) => {
    scope.append(el("option", { value, text: label }));
  });
  const list = el("div");
  async function draw() {
    const data = await api(`/api/groups/${chatId}/log?scope=${scope.value}&limit=30`);
    clear(list);
    const warnings = await api(`/api/groups/${chatId}/warnings`);
    if (warnings.warnings.length) {
      list.append(el("div", { class: "hint", text: "当前警告" }));
      warnings.warnings.forEach((item) => {
        list.append(el("div", { text: `${item.name} · ${item.warnings} 次` }));
      });
    }
    data.entries.forEach((item) => {
      list.append(el("div", { class: "item" }, [
        el("span", { text: `${item.created_at} ${item.kind}/${item.action}` }),
        el("span", { text: item.detail || "" }),
      ]));
    });
  }
  scope.addEventListener("change", () => draw().catch(showError));
  await draw();
  return [card("记录", [scope, list])];
}

function renderChannel() {
  const input = el("input", {
    type: "text",
    value: settings.log_channel_id ? String(settings.log_channel_id) : "",
    placeholder: "频道 ID，留空表示关闭",
  });
  return [card("日志频道", [
    el("div", { class: "hint", text: "把机器人加进频道或群，填它的数字 ID。留空则本群不再转发。" }),
    input,
    button("保存", "primary", async () => {
      try {
        const raw = input.value.trim();
        const data = await api(`/api/groups/${chatId}/log-channel`, "POST", {
          log_channel_id: raw ? Number(raw) : 0,
        });
        settings = data.settings;
      } catch (error) {
        showError(error);
      }
    }),
  ])];
}

async function renderPage() {
  clear(main);
  if (!tg) {
    main.append(el("div", { class: "card", text: "请在 Telegram 里打开这个面板。" }));
    return;
  }
  if (!chatId || !settings) {
    main.append(el("div", { class: "card", text: "没有可以管理的群。请先把机器人加进群，并确认你是管理员。" }));
    return;
  }
  try {
    let nodes = [];
    if (page === "dash") {
      const data = await api(`/api/groups/${chatId}`);
      settings = data.settings;
      nodes = renderDash(data);
    } else if (page === "modules") nodes = renderModules();
    else if (page === "words") nodes = renderWords();
    else if (page === "regex") nodes = renderRegex();
    else if (page === "links") nodes = renderLinks();
    else if (page === "flood") nodes = renderFlood();
    else if (page === "verify") nodes = renderVerify();
    else if (page === "welcome") nodes = renderWelcome();
    else if (page === "replies") nodes = renderReplies();
    else if (page === "announce") nodes = renderAnnounce();
    else if (page === "rules") nodes = renderRules();
    else if (page === "points") nodes = renderPoints();
    else if (page === "rank") nodes = await renderRank();
    else if (page === "log") nodes = await renderLog();
    else if (page === "channel") nodes = renderChannel();
    nodes.forEach((node) => main.append(node));
  } catch (error) {
    showError(error);
  }
}

groupSelect.addEventListener("change", async () => {
  chatId = Number(groupSelect.value);
  try {
    await loadSettings();
    renderPage();
  } catch (error) {
    showError(error);
  }
});

async function boot() {
  renderNav();
  if (!tg || !tg.initData) {
    subtitle.textContent = "请在 Telegram 里打开";
    renderPage();
    return;
  }
  try {
    await loadGroups();
    await loadSettings();
    renderPage();
  } catch (error) {
    showError(error);
  }
}

boot();
