"""Exam instructions as the Word-exported HTML of a lab handout.

The stylesheet and the markup (p.LabTitle, p.LabSection, p.BodyTextL25, p.Bulletlevel1/2,
table.MsoNormalTable with p.TableHeading / p.TableText, inside div.WordSection1) are
that format's own: PT's instruction pane shows it as is. Every text goes through
html.escape; `**word**` is the only markup a spec can ask for, and renders <b>.
"""

from __future__ import annotations

import html
import re

from ..models.exam import AddressRow, ExamSpec, Requirement, SubRequirement

LABELS = {
    "es": {"table": "Tabla de asignación de direcciones", "situation": "Situación",
           "requirements": "Requisitos", "device": "Dispositivo", "interface": "Interfaz",
           "ip": "Dirección IP", "mask": "Máscara de subred",
           "gateway": "Gateway predeterminado", "note": "Nota:"},
    "en": {"table": "Addressing Table", "situation": "Scenario",
           "requirements": "Requirements", "device": "Device", "interface": "Interface",
           "ip": "IP Address", "mask": "Subnet Mask",
           "gateway": "Default Gateway", "note": "Note:"},
}

_HEAD = (
    '<html contenteditable="true"><head>\n'
    '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">\n'
    '<meta name="Generator" content="Microsoft Word 12 (filtered)"><style>'
)

