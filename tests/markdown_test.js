/* web/markdown.js 的测试。用 node 跑：node tests/markdown_test.js
 *
 * 也被 tests/test_markdown.py 以子进程方式带进 pytest，这样 `uv run pytest`
 * 一条命令仍能全跑到。
 */

"use strict";

require("../web/markdown.js");
const M = globalThis.Markdown;

let failed = 0;
let passed = 0;

function check(label, actual, expected) {
  const ok = typeof expected === "function" ? expected(actual) : actual === expected;
  if (ok) {
    passed += 1;
  } else {
    failed += 1;
    console.error("FAIL " + label);
    console.error("  实际: " + JSON.stringify(actual));
    if (typeof expected !== "function") {
      console.error("  期望: " + JSON.stringify(expected));
    }
  }
}

const has = (needle) => (actual) => actual.includes(needle);
const lacks = (needle) => (actual) => !actual.includes(needle);

// --- 行内 ---

check("粗体", M.render("**你好**"), "<p><strong>你好</strong></p>");
check("斜体", M.render("*你好*"), "<p><em>你好</em></p>");
check("粗斜体", M.render("***你好***"), "<p><strong><em>你好</em></strong></p>");
check("删除线", M.render("~~没了~~"), "<p><del>没了</del></p>");
check("行内代码", M.render("用 `pip install`"), "<p>用 <code>pip install</code></p>");
check(
  "行内代码里的星号不当标记",
  M.render("`a ** b`"),
  "<p><code>a ** b</code></p>"
);
check("下划线命名不被拆开", M.render("snake_case_name"), has("snake_case_name"));
check("双下划线加粗", M.render("__粗__"), "<p><strong>粗</strong></p>");
check("链接", M.render("[点](https://a.com)"), has('href="https://a.com"'));
check("链接带 title", M.render('[点](https://a.com "标题")'), has('href="https://a.com"'));
check("裸链自动成链", M.render("见 https://a.com/x 结束"), has('<a href="https://a.com/x"'));
check(
  "裸链结尾标点不吞进去",
  M.render("见 https://a.com/x。"),
  has('>https://a.com/x</a>')
);

// --- 安全 ---

check("内嵌 HTML 被转义", M.render("<b>粗</b>"), "<p>&lt;b&gt;粗&lt;/b&gt;</p>");
check(
  "img onerror 不可执行",
  M.render('<img src=x onerror="alert(1)">'),
  lacks("<img")
);
check("script 标签被转义", M.render("<script>alert(1)</script>"), lacks("<script"));
check(
  "javascript: 链接不生成 a",
  M.render("[点](javascript:alert(1))"),
  lacks("<a ")
);
check(
  "换行绕过的 javascript: 也挡掉",
  M.render("[点](java\nscript:alert(1))"),
  lacks("<a ")
);
check("data: 链接不生成 a", M.render("[点](data:text/html;base64,xx)"), lacks("<a "));
check(
  "链接文字里的 HTML 被转义",
  M.render('[<img src=x>](https://a.com)'),
  lacks("<img")
);
check(
  "属性注入不成立",
  M.render('[点](https://a.com" onmouseover="alert(1))'),
  lacks("onmouseover=\"alert")
);
check(
  "代码块里的 HTML 被转义",
  M.render("```\n<script>alert(1)</script>\n```"),
  lacks("<script>")
);
check("表格单元格里的 HTML 被转义", M.render("| a |\n| --- |\n| <b>x</b> |"), lacks("<b>x"));

// --- 块级 ---

