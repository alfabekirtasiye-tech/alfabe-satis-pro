# -*- coding: utf-8 -*-
"""Kullanım kılavuzu PDF'ini üretir: python kilavuz_uret.py  ->  ALFABE_KULLANIM_KILAVUZU.pdf
Metin ALFABE_YARDIM.py'den gelir (program içi Yardım ile aynı). Gerekli: reportlab (yalnız bu betik için)."""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ALFABE_YARDIM as Y
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, KeepTogether
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from xml.sax.saxutils import escape as esc

FD = '/usr/share/fonts/truetype/dejavu/'
pdfmetrics.registerFont(TTFont('F', FD + 'DejaVuSans.ttf'))
pdfmetrics.registerFont(TTFont('FB', FD + 'DejaVuSans-Bold.ttf'))
pdfmetrics.registerFontFamily('F', normal='F', bold='FB', italic='F', boldItalic='FB')
MAV = colors.HexColor('#0b376d')
st = dict(
    k=ParagraphStyle('k', fontName='FB', fontSize=26, leading=32, textColor=MAV, spaceAfter=10),
    ak=ParagraphStyle('ak', fontName='F', fontSize=12, leading=17, textColor=colors.HexColor('#475569')),
    g=ParagraphStyle('g', fontName='FB', fontSize=18, leading=22, textColor=MAV, spaceBefore=6, spaceAfter=4),
    gi=ParagraphStyle('gi', fontName='F', fontSize=10, leading=14, textColor=colors.HexColor('#475569'), spaceAfter=10),
    t=ParagraphStyle('t', fontName='FB', fontSize=13, leading=17, textColor=MAV, spaceBefore=14, spaceAfter=4),
    h=ParagraphStyle('h', fontName='FB', fontSize=10.5, leading=14, spaceBefore=8, spaceAfter=2),
    p=ParagraphStyle('p', fontName='F', fontSize=9.5, leading=14, spaceAfter=3),
    b=ParagraphStyle('b', fontName='F', fontSize=9.5, leading=14, leftIndent=14, bulletIndent=4, spaceAfter=2),
    n=ParagraphStyle('n', fontName='F', fontSize=9.5, leading=14, leftIndent=18, firstLineIndent=-14, spaceAfter=2),
    not_=ParagraphStyle('nt', fontName='F', fontSize=9, leading=13, backColor=colors.HexColor('#fffbeb'), borderPadding=5, spaceBefore=6, spaceAfter=8, textColor=colors.HexColor('#92400e')),
    ic=ParagraphStyle('ic', fontName='F', fontSize=10, leading=15, leftIndent=10),
)


def sayfa(c, d):
    c.saveState(); c.setFont('F', 8); c.setFillColor(colors.HexColor('#64748b'))
    c.drawString(2 * cm, 1.2 * cm, 'ALFABE SATIŞ PRO  -  Kullanım Kılavuzu')
    c.drawRightString(A4[0] - 2 * cm, 1.2 * cm, 'Sayfa %d' % d.page); c.restoreState()


def build(out):
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
                            title='ALFABE SATIŞ PRO Kullanım Kılavuzu', author='ALFABE')
    el = [Spacer(1, 5 * cm), Paragraph('ALFABE SATIŞ PRO', st['k']), Paragraph('Kullanım Kılavuzu', st['k']),
          Paragraph('Programdaki ekranların adım adım anlatımı. Aynı metin programın içinde "Yardım / Kılavuz" penceresinde de vardır.', st['ak']),
          Spacer(1, 1 * cm), Paragraph('İçindekiler', st['g'])]
    K = Y.konular()
    for g, intro, ts in K:
        el.append(Paragraph('<b>%s</b>' % esc(g), st['ic']))
        for t in ts:
            el.append(Paragraph('-  %s' % esc(t['title']), ParagraphStyle('i2', parent=st['ic'], leftIndent=26, fontSize=9, leading=12)))
    for g, intro, ts in K:
        el.append(PageBreak()); el.append(Paragraph(esc(g), st['g'])); el.append(Paragraph(esc(intro), st['gi']))
        for t in ts:
            blok = [Paragraph(esc(t['title']), st['t'])]
            for ln in t['text'].split('\n'):
                if ln.startswith('# '): blok.append(Paragraph(esc(ln[2:]), st['h']))
                elif ln.startswith('- '): blok.append(Paragraph(esc(ln[2:]), st['b'], bulletText='•'))
                elif ln.startswith('! '): blok.append(Paragraph('<b>Not:</b> ' + esc(ln[2:]), st['not_']))
                elif re.match(r'^\d+\. ', ln): blok.append(Paragraph(esc(ln), st['n']))
                elif ln.strip(): blok.append(Paragraph(esc(ln), st['p']))
            el.extend(blok[:3]) if False else el.extend(blok)
    doc.build(el, onFirstPage=lambda c, d: None, onLaterPages=sayfa)


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), Y.PDF_AD)
    build(out); print(out)