STYLE = r"""
<!--
/* Font Definitions */
@font-face
{font-family:"Cambria Math";
panose-1:2 4 5 3 5 4 6 3 2 4;}
@font-face
{font-family:Cambria;
panose-1:2 4 5 3 5 4 6 3 2 4;}
@font-face
{font-family:Calibri;
panose-1:2 15 5 2 2 2 4 3 2 4;}
@font-face
{font-family:Tahoma;
panose-1:2 11 6 4 3 5 4 4 2 4;}
/* Style Definitions */
p.MsoNormal, li.MsoNormal, div.MsoNormal
{margin-top:3.0pt;
margin-right:0in;
margin-bottom:3.0pt;
margin-left:0in;
line-height:115%;
font-size:11.0pt;
font-family:"Arial","sans-serif";}
h1
{mso-style-link:"Heading 1 Char";
margin-top:24.0pt;
margin-right:0in;
margin-bottom:0in;
margin-left:0in;
margin-bottom:.0001pt;
line-height:115%;
page-break-after:avoid;
font-size:14.0pt;
font-family:"Cambria","serif";
color:#365F91;}
h2
{mso-style-link:"Heading 2 Char";
margin-top:10.0pt;
margin-right:0in;
margin-bottom:0in;
margin-left:0in;
margin-bottom:.0001pt;
line-height:115%;
page-break-after:avoid;
font-size:13.0pt;
font-family:"Cambria","serif";
color:#4F81BD;}
p.MsoCommentText, li.MsoCommentText, div.MsoCommentText
{mso-style-link:"Comment Text Char";
margin-top:3.0pt;
margin-right:0in;
margin-bottom:3.0pt;
margin-left:0in;
line-height:115%;
font-size:10.0pt;
font-family:"Arial","sans-serif";}
p.MsoHeader, li.MsoHeader, div.MsoHeader
{mso-style-link:"Header Char";
margin-top:3.0pt;
margin-right:0in;
margin-bottom:0in;
margin-left:0in;
margin-bottom:.0001pt;
font-size:11.0pt;
font-family:"Arial","sans-serif";}
p.MsoFooter, li.MsoFooter, div.MsoFooter
{mso-style-link:"Footer Char";
margin-top:3.0pt;
margin-right:0in;
margin-bottom:0in;
margin-left:0in;
margin-bottom:.0001pt;
font-size:8.0pt;
font-family:"Arial","sans-serif";}
p.MsoDocumentMap, li.MsoDocumentMap, div.MsoDocumentMap
{mso-style-link:"Document Map Char";
margin-top:3.0pt;
margin-right:0in;
margin-bottom:0in;
margin-left:0in;
margin-bottom:.0001pt;
font-size:8.0pt;
font-family:"Tahoma","sans-serif";}
pre
{mso-style-link:"HTML Preformatted Char";
margin:0in;
margin-bottom:.0001pt;
font-size:10.0pt;
font-family:"Courier New";}
p.MsoCommentSubject, li.MsoCommentSubject, div.MsoCommentSubject
{mso-style-link:"Comment Subject Char";
margin-top:3.0pt;
margin-right:0in;
margin-bottom:3.0pt;
margin-left:0in;
line-height:115%;
font-size:10.0pt;
font-family:"Arial","sans-serif";
font-weight:bold;}
p.MsoAcetate, li.MsoAcetate, div.MsoAcetate
{mso-style-link:"Balloon Text Char";
margin-top:3.0pt;
margin-right:0in;
margin-bottom:0in;
margin-left:0in;
margin-bottom:.0001pt;
font-size:8.0pt;
font-family:"Tahoma","sans-serif";}
p.MsoListParagraph, li.MsoListParagraph, div.MsoListParagraph
{margin-top:3.0pt;
margin-right:0in;
margin-bottom:3.0pt;
margin-left:.5in;
line-height:115%;
font-size:11.0pt;
font-family:"Arial","sans-serif";}
span.Heading1Char
{mso-style-name:"Heading 1 Char";
mso-style-link:"Heading 1";
font-family:"Cambria","serif";
color:#365F91;
font-weight:bold;}
span.Heading2Char
{mso-style-name:"Heading 2 Char";
mso-style-link:"Heading 2";
font-family:"Cambria","serif";
color:#4F81BD;
font-weight:bold;}
p.ClientNote, li.ClientNote, div.ClientNote
{mso-style-name:"Client Note";
margin-top:3.0pt;
margin-right:0in;
margin-bottom:0in;
margin-left:0in;
margin-bottom:.0001pt;
font-size:11.0pt;
font-family:"Arial","sans-serif";
color:red;
font-style:italic;}
p.LabSection, li.LabSection, div.LabSection
{mso-style-name:"Lab Section";
margin-top:12.0pt;
margin-right:0in;
margin-bottom:6.0pt;
margin-left:0in;
text-indent:0in;
page-break-after:avoid;
font-size:12.0pt;
font-family:"Arial","sans-serif";
font-weight:bold;}
p.LabTitle, li.LabTitle, div.LabTitle
{mso-style-name:"Lab Title";
margin-top:3.0pt;
margin-right:0in;
margin-bottom:3.0pt;
margin-left:0in;
line-height:115%;
font-size:16.0pt;
font-family:"Arial","sans-serif";
font-weight:bold;}
p.PageHead, li.PageHead, div.PageHead
{mso-style-name:"Page Head";
margin-top:3.0pt;
margin-right:0in;
margin-bottom:3.0pt;
margin-left:0in;
line-height:115%;
border:none;
padding:0in;
font-size:10.0pt;
font-family:"Arial","sans-serif";
font-weight:bold;}
p.StepHead, li.StepHead, div.StepHead
{mso-style-name:"Step Head";
margin-top:12.0pt;
margin-right:0in;
margin-bottom:6.0pt;
margin-left:.65in;
text-indent:-.65in;
line-height:115%;
page-break-after:avoid;
font-size:11.0pt;
font-family:"Arial","sans-serif";
font-weight:bold;}
span.HeaderChar
{mso-style-name:"Header Char";
mso-style-link:Header;}
span.FooterChar
{mso-style-name:"Footer Char";
mso-style-link:Footer;}
span.BalloonTextChar
{mso-style-name:"Balloon Text Char";
mso-style-link:"Balloon Text";
font-family:"Tahoma","sans-serif";}
p.BodyText1, li.BodyText1, div.BodyText1
{mso-style-name:"Body Text1";
margin-top:3.0pt;
margin-right:0in;
margin-bottom:3.0pt;
margin-left:0in;
font-size:10.0pt;
font-family:"Arial","sans-serif";}
p.TableText, li.TableText, div.TableText
{mso-style-name:"Table Text";
mso-style-link:"Table Text Char";
margin-top:3.0pt;
margin-right:0in;
margin-bottom:3.0pt;
margin-left:0in;
page-break-after:avoid;
font-size:10.0pt;
font-family:"Arial","sans-serif";}
span.TableTextChar
{mso-style-name:"Table Text Char";
mso-style-link:"Table Text";}
p.TableHeading, li.TableHeading, div.TableHeading
{mso-style-name:"Table Heading";
margin-top:6.0pt;
margin-right:0in;
margin-bottom:6.0pt;
margin-left:0in;
text-align:center;
line-height:115%;
page-break-after:avoid;
font-size:10.0pt;
font-family:"Arial","sans-serif";
font-weight:bold;}
p.Bulletlevel1, li.Bulletlevel1, div.Bulletlevel1
{mso-style-name:"Bullet level 1";
margin-top:3.0pt;
margin-right:0in;
margin-bottom:3.0pt;
margin-left:.5in;
text-indent:-.25in;
line-height:115%;
font-size:10.0pt;
font-family:"Arial","sans-serif";}
p.Bulletlevel2, li.Bulletlevel2, div.Bulletlevel2
{mso-style-name:"Bullet level 2";
margin-top:3.0pt;
margin-right:0in;
margin-bottom:3.0pt;
margin-left:.75in;
text-indent:-.25in;
line-height:115%;
font-size:10.0pt;
font-family:"Arial","sans-serif";}
p.InstNoteRed, li.InstNoteRed, div.InstNoteRed
{mso-style-name:"Inst Note Red";
margin-top:3.0pt;
margin-right:0in;
margin-bottom:3.0pt;
margin-left:0in;
font-size:10.0pt;
font-family:"Arial","sans-serif";
color:red;}
p.PartHead, li.PartHead, div.PartHead
{mso-style-name:"Part Head";
margin-top:12.0pt;
margin-right:0in;
margin-bottom:3.0pt;
margin-left:.75in;
text-indent:-.75in;
line-height:115%;
page-break-after:avoid;
font-size:14.0pt;
font-family:"Arial","sans-serif";
font-weight:bold;}
p.SubStepAlpha, li.SubStepAlpha, div.SubStepAlpha
{mso-style-name:"SubStep Alpha";
margin-top:6.0pt;
margin-right:0in;
margin-bottom:6.0pt;
margin-left:.5in;
text-indent:-.25in;
font-size:10.0pt;
font-family:"Arial","sans-serif";}
p.CMD, li.CMD, div.CMD
{mso-style-name:CMD;
margin-top:3.0pt;
margin-right:0in;
margin-bottom:3.0pt;
margin-left:.5in;
font-size:10.0pt;
font-family:"Courier New";}
p.BodyTextL50, li.BodyTextL50, div.BodyTextL50
{mso-style-name:"Body Text L50";
margin-top:6.0pt;
margin-right:0in;
margin-bottom:3.0pt;
margin-left:.5in;
font-size:10.0pt;
font-family:"Arial","sans-serif";}
p.BodyTextL25, li.BodyTextL25, div.BodyTextL25
{mso-style-name:"Body Text L25";
margin-top:6.0pt;
margin-right:0in;
margin-bottom:6.0pt;
margin-left:.25in;
font-size:10.0pt;
font-family:"Arial","sans-serif";}
p.InstNoteRedL50, li.InstNoteRedL50, div.InstNoteRedL50
{mso-style-name:"Inst Note Red L50";
margin-top:6.0pt;
margin-right:0in;
margin-bottom:6.0pt;
margin-left:.5in;
font-size:10.0pt;
font-family:"Arial","sans-serif";
color:red;}
p.DevConfigs, li.DevConfigs, div.DevConfigs
{mso-style-name:DevConfigs;
margin:0in;
margin-bottom:.0001pt;
line-height:115%;
font-size:10.0pt;
font-family:"Courier New";}
p.Visual, li.Visual, div.Visual
{mso-style-name:Visual;
margin-top:12.0pt;
margin-right:0in;
margin-bottom:12.0pt;
margin-left:0in;
text-align:center;
line-height:115%;
font-size:11.0pt;
font-family:"Arial","sans-serif";}
span.DocumentMapChar
{mso-style-name:"Document Map Char";
mso-style-link:"Document Map";
font-family:"Tahoma","sans-serif";}
span.LabTitleInstVersred
{mso-style-name:"Lab Title Inst Vers \(red\)";
font-family:"Arial","sans-serif";
color:red;
font-weight:bold;}
span.AnswerGray
{mso-style-name:"Answer Gray";
font-family:"Arial","sans-serif";
border:none;
background:#BFBFBF;}
span.LabSectionGray
{mso-style-name:"Lab Section Gray";
font-family:"Arial","sans-serif";
border:none;
background:#BFBFBF;}
p.SubStepNum, li.SubStepNum, div.SubStepNum
{mso-style-name:"SubStep Num";
margin-top:6.0pt;
margin-right:0in;
margin-bottom:6.0pt;
margin-left:.75in;
text-indent:-.25in;
font-size:10.0pt;
font-family:"Arial","sans-serif";}
span.DevConfigGray
{mso-style-name:"DevConfig Gray";
font-family:"Courier New";
color:windowtext;
border:none;
background:#BFBFBF;}
p.CMDOutput, li.CMDOutput, div.CMDOutput
{mso-style-name:"CMD Output";
margin-top:3.0pt;
margin-right:0in;
margin-bottom:3.0pt;
margin-left:.5in;
font-size:9.0pt;
font-family:"Courier New";}
p.InstNoteRedL25, li.InstNoteRedL25, div.InstNoteRedL25
{mso-style-name:"Inst Note Red L25";
margin-top:6.0pt;
margin-right:0in;
margin-bottom:6.0pt;
margin-left:.25in;
font-size:10.0pt;
font-family:"Arial","sans-serif";
color:red;}
p.BodyTextL25Bold, li.BodyTextL25Bold, div.BodyTextL25Bold
{mso-style-name:"Body Text L25 Bold";
margin-top:6.0pt;
margin-right:0in;
margin-bottom:6.0pt;
margin-left:.25in;
font-size:10.0pt;
font-family:"Arial","sans-serif";
font-weight:bold;}
span.HTMLPreformattedChar
{mso-style-name:"HTML Preformatted Char";
mso-style-link:"HTML Preformatted";
font-family:"Courier New";}
span.CommentTextChar
{mso-style-name:"Comment Text Char";
mso-style-link:"Comment Text";}
span.CommentSubjectChar
{mso-style-name:"Comment Subject Char";
mso-style-link:"Comment Subject";
font-weight:bold;}
p.ReflectionQ, li.ReflectionQ, div.ReflectionQ
{mso-style-name:"Reflection Q";
margin-top:6.0pt;
margin-right:0in;
margin-bottom:6.0pt;
margin-left:.25in;
text-indent:-.25in;
font-size:10.0pt;
font-family:"Arial","sans-serif";}
.MsoChpDefault
{font-size:10.0pt;}
/* Page Definitions */
@page WordSection1
{size:8.5in 11.0in;
margin:1.0in .75in 1.0in .75in;}
div.WordSection1
{page:WordSection1;}
/* List Definitions */
ol
{margin-bottom:0in;}
ul
{margin-bottom:0in;}
-->
"""

