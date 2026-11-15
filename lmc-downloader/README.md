# lmc-downloader

把 XJTLU **Learning Mall Core（LMC，Moodle）** 课程按原本的目录结构归档到本地：
枚举章节与活动、解析每个文件的真实下载地址、断点续传、内容哈希去重、生成归档清单。

零第三方依赖，只用 Python 3.8+ 标准库。

> 本包是 [xjtlu-lmc-downloader](../README.md) 的下载器组件。目录结构由
> [`build-dir-tree`](../build-dir-tree/) 负责还原；两者共用同一套命名规则，
> 因此下载出来的文件会落在已经建好的目录里。

---

## 它解决什么

`build-dir-tree` 只解决「结构」，文件还得有人搬下来。手动搬会遇到四件事：

1. **活动页不是文件页**。`mod/resource/view.php?id=N` 返回 302 跳到
   `pluginfile.php/...`，直接把活动页存下来只会得到一个网页。
2. **同名不同节**。同一份 workbook 同时挂在 `Week 5` 和 `Week 5 Tutorial` 下，
   盲目去重会丢掉其中一个章节的记录，不去重又会出现两份一模一样的大文件。
3. **重跑要能续**。课程更新后只想拉变化的部分，不想把 100 MB 重新下一遍。
4. **文件名不可信**。服务器给的名字可能为空、可能只有 `download`，扩展名要靠 MIME 推。

这四件事分别对应 `crawler` 的解析规则、`fetcher` 的去重策略、`manifest` 的增量比对、
`names` 的命名回退链。

## 安装

```bash
git clone https://github.com/zhuhaoxiangAndy/xjtlu-lmc-downloader.git
cd xjtlu-lmc-downloader/lmc-downloader
python -m pip install .
```

要改代码就用可编辑安装（注意：可编辑安装**不能移动目录**）：

```bash
python -m pip install -e .
```

> 想读 `.mhtml` 快照离线分析，再装 `build-dir-tree`：
> `python -m pip install ../build-dir-tree`。
> 只下载线上课程则**不需要**它 —— 实时课程页由内置解析器处理。

## 准备登录状态

下载需要登录态。**不要把 cookie 写进仓库**，三种方式任选：

```bash
# 1) 浏览器扩展导出的 Netscape cookies.txt
python -m lmc_downloader download 1213 --cookies cookies.txt

# 2) 直接给 Cookie 头（注意别留在 shell 历史里）
python -m lmc_downloader download 1213 --cookies "MoodleSession=abc; MOODLEID1_=xyz"

# 3) 环境变量，避免出现在命令行参数与历史记录中
set LMC_COOKIES=D:\path\to\cookies.txt
python -m lmc_downloader download 1213
```

本仓库自带一个导出脚本，从已登录的 Chromium 调试端口读取 cookie：

```bash
python scripts/export_cookies.py --port 9333 --out ..\cookies.txt
```

Moodle 会在请求过程中轮换 `MoodleSession`。本工具把每个响应的 `Set-Cookie`
写回 cookie 存储，所以长时间抓取不会中途掉回访客身份；一旦被重定向到登录页，
会立刻报 `session expired` 而不是把登录页当成文件存下来。

## 使用

先看清楚课程里有什么，不下载任何东西：

```bash
python -m lmc_downloader outline 1213 --cookies cookies.txt
```

```
course  : Course: ISA104-2627-S1-AI Application Design | LM Core
id      : 1213
sections: 20
activity: 68
types   : assign=2, folder=2, forum=1, h5pactivity=7, resource=36, url=9

[Week 1 - Introduction to AI and GAI]
   resource       [LECTURE] Introduction to AI and GenAI
   resource       [EXTRA-READING] GPT-4 Technical Report
   url            [RECORDING] L1 Introduction to AI and GAI
```

下载全部，落到指定课程目录：

```bash
python -m lmc_downloader download 1213 --cookies cookies.txt --root "D:\LMC\ISA104"
```

```
[2/49] OK    resource  ISA104 ICT Task Sheet
            100.9 KiB  D:\LMC\ISA104\In-class Test\ISA104 ICT Task Sheet\....pdf
[3/49] same  resource  Week 3 Workbook
summary : failed=1, skipped=2, downloaded=46; 121.0 MiB on disk
manifest: D:\LMC\ISA104\manifest.json
```

只拉 Office 文档：

```bash
python -m lmc_downloader download 1213 --cookies cookies.txt ^
    --root "D:\LMC\ISA104" --ext office
```

先看会做什么、不落盘：

```bash
python -m lmc_downloader download 1213 --cookies cookies.txt ^
    --root "D:\LMC\ISA104" --dry-run
```

### 参数

