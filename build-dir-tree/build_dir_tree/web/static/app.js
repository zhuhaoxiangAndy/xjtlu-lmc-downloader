const $ = (id) => document.getElementById(id);

let treeData = [];
const collapsed = new Set();
const selected = new Set();
let allIds = [];
let settings = {};

function setStatus(text, kind) {
  const el = $("status");
  el.textContent = text || "";
  el.className = "status" + (kind ? " " + kind : "");
}

function setSettingsStatus(text, kind) {
  const el = $("settingsStatus");
  el.textContent = text || "";
  el.className = "status" + (kind ? " " + kind : "");
}

function collectIds(nodes, out) {
  for (const n of nodes) {
    out.push(n.id);
    if (n.children && n.children.length) collectIds(n.children, out);
  }
  return out;
}

function namingPayload() {
  return {
    number: $("number").checked,
    collapseSpaces: $("collapseSpaces").checked,
    spaceReplace: $("spaceReplace").value,
    illegalReplace: $("illegalReplace").value,
    dropTrailing: $("dropTrailing").checked,
  };
}

async function post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return await res.json();
}

/* ---------------- 页签 ---------------- */

function showView(which) {
  const main = which === "main";
  $("viewMain").hidden = !main;
  $("viewSettings").hidden = main;
  $("tabMain").classList.toggle("active", main);
  $("tabSettings").classList.toggle("active", !main);
  if (!main) loadSettingsIntoForm();
}

$("tabMain").addEventListener("click", () => showView("main"));
$("tabSettings").addEventListener("click", () => showView("settings"));

/* ---------------- 设置 ---------------- */

function applySettingsToMainForm(s) {
  $("number").checked = !!s.number;
  $("collapseSpaces").checked = !!s.collapseSpaces;
  $("dropTrailing").checked = s.dropTrailing !== false;
  $("spaceReplace").value = s.spaceReplace || "";
  $("illegalReplace").value = s.illegalReplace || "";
  $("createRoot").checked = !!s.createRoot;
  $("rootNameField").hidden = !s.createRoot;
  $("rootName").value = s.rootName || "";
  if (s.outputDir) $("output").value = s.outputDir;
}

function loadSettingsIntoForm() {
  post("/api/settings", {}).then((data) => {
    if (!data.ok) return;
    settings = data.settings || {};
    const s = settings;
    $("setOutputDir").value = s.outputDir || "";
    $("setCreateRoot").checked = !!s.createRoot;
    $("setRootNameField").hidden = !s.createRoot;
    $("setRootName").value = s.rootName || "";
    $("setNumber").checked = !!s.number;
    $("setCollapse").checked = !!s.collapseSpaces;
    $("setDropTrailing").checked = s.dropTrailing !== false;
    $("setSpaceReplace").value = s.spaceReplace || "";
    $("setIllegalReplace").value = s.illegalReplace || "";
    $("setScanDir").value = s.scanDir || "";
    for (const r of document.querySelectorAll('input[name="setSelection"]')) {
      r.checked = r.value === (s.defaultSelection || "sections");
    }
    const auto = data.outputDefault || "";
    $("setOutputHint").innerHTML = auto
      ? "留空时自动使用：<code>" + auto + "</code>"
      : "";
    $("configPathLabel").textContent = "设置文件：" + (data.configPath || "");
    applySettingsToMainForm(s);
  });
}

$("setCreateRoot").addEventListener("change", () => {
  $("setRootNameField").hidden = !$("setCreateRoot").checked;
});

$("setBrowseOut").addEventListener("click", async () => {
  const data = await post("/api/dialog", { kind: "dir" });
  if (data && data.ok && data.path) $("setOutputDir").value = data.path;
});

$("setBrowseScan").addEventListener("click", async () => {
  const data = await post("/api/dialog", { kind: "dir" });
  if (data && data.ok && data.path) $("setScanDir").value = data.path;
});

$("saveSettings").addEventListener("click", async () => {
  const choice = document.querySelector('input[name="setSelection"]:checked');
  const payload = {
    outputDir: $("setOutputDir").value.trim(),
    createRoot: $("setCreateRoot").checked,
    rootName: $("setRootName").value.trim(),
    defaultSelection: choice ? choice.value : "sections",
    number: $("setNumber").checked,
    collapseSpaces: $("setCollapse").checked,
    dropTrailing: $("setDropTrailing").checked,
    spaceReplace: $("setSpaceReplace").value,
    illegalReplace: $("setIllegalReplace").value,
    scanDir: $("setScanDir").value.trim(),
  };
  const data = await post("/api/settings", { save: true, settings: payload });
  if (data.ok) {
    settings = data.settings;
    applySettingsToMainForm(settings);
    setSettingsStatus("已保存", "ok");
  } else {
    setSettingsStatus("保存失败：" + data.error, "err");
  }
});

$("resetSettings").addEventListener("click", async () => {
  const data = await post("/api/settings", { save: true, settings: {
    outputDir: "", createRoot: false, rootName: "", defaultSelection: "sections",
    number: false, collapseSpaces: false, dropTrailing: true,
    spaceReplace: "", illegalReplace: "", scanDir: "",
  } });
  if (data.ok) {
    loadSettingsIntoForm();
    setSettingsStatus("已恢复默认", "ok");
  }
});

