# 将简历展示文档.md 渲染为 PDF（使用 reportlab 内置中日韩字体 STSong-Light）
import re, os
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                ListFlowable, ListItem)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

# 注册中日韩字体，解决中文乱码
pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
FONT = 'STSong-Light'

def md_to_flowables(path):
    h1 = ParagraphStyle('h1', fontName=FONT, fontSize=20, leading=26,
                        spaceAfter=10, textColor='#0c2530')
    h2 = ParagraphStyle('h2', fontName=FONT, fontSize=14, leading=19,
                        spaceBefore=12, spaceAfter=6, textColor='#0e7c8a')
    h3 = ParagraphStyle('h3', fontName=FONT, fontSize=11.5, leading=16,
                        spaceBefore=8, spaceAfter=4, textColor='#13333d')
    body = ParagraphStyle('body', fontName=FONT, fontSize=10, leading=15,
                          spaceAfter=6, textColor='#1d2b30')
    bullet = ParagraphStyle('bullet', fontName=FONT, fontSize=10, leading=15,
                            leftIndent=12, spaceAfter=3, textColor='#1d2b30')
    flow = []
    with open(path, encoding='utf-8') as f:
        lines = f.read().split('\n')
    list_buf = []

    def flush():
        nonlocal list_buf
        if list_buf:
            items = [ListItem(Paragraph(t, bullet)) for t in list_buf]
            flow.append(ListFlowable(items, bulletType='bullet', start='•'))
            list_buf = []

    for line in lines:
        s = line.strip()
        if not s:
            flush(); continue
        txt = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', s)
        if s.startswith('## '):
            flush(); flow.append(Paragraph(s[3:], h2)); continue
        if s.startswith('### '):
            flush(); flow.append(Paragraph(s[4:], h3)); continue
        if s.startswith('# '):
            flush(); flow.append(Paragraph(s[2:], h1)); continue
        if s.startswith('- '):
            list_buf.append(txt[2:]); continue
        flush()
        flow.append(Paragraph(txt, body))
    flush()
    return flow

here = os.path.dirname(os.path.abspath(__file__))
doc = SimpleDocTemplate(
    os.path.join(here, '简历展示文档.pdf'), pagesize=A4,
    topMargin=18 * mm, bottomMargin=16 * mm,
    leftMargin=18 * mm, rightMargin=18 * mm,
    title='马拉松训练计划 Agent 简历展示文档')
doc.build(md_to_flowables(os.path.join(here, '简历展示文档.md')))
print('PDF generated: 简历展示文档.pdf')
