const $ = (id) => document.getElementById(id);

let treeData = [];
const collapsed = new Set();
const selected = new Set();
let allIds = [];

function setStatus(text, kind) {
  const el = $("status");
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

/* ---------- 扫描与文件选择 ---------- */

function humanSize(bytes) {
  if (bytes > 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + " MB";
  if (bytes > 1024) return Math.round(bytes / 1024) + " KB";
  return bytes + " B";
}

function renderFound(directory, files) {
  const host = $("found");
  host.textContent = "";
  $("scanInfo").textContent = directory || "";
  if (!files || !files.length) {
    host.innerHTML = '<div class="empty">这个目录里没有找到 .mhtml / .html 快照。换个目录试试。</div>';
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
    btn.addEventListener("click", () => {
      $("snapshot").value = file.path;
      loadTree(false);
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
    await loadTree(false);
  } catch (err) {
    setStatus("打开对话框失败：" + err.message, "err");
  } finally {
    $("browseFile").disabled = false;
  }
}

async function pickDir(targetInput, label) {
  const data = await post("/api/dialog", { kind: "dir" });
  if (data.error) { setStatus("无法打开对话框：" + data.error, "err"); return null; }
  if (!data.ok || !data.path) return null;
  if (targetInput) targetInput.value = data.path;
  if (label) label.textContent = data.path;
  return data.path;
}

/* ---------- 树渲染 ---------- */

function setSubtree(node, on) {
  if (on) selected.add(node.id);
  else selected.delete(node.id);
  for (const child of node.children || []) setSubtree(child, on);
}

function renderTree() {
  const host = $("tree");
  host.textContent = "";
  if (!treeData.length) {
    host.innerHTML = '<div class="empty">还没有载入课程。点上面的「选择课程快照…」。</div>';
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
      cb.addEventListener("change", () => {
        setSubtree(node, cb.checked);
        renderTree();
      });
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
      if (hasKids && !collapsed.has(node.id)) {
        li.appendChild(build(node.children, depth + 1));
      }
      ul.appendChild(li);
    }
    return ul;
  };

  host.appendChild(build(treeData, 0));
  $("countLabel").textContent = `已选 ${selected.size} / ${allIds.length} 个目录`;
}

async function loadTree(keepSelection, snapshotPath) {
  const snapshot = snapshotPath || $("snapshot").value.trim();
  if (!snapshot) { setStatus("请先选择或填写快照路径", "err"); return; }
  setStatus("正在解析…");
  try {
    const data = await post("/api/build", Object.assign({
      snapshot,
      output: $("output").value.trim(),
      rootName: $("rootName").value.trim(),
    }, namingPayload()));
    if (!data.ok) { setStatus("出错：" + data.error, "err"); return; }
    treeData = data.tree || [];
    allIds = collectIds(treeData, []);
    if (!keepSelection) {
      selected.clear();
      for (const id of allIds) selected.add(id);
    } else {
      const valid = new Set(allIds);
      for (const id of Array.from(selected)) if (!valid.has(id)) selected.delete(id);
    }
    const c = data.course || {};
    $("summary").textContent =
      `${c.title || ""} — 顶层 ${c.top_sections} / 子章节 ${c.subsections} / 活动 ${c.activities}`;
    renderTree();
    setStatus(`已载入 ${allIds.length} 个目录`, "ok");
    $("out").textContent = "";
  } catch (err) {
    setStatus("请求失败：" + err.message, "err");
  }
}

/* ---------- 事件绑定 ---------- */

$("browseFile").addEventListener("click", pickFile);
$("browseDir").addEventListener("click", async () => {
  const dir = await pickDir(null, null);
  if (dir) scan(dir);
});
$("browseOut").addEventListener("click", () => pickDir($("output"), $("outInfo")));
$("rescan").addEventListener("click", () => scan($("scanInfo").textContent.trim()));
$("load").addEventListener("click", () => loadTree(false));
$("reload").addEventListener("click", () => loadTree(true));

$("expandAll").addEventListener("click", () => { collapsed.clear(); renderTree(); });
$("collapseAll").addEventListener("click", () => {
  for (const n of treeData) if (n.children && n.children.length) collapsed.add(n.id);
  renderTree();
});
$("selectAll").addEventListener("click", () => {
  for (const id of allIds) selected.add(id);
  renderTree();
});
$("selectNone").addEventListener("click", () => {
  selected.clear();
  renderTree();
});
$("selectSections").addEventListener("click", () => {
  selected.clear();
  const walk = (nodes) => {
    for (const n of nodes) {
      if (n.kind === "section") selected.add(n.id);
      walk(n.children || []);
    }
  };
  walk(treeData);
  renderTree();
});

$("create").addEventListener("click", async () => {
  const output = $("output").value.trim();
  if (!output) { setStatus("请先选择输出根目录", "err"); return; }
  if (!selected.size) { setStatus("还没有选中任何目录", "err"); return; }
  $("create").disabled = true;
  setStatus("正在创建…");
  try {
    const data = await post("/api/build", Object.assign({
      snapshot: $("snapshot").value.trim(),
      output,
      rootName: $("rootName").value.trim(),
      create: true,
      selected: Array.from(selected),
    }, namingPayload()));
    if (!data.ok) { setStatus("出错：" + data.error, "err"); return; }
    $("out").textContent = data.dirs.join("\n");
    if (data.createError) {
      setStatus("创建中断：" + data.createError, "err");
    } else {
      setStatus(`已创建 ${data.created} 个目录（选中 ${data.count} 个，共 ${data.totalCount} 个）`, "ok");
    }
  } catch (err) {
    setStatus("请求失败：" + err.message, "err");
  } finally {
    $("create").disabled = false;
  }
});

$("copy").addEventListener("click", async () => {
  const text = $("out").textContent;
  if (!text) { setStatus("还没有可复制的内容", "err"); return; }
  try {
    await navigator.clipboard.writeText(text);
    setStatus("已复制到剪贴板", "ok");
  } catch {
    setStatus("复制失败，请手动选中", "err");
  }
});

$("snapshot").addEventListener("keydown", (e) => {
  if (e.key === "Enter") loadTree(false);
});

renderTree();

/* ---------- 启动时自动扫描 ---------- */

const params = new URLSearchParams(location.search);
const autoScan = params.get("noscan") !== "1";
if (params.get("snapshot")) {
  $("snapshot").value = params.get("snapshot");
  if (params.get("output")) $("output").value = params.get("output");
  if (params.get("autorun")) loadTree(false);
} else if (autoScan) {
  scan("");
}
