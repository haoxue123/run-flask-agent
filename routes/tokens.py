"""设计令牌配置路由：读取 / 应用 / 重置 static/theme.css 的 --rc-* 变量。

安全模型：只提取提交文本中的 --rc-* 变量声明，按选择器（:root 亮色 / [data-theme="dark"] 暗色）
分别合并更新 theme.css 对应块；禁止 @import / url() / javascript: 等危险构造。应用无鉴权（内测），
本页仅供本地/内测使用，避免在生产环境暴露。
"""
import os
import re
from flask import Blueprint, render_template, request, jsonify

tokens_bp = Blueprint("tokens", __name__)

_THEME_CSS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "theme.css"
)

# 内置默认令牌（跑表读数方案，与 static/theme.css 当前默认值一致）
_DEFAULT_TOKEN_CSS = """:root {
  --rc-background:#F3F5F4; --rc-surface:#FFFFFF; --rc-surface-raised:#E9EEEB; --rc-surface-sunken:#EEF2F0;
  --rc-border:rgba(23,32,29,0.12); --rc-border-strong:rgba(23,32,29,0.20); --rc-border-faint:rgba(23,32,29,0.06);
  --rc-text:#17201D; --rc-text-secondary:#46514C; --rc-text-muted:#66736C; --rc-ring:#167657; --rc-ring-offset:#FFFFFF;
  --rc-brand:#167657; --rc-brand-hover:#125F47; --rc-brand-active:#0E4E3B; --rc-on-brand:#FFFFFF; --rc-brand-soft:rgba(22,118,87,0.10); --rc-brand-border:rgba(22,118,87,0.28);
  --rc-energy:#9A6400; --rc-on-energy:#FFFFFF; --rc-energy-soft:rgba(154,100,0,0.10);
  --rc-data:#2A78D6; --rc-on-data:#FFFFFF; --rc-data-soft:rgba(42,120,214,0.10);
  --rc-success:#147C55; --rc-info:#256A92; --rc-info-fg:#256A92; --rc-warning:#9A6200; --rc-warning-fg:#9A6200; --rc-danger:#C53F45; --rc-danger-fg:#C53F45;
  --rc-zone-1:#2A78D6; --rc-zone-2:#167657; --rc-zone-3:#9A6400; --rc-zone-4:#B95627; --rc-zone-5:#B83A44;
  --rc-chart-1:#2A78D6; --rc-chart-2:#EB6834; --rc-chart-3:#1BAF7A; --rc-chart-4:#EDA100; --rc-chart-5:#E87BA4;
  --rc-radius-2xl:24px; --rc-glow-brand:none; --rc-glow-energy:none;
}
[data-theme="dark"], .dark {
  --rc-brand:#72D6AA; --rc-brand-hover:#8AE2BC; --rc-brand-active:#4FBE90; --rc-on-brand:#09251A; --rc-brand-soft:rgba(114,214,170,0.11); --rc-brand-border:rgba(114,214,170,0.28);
  --rc-energy:#E2B45E; --rc-on-energy:#2C1E08; --rc-energy-soft:rgba(226,180,94,0.12);
  --rc-data:#3987E5; --rc-on-data:#FFFFFF; --rc-data-soft:rgba(57,135,229,0.14);
  --rc-background:#101315; --rc-surface:#181D1F; --rc-surface-raised:#202729; --rc-surface-sunken:#121719;
  --rc-border:rgba(237,241,239,0.09); --rc-border-strong:rgba(237,241,239,0.16); --rc-border-faint:rgba(237,241,239,0.05);
  --rc-text:#EDF1EF; --rc-text-secondary:#B0BBB6; --rc-text-muted:#8D9993; --rc-ring:#72D6AA; --rc-ring-offset:#101315;
  --rc-success:#72D6AA; --rc-info:#66B5E5; --rc-info-fg:#79BDEB; --rc-warning:#E2B45E; --rc-warning-fg:#E2B45E; --rc-danger:#F08484; --rc-danger-fg:#F08484;
  --rc-zone-1:#6AA9E9; --rc-zone-2:#72D6AA; --rc-zone-3:#E2B45E; --rc-zone-4:#E58A61; --rc-zone-5:#F08484;
  --rc-chart-1:#3987E5; --rc-chart-2:#D95926; --rc-chart-3:#199E70; --rc-chart-4:#C98500; --rc-chart-5:#D55181;
  --rc-radius-2xl:24px; --rc-glow-brand:none; --rc-glow-energy:none;
  --rc-text-4xl:3rem; --rc-text-5xl:3.75rem; --rc-space-16:64px; --rc-space-20:80px; --rc-space-24:96px;
}"""