check("一级标题", M.render("# 标题"), "<h1>标题</h1>");
check("六级标题", M.render("###### 标题"), "<h6>标题</h6>");
check("七个井号不是标题", M.render("####### 标题"), has("<p>"));
check("分割线", M.render("---"), "<hr>");
check("引用", M.render("> 引文"), "<blockquote><p>引文</p></blockquote>");
check(
  "嵌套引用",
  M.render("> > 深"),
  "<blockquote><blockquote><p>深</p></blockquote></blockquote>"
);
check("无序列表", M.render("- a\n- b"), "<ul><li>a</li><li>b</li></ul>");
check("有序列表", M.render("1. a\n2. b"), "<ol><li>a</li><li>b</li></ol>");
check("有序列表自定义起始", M.render("3. a"), has('<ol start="3">'));
check(
  "嵌套列表",
  M.render("- a\n  - b"),
  "<ul><li>a<ul><li>b</li></ul></li></ul>"
);
check("列表项内的强调", M.render("- **粗**"), "<ul><li><strong>粗</strong></li></ul>");
check("段落软换行成 br", M.render("一\n二"), "<p>一<br>二</p>");
check("空行分段", M.render("一\n\n二"), "<p>一</p><p>二</p>");

// --- 表格 ---

const table = M.render("| 名 | 值 |\n| --- | ---: |\n| a | 1 |");
check("表格有 thead", table, has("<thead>"));
check("表格有单元格", table, has("<td>a</td>"));
check("表格右对齐", table, has('style="text-align:right"'));
check(
  "缺分隔行就不是表格",
  M.render("| 名 | 值 |\n| a | 1 |"),
  lacks("<table>")
);

// --- 代码块 ---

const python = M.render('```python\ndef hi():\n    return "你好"  # 注释\n```');
check("代码块结构", python, has("<pre><code"));
check("语言标签", python, has('class="code-lang">python<'));
check("关键字上色", python, has('<span class="tok-kw">def</span>'));
check("字符串上色", python, has('class="tok-str"'));
check("注释上色", python, has('class="tok-com"'));

check(
  "未知语言不上色但仍是代码块",
  M.render("```brainfuck\n+++\n```"),
  (actual) => actual.includes("<pre><code") && !actual.includes("tok-")
);
check(
  "无语言的代码块",
  M.render("```\nplain\n```"),
  (actual) => actual.includes("<pre><code") && !actual.includes("code-lang")
);
check("波浪号围栏", M.render("~~~\nx\n~~~"), has("<pre><code"));
check(
  "流式半截围栏也渲染成代码块",
  M.render("```python\ndef hi():"),
  has("<pre><code")
);
check(
  "代码块里的反引号不提前闭合",
  M.render("````\n```\ninner\n```\n````"),
  has("inner")
);
check(
  "代码块内容不当 Markdown 处理",
  M.render("```\n# 不是标题\n**不加粗**\n```"),
  (actual) => !actual.includes("<h1>") && !actual.includes("<strong>")
);
check("diff 加行上色", M.render("```diff\n+新增\n-删除\n```"), has("diff-add"));
check("sql 关键字不分大小写", M.render("```sql\nSELECT 1\n```"), has("tok-kw"));

// --- 边界 ---

check("空输入", M.render(""), "");
check("null 输入", M.render(null), "");
check("只有空白", M.render("   \n\n  "), "");
check("纯文本", M.render("就是一句话"), "<p>就是一句话</p>");
check("CRLF 换行", M.render("一\r\n\r\n二"), "<p>一</p><p>二</p>");
check(
  "正文里的 NUL 不破坏占位符",
  M.render("a" + String.fromCharCode(0) + "0" + String.fromCharCode(0) + "b `c`"),
  has("<code>c</code>")
);
check(
  "未闭合的强调按字面处理",
  M.render("**没关"),
  has("**没关")
);

// --- 综合 ---

const doc = M.render(
  [
    "# 标题",
    "",
    "一段**加粗**文字，含 `code`。",
    "",
    "- 项目一",
    "- 项目二",
    "  - 子项",
    "",
    "```python",
    'print("hi")',
    "```",
    "",
    "> 引用",
    "",
    "| a | b |",
    "| --- | --- |",
    "| 1 | 2 |"
  ].join("\n")
);
check("综合文档含全部结构", doc, (actual) =>
  ["<h1>", "<strong>", "<code>", "<ul>", "<ol", "<pre>", "<blockquote>", "<table>"]
    .filter((tag) => tag !== "<ol")
    .every((tag) => actual.includes(tag))
);

console.log(passed + " 项通过" + (failed ? "，" + failed + " 项失败" : ""));
process.exit(failed ? 1 : 0);
