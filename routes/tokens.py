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

# 内置默认令牌（Pulse Green 优化方案，与 docs/design-audit-cai.md 一致）
_DEFAULT_TOKEN_CSS = """:root {
  --rc-background:#F6FAF7; --rc-surface:#FFFFFF; --rc-surface-raised:#EEF3F0; --rc-surface-sunken:#EAF1ED;
  --rc-border:rgba(16,32,28,0.12); --rc-border-strong:rgba(16,32,28,0.20); --rc-border-faint:rgba(16,32,28,0.07);
  --rc-text:#16201C; --rc-text-secondary:#445049; --rc-text-muted:#5E6862; --rc-ring:#0A9A63; --rc-ring-offset:#FFFFFF;
  --rc-on-data:#FFFFFF;
  --rc-zone-1:#0369A1; --rc-zone-2:#047857; --rc-zone-3:#B45309; --rc-zone-4:#C2410C; --rc-zone-5:#B91C1C;
}
[data-theme="dark"], .dark {
  --rc-brand:#10E08A; --rc-brand-hover:#0FCB7E; --rc-brand-active:#0A9A63; --rc-on-brand:#04140D; --rc-brand-soft:rgba(16,224,138,0.12); --rc-brand-border:rgba(16,224,138,0.30);
  --rc-energy:#C6F000; --rc-on-energy:#1A2400; --rc-energy-soft:rgba(198,240,0,0.12);
  --rc-data:#2BC4E6; --rc-on-data:#04222B; --rc-data-soft:rgba(43,196,230,0.12);
  --rc-background:#0B0F0E; --rc-surface:#141A18; --rc-surface-raised:#1A221F; --rc-surface-sunken:#0E1311;
  --rc-border:rgba(255,255,255,0.08); --rc-border-strong:rgba(255,255,255,0.14); --rc-border-faint:rgba(255,255,255,0.05);
  --rc-text:#ECF3EF; --rc-text-secondary:#9FAAA4; --rc-text-muted:#7C887F; --rc-ring:#10E08A; --rc-ring-offset:#0B0F0E;
  --rc-success:#2BD493; --rc-info:#38BDF8; --rc-info-fg:#0369A1; --rc-warning:#FFB02E; --rc-warning-fg:#92560A; --rc-danger:#FF5C5C; --rc-danger-fg:#B91C1C;
  --rc-zone-1:#38BDF8; --rc-zone-2:#34D399; --rc-zone-3:#FBBF24; --rc-zone-4:#FB923C; --rc-zone-5:#FF5C5C;
  --rc-chart-1:#10E08A; --rc-chart-2:#2BC4E6; --rc-chart-3:#FFB02E; --rc-chart-4:#C6F000; --rc-chart-5:#FF5C5C;
  --rc-radius-2xl:28px; --rc-glow-brand:0 0 24px rgba(16,224,138,0.35); --rc-glow-energy:0 0 24px rgba(198,240,0,0.28);
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


def _root_block(vars_dict, selector):
    body = ";\n  ".join(f"{k}:{v}" for k, v in vars_dict.items())
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
    block = _root_block(old, sel_out)
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