_VAR_RE = re.compile(r"(--rc-[a-z0-9-]+)\s*:\s*([^;{}]+);", re.IGNORECASE)
_BLOCK_RE = re.compile(r"([^{}]+?)\s*\{([^{}]*)\}")


def _read_theme():
    try:
        with open(_THEME_CSS, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _semantic_root_match(css):
    """匹配承载 --rc-* 的语义 :root 块（排除仅含色相阶梯的 primitive 块）。"""
    for m in re.finditer(r":root\s*\{([^}]*)\}", css, re.IGNORECASE):
        if "--rc-surface" in m.group(1) or "--rc-background" in m.group(1):
            return m
    return None


def _extract_vars(block_body):
    out = {}
    for name, val in _VAR_RE.findall(block_body):
        out[name.strip()] = val.strip()
    return out


def _extract_non_vars(block_body):
    """保留块内非 --rc-* 声明（如 color-scheme），避免重建块时被静默丢弃。"""
    body = re.sub(r"/\*.*?\*/", "", block_body, flags=re.S)
    out = []
    for decl in body.split(";"):
        decl = re.sub(r"\s+", " ", decl).strip()
        if not decl or ":" not in decl:
            continue
        if not decl.split(":", 1)[0].strip().startswith("--"):
            out.append(decl)
    return out


def _parse_blocks(css_text):
    """[(selector, {var:val})] —— 仅保留含 --rc-* 的块。"""
    blocks = []
    for m in _BLOCK_RE.finditer(css_text):
        sel = m.group(1).strip()
        if sel.startswith("@"):
            continue
        vars_ = _extract_vars(m.group(2))
        if vars_:
            blocks.append((sel, vars_))
    return blocks


def _root_block(vars_dict, selector, extra=()):
    lines = list(extra) + [f"{k}:{v}" for k, v in vars_dict.items()]
    body = ";\n  ".join(lines)
    return f"{selector} {{\n  {body};\n}}"


def _update_block(css, selector_key, new_vars):
    """在 css 中更新匹配 selector_key 的块（合并 new_vars），返回新 css。"""
    if selector_key == ":root":
        m = _semantic_root_match(css)
        sel_out = ":root"
    else:
        pat = re.compile(r'\[data-theme="dark"\][^{]*\{([^}]*)\}', re.IGNORECASE)
        m = pat.search(css)
        sel_out = '[data-theme="dark"], .dark'
    if not m:
        return css
    old = _extract_vars(m.group(1))
    old.update(new_vars)
    block = _root_block(old, sel_out, extra=_extract_non_vars(m.group(1)))
    return css[:m.start()] + block + css[m.end():]


def _apply_blocks(css, blocks):
    for sel, vars_ in blocks:
        key = ":root" if sel.strip() == ":root" else "dark"
        css = _update_block(css, key, vars_)
    return css


@tokens_bp.route("/tokens")
def tokens_page():
    css = _read_theme()
    parts = []
    m = _semantic_root_match(css)
    if m:
        parts.append(m.group(0))
    dm = re.search(r'\[data-theme="dark"\][^{]*\{[^}]*\}', css, re.IGNORECASE)
    if dm:
        parts.append(dm.group(0))
    token_css = "\n\n".join(parts) if parts else ":root {}"
    return render_template("tokens.html", token_css=token_css)


@tokens_bp.route("/api/tokens/apply", methods=["POST"])
def apply_tokens():
    data = request.get_json(silent=True) or {}
    css = data.get("css", "")
    if not css or len(css) > 20000:
        return jsonify(success=False, error="令牌内容为空或过大"), 400
    if re.search(r"@import|url\s*\(|expression\s*\(|<script|javascript:|</style", css, re.IGNORECASE):
        return jsonify(success=False, error="包含不允许的内容（@import/url/javascript）"), 400
    blocks = _parse_blocks(css)
    if not blocks:
        return jsonify(success=False, error="未找到任何 --rc-* 变量"), 400
    current = _read_theme()
    try:
        new_css = _apply_blocks(current, blocks)
        with open(_THEME_CSS, "w", encoding="utf-8") as f:
            f.write(new_css)
    except OSError as e:
        return jsonify(success=False, error=f"写入失败: {e}"), 500
    return jsonify(success=True, message="已应用，刷新查看换肤")


@tokens_bp.route("/api/tokens/reset", methods=["POST"])
def reset_tokens():
    blocks = _parse_blocks(_DEFAULT_TOKEN_CSS)
    current = _read_theme()
    try:
        new_css = _apply_blocks(current, blocks)
        with open(_THEME_CSS, "w", encoding="utf-8") as f:
            f.write(new_css)
    except OSError as e:
        return jsonify(success=False, error=f"写入失败: {e}"), 500
    return jsonify(success=True, token_css=_DEFAULT_TOKEN_CSS, message="已重置为默认令牌")