_SECTION = '<p class="LabSection" style="margin-left:0in;text-indent:0in">{}</p>'
_PAD = "padding:.7pt 5.75pt .7pt 5.75pt"
_LINE = "solid windowtext 1.0pt"
_BULLET1 = ('<span style="font-family:Symbol">·<span style="font:7.0pt &quot;Times New Roman'
            '&quot;"> &nbsp; &nbsp; \n</span></span>')
_BULLET2 = '-<span style="font:7.0pt &quot;Times New Roman&quot;"> &nbsp; &nbsp; \n</span>'
_BOLD = re.compile(r"\*\*(.+?)\*\*")
# (px, pt) of each column.
_DEVICE_W, _INTERFACE_W, _VALUE_W = (84, "63.0pt"), (75, "56.25pt"), (132, "99.0pt")


def inline(text: str) -> str:
    """Escaped text with **bold** applied."""
    return _BOLD.sub(r"<b>\1</b>", html.escape(text, quote=False))


def _heading_cell(text: str, width: tuple[int, str], first: bool) -> str:
    left = "" if first else ";border-left:none"
    return (f'<td width="{width[0]}" style="width:{width[1]};border:{_LINE}{left};'
            f'background:#DBE5F1;{_PAD};height:12.75pt">\n'
            f'<p class="TableHeading">{html.escape(text, quote=False)}</p>\n</td>')


