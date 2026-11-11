const $ = (id) => document.getElementById(id);

let treeData = [];
const collapsed = new Set();
const selected = new Set();
let allIds = [];

function layoutValue() {
  const el = document.querySelector('input[name="layout"]:checked');
  return el ? el.value : "sections";
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

function countSelected() {
  let n = 0;
  for (const id of selected) n++;
  return n;
}

function renderTree() {
  const host = $("tree");
  host.textContent = "";
  if (!treeData.length) {
    host.innerHTML = '<div class="empty">还没有载入课程。填好快照路径后点「载入课程」。</div>';
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
  $("countLabel").textContent = `已选 ${countSelected()} / ${allIds.length} 个目录`;
}

function setSubtree(node, on) {
  if (on) selected.add(node.id);
  else selected.delete(node.id);
  for (const child of node.children || []) setSubtree(child, on);
}

function findNode(nodes, id) {
  for (const n of nodes) {
    if (n.id === id) return n;
    const hit = findNode(n.children || [], id);
    if (hit) return hit;
  }
  return null;
}

async function callBuild(extra) {
  const snapshot = $("snapshot").value.trim();
  if (!snapshot) { setStatus("请先填写快照路径", "err"); return null; }
  const body = Object.assign({
    snapshot,
    output: $("output").value.trim(),
    rootName: $("rootName").value.trim(),
    layout: layoutValue(),
  }, namingPayload(), extra || {});
  const res = await fetch("/api/build", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return await res.json();
}

async function loadTree(keepSelection) {
  $("load").disabled = true;
  $("reload").disabled = true;
  setStatus("正在解析…");
  try {
    const data = await callBuild({});
    if (!data) return;
    if (!data.ok) { setStatus("出错：" + data.error, "err"); return; }
    treeData = data.tree || [];
    allIds = collectIds(treeData, []);
    if (!keepSelection) {
      selected.clear();
      for (const id of allIds) selected.add(id);
    } else {
      // drop ids that no longer exist, keep the rest
      const valid = new Set(allIds);
      for (const id of Array.from(selected)) if (!valid.has(id)) selected.delete(id);
    }
    const c = data.course || {};
    $("summary").textContent =
      `${c.title || ""} — 顶层 ${c.top_sections} / 子章节 ${c.subsections} / 活动 ${c.activities}`;
    renderTree();
    setStatus("已载入，共 " + allIds.length + " 个目录", "ok");
    $("out").textContent = "";
  } catch (err) {
    setStatus("请求失败：" + err.message, "err");
  } finally {
    $("load").disabled = false;
    $("reload").disabled = false;
  }
}

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
  if (!output) { setStatus("请先填写输出根目录", "err"); return; }
  if (!selected.size) { setStatus("还没有选中任何目录", "err"); return; }
  $("create").disabled = true;
  setStatus("正在创建…");
  try {
    const data = await callBuild({ create: true, selected: Array.from(selected) });
    if (!data) return;
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

// AUTOLOAD: lets automation pre-fill the snapshot path via the query string.
//   ?snapshot=<path>&autorun=1
const params = new URLSearchParams(location.search);
if (params.get("snapshot")) {
  $("snapshot").value = params.get("snapshot");
  if (params.get("output")) $("output").value = params.get("output");
  if (params.get("autorun")) loadTree(false);
}
