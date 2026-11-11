const $ = (id) => document.getElementById(id);

function payload(create) {
  const layout = document.querySelector('input[name="layout"]:checked').value;
  return {
    snapshot: $("snapshot").value.trim(),
    output: $("output").value.trim(),
    rootName: $("rootName").value.trim(),
    layout,
    skipEmptySubsections: $("skipEmpty").checked,
    number: $("number").checked,
    collapseSpaces: $("collapseSpaces").checked,
    spaceReplace: $("spaceReplace").value,
    illegalReplace: $("illegalReplace").value,
    dropTrailing: $("dropTrailing").checked,
    create: !!create,
  };
}

function setStatus(text, kind) {
  const el = $("status");
  el.textContent = text || "";
  el.className = "status" + (kind ? " " + kind : "");
}

function render(data) {
  if (!data.ok) {
    setStatus("出错：" + data.error, "err");
    $("tree").textContent = "";
    $("summary").textContent = "";
    return;
  }
  const c = data.course || {};
  $("summary").textContent =
    `${c.title || ""} — 顶层 ${c.top_sections} / 子章节 ${c.subsections} / 活动 ${c.activities} — ` +
    `本次 ${data.count} 个目录，顶层文件夹 "${data.rootName}"`;
  $("tree").textContent = data.dirs.join("\n");
  if (data.createError) {
    setStatus("目录创建中断：" + data.createError, "err");
  } else if (data.created > 0) {
    setStatus(`已创建 ${data.created} 个目录（共 ${data.count} 个）`, "ok");
  } else if (data.created === 0 && data.count) {
    setStatus("目录已存在，无需创建", "ok");
  } else {
    setStatus(`预览：${data.count} 个目录（未落盘）`, "ok");
  }
}

async function run(create) {
  const body = payload(create);
  if (!body.snapshot) { setStatus("请先填写快照路径", "err"); return; }
  if (create && !body.output) { setStatus("创建目录需要填写输出根目录", "err"); return; }
  const buttons = [$("preview"), $("create")];
  buttons.forEach((b) => (b.disabled = true));
  setStatus(create ? "正在创建…" : "正在解析…");
  try {
    const res = await fetch("/api/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    render(await res.json());
  } catch (err) {
    setStatus("请求失败：" + err.message, "err");
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

$("preview").addEventListener("click", () => run(false));
$("create").addEventListener("click", () => run(true));

$("copy").addEventListener("click", async () => {
  const text = $("tree").textContent;
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    setStatus("已复制到剪贴板", "ok");
  } catch {
    setStatus("复制失败，请手动选中", "err");
  }
});

// Enter in the snapshot field triggers a preview.
$("snapshot").addEventListener("keydown", (e) => {
  if (e.key === "Enter") run(false);
});