def _cell(text: str, width: tuple[int, str], *, first: bool = False, rowspan: int = 1) -> str:
    span = f' rowspan="{rowspan}"' if rowspan > 1 else ""
    border = (f"border:{_LINE};border-top:none" if first else
              f"border-top:none;border-left:none;border-bottom:{_LINE};border-right:{_LINE}")
    return (f'<td width="{width[0]}"{span} style="width:{width[1]};{border};{_PAD}">\n'
            f'<p class="TableText">{html.escape(text, quote=False)}</p>\n</td>')


def _table(rows: list[AddressRow], labels: dict[str, str]) -> str:
    gateway = any(r.gateway.strip() for r in rows)
    values = [("ip", _VALUE_W), ("mask", _VALUE_W)] + ([("gateway", _VALUE_W)] if gateway else [])
    head = [_heading_cell(labels["device"], _DEVICE_W, True),
            _heading_cell(labels["interface"], _INTERFACE_W, False)]
    head += [_heading_cell(labels[key], width, False) for key, width in values]
    width_px = _DEVICE_W[0] + _INTERFACE_W[0] + _VALUE_W[0] * len(values)
    out = [
        '<div align="center">',
        f'<table class="MsoNormalTable" border="1" cellspacing="0" cellpadding="0" '
        f'width="{width_px}" style="width:{width_px * 0.75:g}pt;border-collapse:collapse;border:none">',
        '<tbody><tr style="page-break-inside:avoid;height:12.75pt">', *head, "</tr>",
    ]
    i = 0
    while i < len(rows):
        span = 1  # consecutive rows of a device share its cell
        while i + span < len(rows) and rows[i + span].device == rows[i].device:
            span += 1
        for j in range(span):
            row = rows[i + j]
            cells = [_cell(row.device, _DEVICE_W, first=True, rowspan=span)] if j == 0 else []
            cells.append(_cell(row.interface, _INTERFACE_W))
            cells += [_cell(getattr(row, key), width) for key, width in values]
            out += ['<tr style="page-break-inside:avoid">', *cells, "</tr>"]
        i += span
    out += ["</tbody></table>", "</div>"]
    return "\n".join(out)


