/* 极简 Markdown 渲染器 —— 零依赖，浏览器与 Node 都能加载。
 *
 * 为什么自己写而不引 marked.js：这个应用全程离线可用、没有构建步骤（连字体都
 * 刻意不走 CDN），自己写一份覆盖聊天常用语法的实现就够了。
 *
 * 安全前提：**输入是模型输出，属于不可信文本**，而渲染结果要进 innerHTML。因此
 *   1. 任何文本片段进入输出前一律 escapeHtml()
 *   2. 只输出本文件自己生成的标签，刻意**不支持内嵌 HTML**——模型吐出
 *      `<img onerror=...>` 会显示成字面文本，而不是被浏览器执行
 *   3. 链接 scheme 走白名单，javascript: 之类降级成纯文本
 *   4. 语法高亮在**原始**代码上分词，每个 token 各自转义后再拼 span；不在已转义
 *      的字符串上跑正则，免得把 &lt; 这类实体切坏
 *
 * 导出 globalThis.Markdown = { render, renderInline, highlight, escapeHtml }
 */

(function (root) {
  "use strict";

  // --- 转义 ---

  const ESCAPES = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  };

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (c) => ESCAPES[c]);
  }

  // 行内代码的占位符边界。用 NUL：正常 Markdown 正文里不会出现，最不容易撞车
  const MARK = String.fromCharCode(0);

  /** 只允许这些 scheme 生成链接；其余（javascript:、data: 等）保持纯文本。 */
  const SAFE_URL = /^(?:https?:\/\/|mailto:|#|\/)/i;

  // 裸链结尾的标点不该被吞进 URL。中文正文常以全角标点收尾，所以两套都要管
  const TRAILING_PUNCT = /[.,;:!?'"、。，；：！？）〕】》」』]+$/;

  /** 去掉空白与所有控制字符，防 "java(换行)script:alert(1)" 这类绕过。 */
  function stripBlank(text) {
    let out = "";
    for (const ch of String(text)) {
      if (ch.charCodeAt(0) > 32) out += ch;
    }
    return out;
  }

  function safeUrl(url) {
    const cleaned = stripBlank(url);
    return SAFE_URL.test(cleaned) ? cleaned : null;
  }

  function unescapeEntities(text) {
    return text
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/&amp;/g, "&");
  }

  // --- 行内 ---

  /** 粗体 / 斜体 / 删除线。输入必须已转义，这里只负责加标签。 */
  function emphasize(text) {
    return text
      .replace(/\*\*\*(?!\s)([\s\S]*?[^\s*])\*\*\*/g, "<strong><em>$1</em></strong>")
      .replace(/\*\*(?!\s)([\s\S]*?[^\s*])\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^\w*])\*(?!\s)([\s\S]*?[^\s*])\*(?![\w*])/g, "$1<em>$2</em>")
      .replace(/~~(?!\s)([\s\S]*?[^\s~])~~/g, "<del>$1</del>")
      // 下划线形式要求两侧是非单词字符，否则 snake_case_name 会被拆开
      .replace(/(^|[^\w])__(?!\s)([\s\S]*?[^\s_])__(?!\w)/g, "$1<strong>$2</strong>")
      .replace(/(^|[^\w])_(?!\s)([\s\S]*?[^\s_])_(?!\w)/g, "$1<em>$2</em>");
  }

  /** 行内渲染。行内代码先抠成占位符，免得里面的 * _ [ ] 被当成标记。 */
  function renderInline(text) {
    const codes = [];
    let out = String(text).replace(/(`+)([\s\S]*?)\1/g, (_, __, code) => {
      codes.push("<code>" + escapeHtml(code.replace(/^ | $/g, "")) + "</code>");
      return MARK + (codes.length - 1) + MARK;
    });

    out = escapeHtml(out);

    // [文字](链接)，可带 "title"。文字里的强调递归处理，链接过白名单
    out = out.replace(
      /\[([^\]]*)\]\(\s*([^\s)]+?)(?:\s+&quot;[^&]*&quot;)?\s*\)/g,
      (whole, label, url) => {
        const href = safeUrl(unescapeEntities(url));
        if (!href) return whole;
        return (
          '<a href="' +
          escapeHtml(href) +
          '" target="_blank" rel="noopener noreferrer">' +
          emphasize(label) +
          "</a>"
        );
      }
    );

    out = emphasize(out);

    // 裸 URL 自动链接。前面必须是行首或空白，因此不会碰到上面刚生成的 href="…"
    out = out.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, (_, lead, url) => {
      const clean = url.replace(TRAILING_PUNCT, "");
      return (
        lead +
        '<a href="' +
        clean +
        '" target="_blank" rel="noopener noreferrer">' +
        clean +
        "</a>" +
        url.slice(clean.length)
      );
    });

    // 放回行内代码
    return out.replace(
      new RegExp(MARK + "(\\d+)" + MARK, "g"),
      (_, i) => codes[Number(i)]
    );
  }

  // --- 语法高亮 ---

  // 各语言只描述「注释怎么写、字符串怎么写、关键字有哪些」，分词逻辑共用一套。
  // 认不出的语言就只给等宽字体，不上色——宁可不高亮，也不要标错。
  const C_LIKE_STRINGS = ['"', "'"];
  const KW = {
    python:
      "and as assert async await break class continue def del elif else except finally " +
      "for from global if import in is lambda nonlocal not or pass raise return try " +
      "while with yield None True False self match case",
    javascript:
      "async await break case catch class const continue debugger default delete do else " +
      "export extends finally for function if import in instanceof let new of return " +
      "static super switch this throw try typeof var void while with yield null true " +
      "false undefined NaN interface type enum implements readonly public private as satisfies",
    json: "true false null",
    bash:
      "if then else elif fi for while do done case esac in function return local export " +
      "readonly declare source alias unset echo cd exit set trap",
    css: "important media import keyframes supports from to and not only",
    html: "",
    sql:
      "select from where insert into values update set delete create table drop alter add " +
      "primary key foreign references index view join left right inner outer on group by " +
      "order having limit offset distinct as and or not null is in between like union all " +
      "case when then else end begin commit rollback",
    go:
      "break case chan const continue default defer else fallthrough for func go goto if " +
      "import interface map package range return select struct switch type var nil true " +
      "false iota make new len cap append error string int bool byte rune",
    rust:
      "as async await break const continue crate dyn else enum extern false fn for if impl " +
      "in let loop match mod move mut pub ref return self Self static struct super trait " +
      "true type unsafe use where while Some None Ok Err String Vec Option Result",
    java:
      "abstract assert boolean break byte case catch char class const continue default do " +
      "double else enum extends final finally float for if implements import instanceof " +
      "int interface long native new package private protected public return short static " +
      "super switch synchronized this throw throws try void volatile while true false null var record",
    c:
      "auto break case char const continue default do double else enum extern float for " +
      "goto if inline int long register restrict return short signed sizeof static struct " +
      "switch typedef union unsigned void volatile while bool true false NULL " +
      "class namespace template typename public private protected virtual override new delete this nullptr using",
    yaml: "true false null yes no on off",
    toml: "true false"
  };

  const SPECS = {
    python: { line: ["#"], strings: ['"""', "'''", '"', "'"], kw: KW.python },
    javascript: { line: ["//"], block: ["/*", "*/"], strings: ['"', "'", "`"], kw: KW.javascript },
    json: { line: [], strings: ['"'], kw: KW.json },
    bash: { line: ["#"], strings: ['"', "'"], kw: KW.bash },
    css: { line: [], block: ["/*", "*/"], strings: C_LIKE_STRINGS, kw: KW.css },
    html: { line: [], block: ["<!--", "-->"], strings: C_LIKE_STRINGS, kw: KW.html },
    sql: { line: ["--"], block: ["/*", "*/"], strings: ["'", '"'], kw: KW.sql, nocase: true },
    go: { line: ["//"], block: ["/*", "*/"], strings: ['"', "'", "`"], kw: KW.go },
    rust: { line: ["//"], block: ["/*", "*/"], strings: ['"', "'"], kw: KW.rust },
    java: { line: ["//"], block: ["/*", "*/"], strings: C_LIKE_STRINGS, kw: KW.java },
    c: { line: ["//"], block: ["/*", "*/"], strings: C_LIKE_STRINGS, kw: KW.c },
    yaml: { line: ["#"], strings: C_LIKE_STRINGS, kw: KW.yaml },
    toml: { line: ["#"], strings: C_LIKE_STRINGS, kw: KW.toml }
  };

  const ALIASES = {
    py: "python",
    py3: "python",
    js: "javascript",
    jsx: "javascript",
    mjs: "javascript",
    cjs: "javascript",
    ts: "javascript",
    tsx: "javascript",
    typescript: "javascript",
    node: "javascript",
    sh: "bash",
    shell: "bash",
    zsh: "bash",
    console: "bash",
    scss: "css",
    less: "css",
    xml: "html",
    svg: "html",
    vue: "html",
    postgres: "sql",
    postgresql: "sql",
    mysql: "sql",
    sqlite: "sql",
    golang: "go",
    rs: "rust",
    kt: "java",
    kotlin: "java",
    cs: "c",
    csharp: "c",
    cpp: "c",
    "c++": "c",
    h: "c",
    hpp: "c",
    objc: "c",
    swift: "c",
    yml: "yaml",
    ini: "toml",
    cfg: "toml"
  };

  function escapeRe(text) {
    return text.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
  }

  const scanners = new Map();

  /** 把一份语言描述编译成一条扫描正则，按优先级：注释 → 字符串 → 数字 → 单词。 */
  function scannerFor(name, spec) {
    if (scanners.has(name)) return scanners.get(name);
    const parts = [];
    if (spec.block) {
      // 收尾符没等到就吃到结尾——流式输出里代码块常常是半截的
      parts.push(escapeRe(spec.block[0]) + "[\\s\\S]*?(?:" + escapeRe(spec.block[1]) + "|$)");
    }
    for (const prefix of spec.line) parts.push(escapeRe(prefix) + "[^\\n]*");
    for (const quote of spec.strings) {
      const q = escapeRe(quote);
      parts.push(q + "(?:\\\\[\\s\\S]|(?!" + q + ")[\\s\\S])*(?:" + q + "|$)");
    }
    parts.push("\\b\\d[\\w.]*");
    parts.push("[A-Za-z_$][\\w$]*");
    const scanner = new RegExp(parts.join("|"), "g");
    scanners.set(name, scanner);
    return scanner;
  }

  const keywordSets = new Map();

  function keywordsFor(name, spec) {
    if (!keywordSets.has(name)) {
      keywordSets.set(name, new Set(spec.kw.split(/\s+/).filter(Boolean)));
    }
    return keywordSets.get(name);
  }

  function classify(token, spec, keywords) {
    const head = token[0];
    if (spec.block && token.startsWith(spec.block[0])) return "com";
    for (const prefix of spec.line) if (token.startsWith(prefix)) return "com";
    for (const quote of spec.strings) if (token.startsWith(quote)) return "str";
    if (head >= "0" && head <= "9") return "num";
    const word = spec.nocase ? token.toLowerCase() : token;
    return keywords.has(word) ? "kw" : null;
  }

  /** diff 按行上色，与逐 token 分词是两套逻辑，单独处理。 */
  function highlightDiff(code) {
    return code
      .split("\n")
      .map((line) => {
        const cls =
          line.startsWith("+") && !line.startsWith("+++")
            ? "diff-add"
            : line.startsWith("-") && !line.startsWith("---")
              ? "diff-del"
              : line.startsWith("@@")
                ? "diff-hunk"
                : null;
        const text = escapeHtml(line);
        return cls ? '<span class="' + cls + '">' + text + "</span>" : text;
      })
      .join("\n");
  }

  /** 给一段代码上色，返回 HTML。分词跑在**原始**文本上，每个 token 各自转义。 */
  function highlight(code, language) {
    const key = String(language || "").toLowerCase();
    const name = ALIASES[key] || key;
    if (name === "diff" || name === "patch") return highlightDiff(code);

    const spec = SPECS[name];
    if (!spec) return escapeHtml(code);

    const scanner = scannerFor(name, spec);
    const keywords = keywordsFor(name, spec);
    scanner.lastIndex = 0;

    let out = "";
    let last = 0;
    let match;
    while ((match = scanner.exec(code)) !== null) {
      const token = match[0];
      if (!token) {
        scanner.lastIndex += 1;
        continue;
      }
      out += escapeHtml(code.slice(last, match.index));
      const cls = classify(token, spec, keywords);
      out += cls ? '<span class="tok-' + cls + '">' + escapeHtml(token) + "</span>" : escapeHtml(token);
      last = match.index + token.length;
    }
    return out + escapeHtml(code.slice(last));
  }

  // --- 块级 ---

  const FENCE = /^([ \t]*)(`{3,}|~{3,})[ \t]*([\w+#.-]*)[ \t]*$/;
  const HEADING = /^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*$/;
  const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
  const QUOTE = /^ {0,3}> ?(.*)$/;
  const BULLET = /^([ \t]*)([-*+])[ \t]+(.*)$/;
  const ORDERED = /^([ \t]*)(\d{1,9})[.)][ \t]+(.*)$/;
  const TABLE_SEP = /^[ \t]*\|?[ \t]*:?-{1,}:?[ \t]*(\|[ \t]*:?-{1,}:?[ \t]*)*\|?[ \t]*$/;

  function indentOf(text) {
    // Tab 按 4 空格算，只用于判断嵌套层级
    return text.replace(/\t/g, "    ").match(/^ */)[0].length;
  }

  function splitRow(line) {
    return line
      .trim()
      .replace(/^\||\|$/g, "")
      .split("|")
      .map((cell) => cell.trim());
  }

  function alignOf(spec) {
    const left = spec.startsWith(":");
    const right = spec.endsWith(":");
    if (left && right) return ' style="text-align:center"';
    if (right) return ' style="text-align:right"';
    if (left) return ' style="text-align:left"';
    return "";
  }

  /** 渲染一段块级内容。lines 是已按换行切好的数组。 */
  function renderBlocks(lines) {
    let out = "";
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      if (!line.trim()) {
        i += 1;
        continue;
      }

      // 围栏代码块。内容原样保留，不再经过任何 Markdown 规则
      const fence = line.match(FENCE);
      if (fence) {
        const [, pad, marker, language] = fence;
        const body = [];
        i += 1;
        while (i < lines.length) {
          const candidate = lines[i];
          // 同种符号、且不短于开栏才算收尾
          if (
            candidate.trim().startsWith(marker[0].repeat(marker.length)) &&
            /^[ \t]*(`{3,}|~{3,})[ \t]*$/.test(candidate)
          ) {
            i += 1;
            break;
          }
          body.push(candidate.startsWith(pad) ? candidate.slice(pad.length) : candidate);
          i += 1;
        }
        out += codeBlock(body.join("\n"), language);
        continue;
      }

      const heading = line.match(HEADING);
      if (heading) {
        const level = heading[1].length;
        out += "<h" + level + ">" + renderInline(heading[2]) + "</h" + level + ">";
        i += 1;
        continue;
      }

      if (RULE.test(line)) {
        out += "<hr>";
        i += 1;
        continue;
      }

      // 引用：连续的 > 行收成一段，去掉标记后递归解析
      if (QUOTE.test(line)) {
        const inner = [];
        while (i < lines.length && (QUOTE.test(lines[i]) || (inner.length && lines[i].trim()))) {
          const m = lines[i].match(QUOTE);
          inner.push(m ? m[1] : lines[i]);
          i += 1;
        }
        out += "<blockquote>" + renderBlocks(inner) + "</blockquote>";
        continue;
      }

      // 表格：表头 + 分隔行才算，否则当普通段落
      if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
        const header = splitRow(line);
        const aligns = splitRow(lines[i + 1]).map(alignOf);
        i += 2;
        let html = "<table><thead><tr>";
        header.forEach((cell, index) => {
          html += "<th" + (aligns[index] || "") + ">" + renderInline(cell) + "</th>";
        });
        html += "</tr></thead><tbody>";
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
          const cells = splitRow(lines[i]);
          html += "<tr>";
          for (let c = 0; c < header.length; c += 1) {
            html += "<td" + (aligns[c] || "") + ">" + renderInline(cells[c] || "") + "</td>";
          }
          html += "</tr>";
          i += 1;
        }
        out += html + "</tbody></table>";
        continue;
      }

      if (BULLET.test(line) || ORDERED.test(line)) {
        const [html, next] = renderList(lines, i);
        out += html;
        i = next;
        continue;
      }

      // 段落：吃到空行或下一个块级结构为止
      const paragraph = [];
      while (i < lines.length && lines[i].trim()) {
        const candidate = lines[i];
        if (
          paragraph.length &&
          (FENCE.test(candidate) ||
            HEADING.test(candidate) ||
            RULE.test(candidate) ||
            QUOTE.test(candidate) ||
            BULLET.test(candidate) ||
            ORDERED.test(candidate))
        ) {
          break;
        }
        paragraph.push(candidate.trim());
        i += 1;
      }
      // 段落内的换行保留成 <br>，聊天里模型经常靠软换行分行
      out += "<p>" + paragraph.map(renderInline).join("<br>") + "</p>";
    }

    return out;
  }

  /** 代码块。语言名只用于挑高亮规则与显示标签，不进 HTML 属性。 */
  function codeBlock(code, language) {
    const key = String(language || "").toLowerCase();
    const known = ALIASES[key] || key;
    const label = language ? '<span class="code-lang">' + escapeHtml(language) + "</span>" : "";
    const body = highlight(code.replace(/\n+$/, ""), language);
    return (
      '<div class="code-block">' +
      label +
      '<pre><code class="lang-' +
      escapeHtml(SPECS[known] || known === "diff" ? known : "plain") +
      '">' +
      body +
      "</code></pre></div>"
    );
  }

  /** 列表。按缩进递归嵌套，列表项内容再走一遍块级解析（因此项内可含代码块）。 */
  function renderList(lines, start) {
    const first = lines[start].match(BULLET) || lines[start].match(ORDERED);
    const ordered = !lines[start].match(BULLET);
    const baseIndent = indentOf(first[1]);
    const startNumber = ordered ? Number(first[2]) : 1;

    let i = start;
    const items = [];

    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) {
        // 空行后若不再是同级列表项，列表就结束了
        const next = lines[i + 1];
        if (!next || !next.trim()) break;
        const nextMatch = next.match(BULLET) || next.match(ORDERED);
        if (!nextMatch || indentOf(nextMatch[1]) < baseIndent) break;
        i += 1;
        continue;
      }

      const match = line.match(BULLET) || line.match(ORDERED);
      const indent = match ? indentOf(match[1]) : indentOf(line);

      if (match && indent === baseIndent && !!line.match(BULLET) === !ordered) {
        items.push([match[3]]);
        i += 1;
        continue;
      }
      if (indent > baseIndent || (!match && items.length)) {
        // 缩进更深或是延续行，都归到当前项里，去掉一层缩进后递归
        if (!items.length) break;
        items[items.length - 1].push(line.replace(/^ {1,4}|\t/, ""));
        i += 1;
        continue;
      }
      break;
    }

    const body = items.map((item) => "<li>" + tighten(renderBlocks(item)) + "</li>").join("");

    const tag = ordered ? "ol" : "ul";
    const attr = ordered && startNumber !== 1 ? ' start="' + startNumber + '"' : "";
    return ["<" + tag + attr + ">" + body + "</" + tag + ">", i];
  }

  /** 单段落的列表项去掉 <p> 包裹，列表才不会被撑得很松。
   *
   * 只在整项恰好只有一个 <p>、且它就在开头时才拆——「一段文字 + 嵌套子列表」属于
   * 这种情况，而真正有多段的项要保留 <p> 才排得开。
   */
  function tighten(html) {
    const count = (html.match(/<p>/g) || []).length;
    if (count !== 1 || !html.startsWith("<p>")) return html;
    return html.replace(/^<p>([\s\S]*?)<\/p>/, "$1");
  }

  /** 入口：把一段 Markdown 渲染成 HTML 字符串。 */
  function render(markdown) {
    if (!markdown) return "";
    const normalized = String(markdown)
      .replace(/\r\n?/g, "\n")
      // NUL 是行内代码占位符的边界，先清掉免得正文里的 NUL 干扰
      .split(MARK)
      .join("");
    return renderBlocks(normalized.split("\n"));
  }

  root.Markdown = { render, renderInline, highlight, escapeHtml, safeUrl, stripBlank };
})(typeof globalThis !== "undefined" ? globalThis : this);