/* ---------------- 扫描与选择文件 ---------------- */

function humanSize(bytes) {
  if (bytes > 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + " MB";
  if (bytes > 1024) return Math.round(bytes / 1024) + " KB";
  return bytes + " B";
}

function showDefaultOutput(suggested, documents) {
  const hint = $("defaultHint");
  hint.textContent = "";
  if (!suggested) return;
  hint.appendChild(document.createTextNode("留空时建在："));
  const code = document.createElement("code");
  code.textContent = suggested;
  hint.appendChild(code);
  if (documents) {
    const note = document.createElement("span");
    note.className = "hint-note";
    note.textContent = "  （文档目录：" + documents + "）";
    hint.appendChild(note);
  }
}

function renderFound(directory, files) {
  const host = $("found");
  host.textContent = "";
  $("scanInfo").textContent = directory || "";
  if (!files || !files.length) {
    host.innerHTML =
      '<div class="empty">这个目录里没有找到 .mhtml / .html 快照。<br>' +
      '<b>下一步：</b>点「换个目录查找…」挑一个文件夹，' +
      '或点「选择课程快照…」直接指定文件。</div>';
    return;
  }
  const list = document.createElement("ul");
  list.className = "found-list";
  for (const file of files) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.className = "found-item";
    btn.title = file.path;
    btn.innerHTML =
      '<span class="fname"></span><span class="fmeta">' + humanSize(file.size) + "</span>";
    btn.querySelector(".fname").textContent = file.name;
    btn.addEventListener("click", async () => {
      for (const other of host.querySelectorAll(".found-item")) other.classList.remove("active");
      btn.classList.add("active");
      $("snapshot").value = file.path;
      setStatus("正在载入 " + file.name + " …");
      await loadTree("default", file.path);
    });
    li.appendChild(btn);
    list.appendChild(li);
  }
  host.appendChild(list);
}

async function scan(directory) {
  $("rescan").disabled = true;
  try {
    const data = await post("/api/scan", { directory: directory || "" });
    if (!data.ok) { setStatus("扫描失败：" + data.error, "err"); return; }
    renderFound(data.directory, data.files);
    showDefaultOutput(data.suggestedOutput, data.documents);
    if (data.settings) {
      settings = data.settings;
      applySettingsToMainForm(settings);
    }
  } catch (err) {
    setStatus("扫描失败：" + err.message, "err");
  } finally {
    $("rescan").disabled = false;
  }
}

async function pickFile() {
  $("browseFile").disabled = true;
  try {
    const data = await post("/api/dialog", { kind: "file" });
    if (data.error) { setStatus("无法打开对话框：" + data.error, "err"); return; }
    if (!data.ok || !data.path) { setStatus("已取消", ""); return; }
    $("snapshot").value = data.path;
    await loadTree("default", data.path);
  } catch (err) {
    setStatus("打开对话框失败：" + err.message, "err");
  } finally {
    $("browseFile").disabled = false;
  }
}

/* ---------------- 目录树 ---------------- */

function setSubtree(node, on) {
  if (on) selected.add(node.id);
  else selected.delete(node.id);
  for (const child of node.children || []) setSubtree(child, on);
}

function selectByMode(mode) {
  selected.clear();
  if (mode === "all") {
    for (const id of allIds) selected.add(id);
  } else if (mode === "sections") {
    const walk = (nodes) => {
      for (const n of nodes) {
        if (n.kind === "section") selected.add(n.id);
        walk(n.children || []);
      }
    };
    walk(treeData);
  }
}

function renderTree() {
  const host = $("tree");
  host.textContent = "";
  if (!treeData.length) {
    host.innerHTML =
      '<div class="empty">还没有载入课程。<br>' +
      '<b>下一步：</b>在上方「1. 选择快照」里点一个文件名，' +
      '或点「选择课程快照…」按钮挑文件。</div>';
    $("countLabel").textContent = "";
    return;
  }

  const build = (nodes, depth) => {
    const ul = document.createElement("ul");
    ul.className = "node-list" + (depth === 0 ? " root" : "");
    for (const node of nodes) {
      const li = document.createElement("li");
      li.className = "node " + (node.kind === "section" ? "is-section" : "is-activity");
      const row = document.createElement("div");
      row.className = "node-row";
      const hasKids = node.children && node.children.length > 0;
      const caret = document.createElement("button");
      caret.className = "caret" + (hasKids ? "" : " leaf");
      caret.textContent = hasKids ? (collapsed.has(node.id) ? "\u25B8" : "\u25BE") : "";
      if (hasKids) {
        caret.addEventListener("click", (e) => {
          e.stopPropagation();
          if (collapsed.has(node.id)) collapsed.delete(node.id);
          else collapsed.add(node.id);
          renderTree();
        });
      }
      row.appendChild(caret);
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = selected.has(node.id);
      cb.addEventListener("change", () => { setSubtree(node, cb.checked); renderTree(); });
      row.appendChild(cb);
      const label = document.createElement("span");
      label.className = "node-label";
      label.textContent = node.dirName;
      label.title = node.label + "\n" + node.path;
      row.appendChild(label);
      if (node.kind === "activity" && node.modtype) {
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = node.modtype;
        row.appendChild(tag);
      }
      li.appendChild(row);
      if (hasKids && !collapsed.has(node.id)) li.appendChild(build(node.children, depth + 1));
      ul.appendChild(li);
    }
    return ul;
  };

  host.appendChild(build(treeData, 0));
  $("countLabel").textContent = `已选 ${selected.size} / ${allIds.length} 个目录`;
}