def _requirement(req: Requirement | str, labels: dict[str, str]) -> list[str]:
    if isinstance(req, str):
        req = Requirement(text=req)
    out = [f'<p class="Bulletlevel1">{_BULLET1}{inline(req.text)}</p>']
    for item in req.items:
        if isinstance(item, str):
            item = SubRequirement(text=item)
        out.append(f'<p class="Bulletlevel2">{_BULLET2}{inline(item.text)}</p>')
        out += [f'<p class="Bulletlevel2" style="margin-left:1.0in;text-indent:0in">{inline(d)}</p>'
                for d in item.details]
    if req.note.strip():
        out.append(f'<p class="BodyTextL25"><b>{html.escape(labels["note"], quote=False)}</b> '
                   f'{inline(req.note)}</p>')
    return out


def render_exam_html(spec: ExamSpec) -> str:
    labels = LABELS.get(spec.language, LABELS["es"])
    situation = [spec.situation] if isinstance(spec.situation, str) else spec.situation
    parts = [f'<p class="LabTitle">{inline(spec.title)}</p>']
    if spec.addressing:
        parts += [_SECTION.format(html.escape(labels["table"], quote=False)),
                  _table(spec.addressing, labels)]
    parts.append(_SECTION.format(html.escape(labels["situation"], quote=False)))
    parts += [f'<p class="BodyTextL25">{inline(p)}</p>' for p in situation]
    if spec.requirements:
        parts.append(_SECTION.format(html.escape(labels["requirements"], quote=False)))
        for req in spec.requirements:
            parts += _requirement(req, labels)
    body = "\n\n".join(parts)
    return (f'{_HEAD}{STYLE}</style>\n\n</head>\n\n<body lang="EN-US">\n\n'
            f'<div class="WordSection1">\n\n{body}\n\n</div>\n\n</body></html>')