| 参数 | 作用 |
|---|---|
| `--root DIR` | 归档根目录，默认 `<当前目录>/LMC/<课程号>` |
| `--ext LIST` | 扩展名白名单，逗号分隔；`office` 等于 pdf/doc/docx/ppt/pptx/xls/xlsx |
| `--types LIST` | 只处理这些活动类型，默认 `resource,folder,url,assign` |
| `--dry-run` | 只解析与报告，不写任何文件 |
| `--no-dedupe` | 相同内容也按活动各存一份（默认去重） |
| `--limit N` | 只处理前 N 个可下载项 |
| `--snapshot FILE` | 用保存的 `.mhtml`/`.html` 解析结构，不发课程页请求 |
| `--manifest FILE` | 自定义清单路径，默认 `<root>/manifest.json` |

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 全部成功 |
| 1 | 用法错误、cookie 无法读取、课程页无法解析 |
| 2 | 跑完了但有项目失败，或登录态失效（已落盘的部分会保留） |

## 目录布局与清单

```
D:\LMC\ISA104\
├── Week 1 - Introduction to AI and GAI\
│   └── [LECTURE] Introduction to AI and GenAI\
│       └── L1_Intro_to_AI-GAI.pdf
├── In-class Test\
│   └── ISA104 ICT Task Sheet\
│       └── ISA104_Assessment1_Task_Sheet_and_MarkingScheme.pdf
└── manifest.json
```

`manifest.json` 记录每一项的章节、活动、原始活动页 URL、真实文件 URL、
字节数、SHA-256 与结果状态（`downloaded` / `skipped` / `duplicate` /
`adopted` / `link` / `failed`）。

### 增量归档怎么判定

一项被跳过，必须同时满足三条：

1. 清单里有它的记录；
2. 磁盘上的文件大小与 SHA-256 仍与记录一致（本地被改动会重下）；
3. 抓取阶段得知的**远端**大小与记录一致（课程更新了同名文件会重下）。

只有第 2 条时，远端换了文件就永远拉不到新版；只有第 3 条时，本地被截断也发现不了。

### 去重与记录如何两全

默认 `--dedupe`：同一份上传被多个活动引用时，磁盘只留一份，
其余活动在清单里标 `status: duplicate` 并写明 `duplicate_of` 指向那份文件。
这样既不多占空间，也不会丢掉「Week 5 Tutorial 里也挂着这份 workbook」这个事实。
想每个活动各自留一份就用 `--no-dedupe`。

## 已知边界

| 情况 | 行为 | 原因 |
|---|---|---|
| 文件夹里只有子文件夹 | 标记失败并在报告里说明 | Moodle 的 `download_folder.php` 对这种文件夹直接 404「Cannot download this folder」，服务端不提供打包 |
| 资源本身是 HTML | 保存为 `.html` | 打开后是 Moodle 的 iframe 查看器，真实文件就是那个 HTML |
| `url` 活动 | 只记录链接，不下载 | 目标可能是视频平台或需要单独授权的页面 |
| 权限外的活动 | 记录 `HTTP 403/404` 并继续 | 不尝试绕过课程权限 |
| 中文/长文件名 | 逐字符保留，仅替换 Windows 非法字符 | 与 `build-dir-tree` 的命名规则一致 |

## 作为库使用

```python
from lmc_downloader import CookieStore, Crawler, Fetcher, HttpClient
from lmc_downloader.crawler import parse_course_html
from lmc_downloader.session import load_cookies

client = HttpClient(CookieStore(load_cookies("cookies.txt")),
                    allowed_hosts={"core.xjtlu.edu.cn"})
page = client.get("https://core.xjtlu.edu.cn/course/view.php?id=1213")
outline = parse_course_html(page.text(), course_id="1213")

crawler = Crawler(client)
fetcher = Fetcher(client, root="D:/LMC/ISA104")
for activity in outline.activities:
    for ref in crawler.resolve(activity):
        fetcher.fetch(ref)
fetcher.manifest.save()
```

## 测试

```bash
cd lmc-downloader
python -m unittest discover -s tests -v
```

64 个用例全部离线运行：用假 HTTP 层替代网络，因此不需要账号、不需要 cookie，
结果可复现。覆盖命名规则、Content-Disposition 解析、cookie 解析与轮换、
重定向与登录失效判定、四类活动的解析、首次下载/增量跳过/去重/接管已有文件/
失败报告、清单往返，以及 CLI 的成功与失败退出码。

## 使用须知

- 只下载**你有权限访问**的课程内容；账号权限之外的内容，本工具不会也不应绕过。
- 仓库内不存放任何课程内容、账号凭证或 cookie；凭证只在运行时从文件或环境变量读取。
- 归档资料的使用请遵守学校规定与课程教师的要求，不要公开传播受版权保护的材料。

## 许可证

MIT
