"""附件的表示与跨协议编码助手。

四套协议的多模态 payload 形状各不相同，但对附件的分类是同一套：图片走各家的
原生图片块，PDF 走各家的原生文档块，其余（源码、Markdown、JSON、CSV…）解码成
文本内联进正文。

之所以不把纯文本也塞进「文档」通路：四家里只有 PDF 是都原生认的文档类型，而
文本内联对所有模型都有效，也不依赖模型自己的文件解析能力。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import PurePosixPath

# 文本内联的字符上限。附件本身可以到 10 MB，整篇塞进 prompt 既烧 token 又容易
# 顶爆上下文，超出就截断并如实说明。
MAX_INLINE_CHARS = 200_000

# 明确按文本处理的 application/* 类型。SVG 虽是 image/*，但视觉模型基本不认，
# 给出源码反而有用，所以也归到这里。
TEXT_MIMES = frozenset(
    {
        "application/json",
        "application/ld+json",
        "application/xml",
        "application/xhtml+xml",
        "application/yaml",
        "application/x-yaml",
        "application/javascript",
        "application/x-javascript",
        "application/typescript",
        "application/toml",
        "application/sql",
        "application/graphql",
        "application/x-sh",
        "application/x-shellscript",
        "application/x-python",
        "application/x-python-code",
        "application/x-httpd-php",
        "image/svg+xml",
    }
)

# 浏览器对源码文件常报空 MIME 或 application/octet-stream，只靠 MIME 认不出来，
# 因此再按扩展名兜一层。
TEXT_EXTENSIONS = frozenset(
    {
        ".txt", ".text", ".md", ".markdown", ".rst", ".adoc", ".log", ".csv", ".tsv",
        ".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".toml", ".ini", ".cfg",
        ".conf", ".properties", ".env", ".editorconfig", ".gitignore", ".gitattributes",
        ".xml", ".html", ".htm", ".css", ".scss", ".sass", ".less", ".svg",
        ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts",
        ".py", ".pyi", ".pyw", ".rb", ".go", ".rs", ".java", ".kt", ".kts",
        ".c", ".h", ".cpp", ".cxx", ".cc", ".hpp", ".hxx", ".cs", ".swift",
        ".m", ".mm", ".php", ".pl", ".pm", ".lua", ".r", ".jl", ".scala", ".clj",
        ".ex", ".exs", ".erl", ".hs", ".ml", ".fs", ".fsx", ".dart", ".zig", ".nim",
        ".sh", ".bash", ".zsh", ".fish", ".ps1", ".psm1", ".bat", ".cmd",
        ".sql", ".graphql", ".gql", ".proto", ".thrift", ".avsc",
        ".vue", ".svelte", ".astro", ".tf", ".tfvars", ".hcl", ".gradle",
        ".dockerfile", ".containerfile", ".make", ".mk", ".cmake", ".patch", ".diff",
    }
)


@dataclass(frozen=True)
class Attachment:
    """一个随消息发出的附件。``data`` 是 base64 编码的原始字节。"""

    id: str
    name: str
    mime: str
    data: str

    @property
    def suffix(self) -> str:
        """小写扩展名（含点），没有则为空串。"""
        return PurePosixPath(self.name).suffix.lower()


def is_image(attachment: Attachment) -> bool:
    """能直接喂给视觉模型的位图。SVG 走文本通路，不算。"""
    return attachment.mime.startswith("image/") and attachment.mime != "image/svg+xml"


def is_pdf(attachment: Attachment) -> bool:
    return attachment.mime == "application/pdf" or attachment.suffix == ".pdf"


def is_text(attachment: Attachment) -> bool:
    """能解码成文本内联的附件。"""
    mime = attachment.mime.lower()
    if mime.startswith("text/") or mime in TEXT_MIMES:
        return True
    # 无扩展名的常见纯文本文件（Dockerfile、Makefile 之类）
    stem = PurePosixPath(attachment.name).name.lower()
    if not attachment.suffix and stem in {"dockerfile", "makefile", "rakefile", "gemfile"}:
        return True
    return attachment.suffix in TEXT_EXTENSIONS


def raw_bytes(attachment: Attachment) -> bytes:
    """解 base64；坏数据当空内容处理，不让一个附件把整轮对话打断。"""
    try:
        return base64.b64decode(attachment.data, validate=False)
    except (ValueError, TypeError):
        return b""


def data_url(attachment: Attachment) -> str:
    """OpenAI 两套协议要的 ``data:`` URL 形式。"""
    mime = attachment.mime or "application/octet-stream"
    return f"data:{mime};base64,{attachment.data}"


def decode_text(attachment: Attachment) -> str:
    """把文本类附件解码成字符串，超长则截断并注明。"""
    text = raw_bytes(attachment).decode("utf-8", "replace")
    if len(text) <= MAX_INLINE_CHARS:
        return text
    return text[:MAX_INLINE_CHARS] + f"\n…（已截断，原文共 {len(text)} 字符）"


def _fence(body: str) -> str:
    """选一条比正文里最长反引号串还长的围栏，免得内容把代码块提前闭合。"""
    longest = 0
    run = 0
    for char in body:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def as_text_block(attachment: Attachment) -> str:
    """文本类附件内联进正文时的样子：文件名 + 围栏包住的内容。"""
    body = decode_text(attachment)
    fence = _fence(body)
    return f"附件 {attachment.name}：\n{fence}\n{body}\n{fence}"


def unsupported_note(attachment: Attachment) -> str:
    """既不是图片也不是 PDF、又解不成文本时，至少告诉模型有这么个文件。"""
    size = len(raw_bytes(attachment))
    return (
        f"附件 {attachment.name}（{attachment.mime or '未知类型'}，{size} 字节）"
        "为二进制格式，未随请求发送内容。"
    )
