# xjtlu-lmc-downloader

XJTLU **Learning Mall Core（LMC，Moodle）** 课程内容归档工具：把课程页里的文件、页面快照
批量下载到本地，并**保持课程原本的目录结构**。

> 当前状态：仓库脚手架 + 第一个可用组件 `build-dir-tree`（目录结构还原）已就绪。
> 下载器本体尚在规划中，见下方[路线图](#路线图)。

---

## 这个仓库要解决什么

从 LMC 手动下载课程资料，有两个绕不开的痛点：

1. **结构丢失**。LMC 里内容是「章节 → 子章节 → 活动」的树形结构，
   但不管是逐个点开保存，还是用批量下载，落到本地都变成一堆平铺文件。
   `Week 3` 的讲义和 `Week 5` 的 workbook 混在一起，Lab 编号跳号也看不出原因。

2. **网页快照另存为 .mhtml 时，同样只剩一层平铺文件**，页面里的层级关系只存在于 HTML DOM 中。

本项目的目标是把这两件事一起解决：**先还原课程的真实目录结构，再把内容按这个结构落盘**。

## 组件

| 组件 | 作用 | 状态 |
|---|---|---|
| [`build-dir-tree/`](build-dir-tree/) | 解析课程页快照（`.mhtml` / `.html`），还原章节/子章节/活动层级，生成目录骨架、JSON 规格或 PowerShell 脚本；含本地图形界面 | ✅ 可用 |
| `lmc-crawler/` | 登录态复用、抓取课程页、枚举活动与附件真实地址 | 🚧 规划中 |
| `lmc-fetch/` | 按结构批量下载文件与页面快照，断点续传、去重 | 🚧 规划中 |

`build-dir-tree` **零第三方依赖**（Python 3.8+ 标准库）。先装一次，之后任何目录可用：

```bash
git clone https://github.com/zhuhaoxiangAndy/xjtlu-lmc-downloader.git
cd xjtlu-lmc-downloader/build-dir-tree
python -m pip install -e .

python -m build_dir_tree "course.mhtml" --summary
python -m build_dir_tree "course.mhtml" --layout full --mkdir -o ./out
build-dir-tree-ui                                   # 图形界面
```

不想装也行，在 `build-dir-tree` 目录里 `python run.py "course.mhtml" --summary` 即可。

详见 [build-dir-tree/README.md](build-dir-tree/README.md)。

## 设计取向

- **离线优先**。`build-dir-tree` 只读页面 DOM，不调用 Moodle Web Service、不碰 cookie，
  拿到快照就能跑。下载器部分才需要登录态，且凭证只留在本机、不入库。
- **结构先于内容**。先把目录树定下来，后续任何下载器、迁移脚本都能复用同一份规格（JSON）。
- **不做魔法**。课程页怎么排，本地就怎么排。`Lab` 和 `Week` 在 LMC 里本就是同级混排，
  工具不会自作主张重新归类。
- **名字不改写**。目录名直接取课程页标题，逐字符保留 —— 1 个空格、2 个空格、`&`、括号、`[]`
  都不动，Windows 存不了的字符默认直接删除。本地看到的和网页上看到的一致。
  要不要替换成别的、替换成什么，由使用者指定，工具不预设。

## 路线图

- [x] 从页面快照还原课程结构（`build-dir-tree`）
- [x] 两种布局：章节骨架 / 逐活动展开
- [x] 目录名逐字符保留原始标题，仅处理 Windows 非法字符
- [x] 可选加工：序号前缀、空格压缩、空格替换、非法字符替换（默认全部关闭）
- [x] 本地图形界面（`python -m build_dir_tree.ui`），不联网、不上传
- [x] 零安装启动脚本 `run.py`，不想装包时可直接跑
- [x] 四种输出：终端预览、目录落盘、PowerShell 脚本、JSON 规格
- [ ] 抓取层：复用浏览器登录态，拉取课程页并保存为标准快照
- [ ] 枚举层：解析每个 `resource` / `folder` / `url` 活动的真实下载地址
- [ ] 下载层：按结构落盘，支持断点续传与内容哈希去重
- [ ] 增量归档：对比上次结果，只拉新增或变更的内容
- [ ] 归档清单：生成 `manifest.json`，记录每个文件对应的活动、章节与原始 URL

## 使用须知

- 只下载**你有权限访问**的课程内容；账号权限范围之外的内容，本工具不会也不应绕过。
- 归档资料的使用请遵守学校规定与课程教师的要求，不要公开传播受版权保护的材料。
- 仓库内不存放任何课程内容、账号凭证或 cookie；如需要凭证，运行时从环境变量读取。

## 许可证

MIT