async function loadTree(selectionMode, snapshotPath) {
  const snapshot = snapshotPath || $("snapshot").value.trim();
  if (!snapshot) { setStatus("请先选择或填写快照路径", "err"); return; }
  setStatus("正在解析…");
  try {
    const body = Object.assign({ snapshot, output: $("output").value.trim(),
      rootName: $("rootName").value.trim(), createRoot: $("createRoot").checked }, namingPayload());
    const data = await post("/api/build", body);
    if (!data.ok) { setStatus("出错：" + data.error, "err"); return; }
    treeData = data.tree || [];
    allIds = collectIds(treeData, []);
    const mode = selectionMode === "default" ? (settings.defaultSelection || "sections") : selectionMode;
    if (mode === "keep") {
      const valid = new Set(allIds);
      for (const id of Array.from(selected)) if (!valid.has(id)) selected.delete(id);
    } else {
      selectByMode(mode);
    }
    const c = data.course || {};
    $("summary").textContent =
      `${c.title || ""} — 顶层 ${c.top_sections} / 子章节 ${c.subsections} / 活动 ${c.activities}`;
    if (data.output) {
      showDefaultOutput(data.output, null);
    }
    renderTree();
    setStatus(`已载入 ${allIds.length} 个目录，已选 ${selected.size} 个`, "ok");
  } catch (err) {
    setStatus("请求失败：" + err.message, "err");
  }
}

/* ---------------- 事件 ---------------- */

$("browseFile").addEventListener("click", pickFile);
$("browseDir").addEventListener("click", async () => {
  const data = await post("/api/dialog", { kind: "dir" });
  if (data && data.ok && data.path) scan(data.path);
});
$("browseOut").addEventListener("click", async () => {
  const data = await post("/api/dialog", { kind: "dir" });
  if (data && data.ok && data.path) $("output").value = data.path;
});
$("rescan").addEventListener("click", () => scan($("scanInfo").textContent.trim()));
$("load").addEventListener("click", () => loadTree("default"));
$("reload").addEventListener("click", () => loadTree("keep"));

$("createRoot").addEventListener("change", () => {
  $("rootNameField").hidden = !$("createRoot").checked;
});

$("expandAll").addEventListener("click", () => { collapsed.clear(); renderTree(); });
$("collapseAll").addEventListener("click", () => {
  for (const n of treeData) if (n.children && n.children.length) collapsed.add(n.id);
  renderTree();
});
$("selectAll").addEventListener("click", () => selectByMode("all") || renderTree());
$("selectNone").addEventListener("click", () => selectByMode("none") || renderTree());
$("selectSections").addEventListener("click", () => selectByMode("sections") || renderTree());

$("create").addEventListener("click", async () => {
  const output = $("output").value.trim();
  if (!selected.size) { setStatus("还没有选中任何目录", "err"); return; }
  $("create").disabled = true;
  setStatus("正在创建…");
  try {
    const body = Object.assign({
      snapshot: $("snapshot").value.trim(),
      output,
      rootName: $("rootName").value.trim(),
      createRoot: $("createRoot").checked,
      create: true,
      selected: Array.from(selected),
    }, namingPayload());
    const data = await post("/api/build", body);
    if (!data.ok) { setStatus("出错：" + data.error, "err"); return; }
    $("out").textContent = data.dirs.join("\n");
    $("outCount").textContent = "（" + data.count + " 个）";
    $("outWrap").hidden = false;
    if (data.createError) {
      setStatus("创建中断：" + data.createError, "err");
    } else {
      setStatus(`已创建 ${data.created} 个目录，位置：${data.output}`, "ok");
    }
  } catch (err) {
    setStatus("请求失败：" + err.message, "err");
  } finally {
    $("create").disabled = false;
  }
});

$("copy").addEventListener("click", async () => {
  const text = $("out").textContent;
  if (!text) { setStatus("还没有可复制的内容，先创建目录", "err"); return; }
  try {
    await navigator.clipboard.writeText(text);
    setStatus("已复制到剪贴板", "ok");
  } catch {
    setStatus("复制失败，请手动选中", "err");
  }
});

$("snapshot").addEventListener("keydown", (e) => {
  if (e.key === "Enter") loadTree("default");
});

renderTree();

/* ---------------- 启动 ---------------- */

const params = new URLSearchParams(location.search);
if (params.get("snapshot")) {
  $("snapshot").value = params.get("snapshot");
  if (params.get("output")) $("output").value = params.get("output");
  if (params.get("autorun")) loadTree("default");
} else if (params.get("noscan") !== "1") {
  scan("");
}
