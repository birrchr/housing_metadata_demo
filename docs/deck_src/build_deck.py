"""Build docs/housing_metadata_model.pptx with python-pptx."""
import sys
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

IMG = Path(__file__).parent / "img"
OUT = Path(sys.argv[1])

# ---- palette ---------------------------------------------------------------
INK, INK2, MUTED = "1F2933", "52606D", "7B8794"
PANEL, LINE = "F0F4F8", "D9E2EC"
NAVY, TEAL, WARM = "243B53", "0E7C86", "B54531"
ICE = "BCCCDC"
PROV = {"ON": "1C5CAB", "BC": "C2410C", "NS": "0F7A55"}
FONT, MONO = "Calibri", "Courier New"
W, H = 13.333, 7.5
L, CW = 0.6, 12.133          # left margin, content width

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(W), Inches(H)
BLANK = prs.slide_layouts[6]
rgb = RGBColor.from_string


# ---- helpers ---------------------------------------------------------------
def _runs(p, runs, size, color, bold, italic, font):
    if isinstance(runs, str):
        runs = [(runs, {})]
    for text, o in runs:
        r = p.add_run()
        r.text = text
        f = r.font
        f.name = o.get("font", font)
        f.size = Pt(o.get("size", size))
        f.bold = o.get("bold", bold)
        f.italic = o.get("italic", italic)
        f.color.rgb = rgb(o.get("color", color))


def text(slide, x, y, w, h, paras, size=16, color=INK, bold=False, italic=False,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font=FONT, margin=0.0,
         space_after=0, bullets=False, line_spacing=None, name=None):
    """paras: str | list of (str | list of (text, opts))."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.vertical_anchor = anchor
    for side in ("left", "right", "top", "bottom"):
        setattr(tf, f"margin_{side}", Inches(margin))
    if isinstance(paras, str):
        paras = [paras]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if space_after:
            p.space_after = Pt(space_after)
        if line_spacing:
            p.line_spacing = line_spacing
        if bullets:
            pPr = p._p.get_or_add_pPr()
            pPr.set("marL", str(Inches(0.28)))
            pPr.set("indent", str(-Inches(0.28)))
            bu = pPr.makeelement(qn("a:buChar"), {"char": "•"})
            pPr.append(bu)
        _runs(p, para, size, color, bold, italic, font)
    return tb


def box(slide, x, y, w, h, fill=PANEL, line=None, shape=MSO_SHAPE.RECTANGLE, name=None):
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = 0.08
    s.fill.solid()
    s.fill.fore_color.rgb = rgb(fill)
    if line:
        s.line.color.rgb = rgb(line)
        s.line.width = Pt(1)
    else:
        s.line.fill.background()
    st = s._element.find(qn("p:style"))      # drop theme effect (shadow) reference
    if st is not None:
        s._element.remove(st)
    if name:
        s.name = name
    return s


def arrow(slide, x1, y1, x2, y2, color=MUTED):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(2)
    ln = c.line._get_or_add_ln()
    ln.append(ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))
    return c


def image(slide, name, x, y, w=None, h=None, alt=""):
    path = IMG / name
    iw, ih = Image.open(path).size
    if w and not h:
        h = w * ih / iw
    elif h and not w:
        w = h * iw / ih
    pic = slide.shapes.add_picture(str(path), Inches(x), Inches(y), Inches(w), Inches(h))
    pic._element.nvPicPr.cNvPr.set("descr", alt)
    return w, h


def table(slide, x, y, w, col_w, rows, size=14, header_fill=NAVY, row_h=0.42,
          first_col_bold=False, mono_cols=()):
    nr, nc = len(rows), len(rows[0])
    gt = slide.shapes.add_table(nr, nc, Inches(x), Inches(y), Inches(w), Inches(row_h * nr))
    t = gt.table
    tblPr = t._tbl.tblPr
    for attr in ("bandRow", "firstRow"):
        tblPr.set(attr, "0")
    style = tblPr.find(qn("a:tableStyleId"))
    if style is not None:
        style.text = "{5940675A-B579-460E-94D1-54222C63F5DA}"   # "No Style, Table Grid"
    for j, cw in enumerate(col_w):
        t.columns[j].width = Inches(cw)
    for i, row in enumerate(rows):
        t.rows[i].height = Inches(row_h)
        for j, val in enumerate(row):
            cell = t.cell(i, j)
            cell.margin_left = cell.margin_right = Inches(0.12)
            cell.margin_top = cell.margin_bottom = Inches(0.05)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            cell.fill.fore_color.rgb = rgb(header_fill if i == 0 else ("FFFFFF" if i % 2 else PANEL))
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            hdr = i == 0
            _runs(p, val, size, "FFFFFF" if hdr else INK,
                  hdr or (first_col_bold and j == 0), False,
                  MONO if (j in mono_cols and not hdr) else FONT)
            # thin horizontal rules only
            tcPr = cell._tc.get_or_add_tcPr()
            for edge in ("a:lnL", "a:lnR", "a:lnT", "a:lnB"):
                ln = tcPr.makeelement(qn(edge), {"w": "6350" if edge == "a:lnB" else "0"})
                if edge == "a:lnB":
                    sf = ln.makeelement(qn("a:solidFill"), {})
                    sf.append(sf.makeelement(qn("a:srgbClr"), {"val": LINE}))
                    ln.append(sf)
                else:
                    ln.append(ln.makeelement(qn("a:noFill"), {}))
                tcPr.append(ln)
    return gt


SLIDE_NO = [0]


def content(title, ref=None, notes=""):
    s = prs.slides.add_slide(BLANK)
    SLIDE_NO[0] += 1
    text(s, L, 0.42, CW, 0.85, title, size=36, bold=True, anchor=MSO_ANCHOR.MIDDLE, name="Title")
    if ref:
        text(s, L, 6.92, 9, 0.35, [[("Notebook  ", {"bold": True}), (ref, {})]],
             size=12, color=TEAL, anchor=MSO_ANCHOR.MIDDLE, name="Notebook reference")
    text(s, W - 1.6, 6.92, 1.0, 0.35, str(SLIDE_NO[0]), size=11, color=MUTED,
         align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE, name="Slide number")
    if notes:
        s.notes_slide.notes_text_frame.text = notes
    return s


def dark(notes=""):
    s = prs.slides.add_slide(BLANK)
    SLIDE_NO[0] += 1
    bg = s.background.fill
    bg.solid()
    bg.fore_color.rgb = rgb(NAVY)
    if notes:
        s.notes_slide.notes_text_frame.text = notes
    return s


# =============================================================================
# 1. Title
s = dark(notes=(
    "This deck goes with the housing_metadata_demo repo and its walkthrough notebook. "
    "The ask: stop treating each province's metadata as its own manual project, and maintain "
    "one mapping database that a single generic process uses every year."))
text(s, L + 0.2, 2.2, 11.5, 1.6, "One metadata model for every province",
     size=44, bold=True, color="FFFFFF", anchor=MSO_ANCHOR.BOTTOM)
text(s, L + 0.2, 3.95, 11.0, 1.0,
     "Moving annual housing data from manual, province-by-province handling "
     "to one maintained mapping database",
     size=20, color=ICE)
text(s, L + 0.2, 6.4, 11.5, 0.4,
     [[("Demo: ", {"bold": True}), ("notebooks/walkthrough.ipynb  ·  housing_metadata_demo repo", {})]],
     size=14, color=ICE)

# =============================================================================
# 2. The problem
s = content("The same concept arrives three different ways", "§1 · The problem", notes=(
    "Notebook section 1. Three provinces, one concept (tenure), three column names and three "
    "coding schemes. To draw even this simple chart without the model, the notebook has to "
    "hard-code which column to read for each province. That dictionary is exactly the kind of "
    "logic that piles up in our current scripts."))
_, ih = image(s, "fig01.png", L, 1.45, w=CW, alt="Raw tenure codes by province, 2025")
cols = [
    ("Names differ", "Tenure is tenure_cd, TEN or tenure"),
    ("Codes differ", "O/R/X  vs  1/2/3  vs  1/2/9"),
    ("Names change", "Ontario renamed shelter cost in 2025"),
    ("Extras appear", "BC sends a column nobody asked for"),
]
cw4 = (CW - 3 * 0.3) / 4
for k, (hd, body) in enumerate(cols):
    x = L + k * (cw4 + 0.3)
    box(s, x, 5.3, cw4, 1.3, fill=PANEL, name=f"Card {hd}")
    text(s, x + 0.2, 5.42, cw4 - 0.4, 1.1, [[(hd, {"bold": True, "size": 17})], body],
         size=14, color=INK, space_after=4)

# =============================================================================
# 3. Today
s = content("Today: each province is its own project", notes=(
    "This is how the work has usually been done. Each province gets its own script, its own "
    "recodes and its own yearly fixes, often held by different people. None of it is shared, so "
    "every year we repeat the work three times over, and a new province means starting again."))
text(s, L, 1.45, 7.0, 0.4, "How the work is split up now", size=20, bold=True)
steps = ["Own script", "Own recodes", "Own yearly fixes"]
for i, (prov, col) in enumerate(PROV.items()):
    y = 2.05 + i * 1.1
    box(s, L, y, 1.0, 0.85, fill=col, shape=MSO_SHAPE.ROUNDED_RECTANGLE, name=f"{prov} chip")
    text(s, L, y, 1.0, 0.85, prov, size=20, bold=True, color="FFFFFF",
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    for j, st in enumerate(steps):
        x = L + 1.3 + j * 1.95
        box(s, x, y, 1.75, 0.85, fill=PANEL, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
        text(s, x, y, 1.75, 0.85, st, size=14, color=INK2, align=PP_ALIGN.CENTER,
             anchor=MSO_ANCHOR.MIDDLE)
text(s, L, 5.45, 7.2, 0.9,
     "…repeated every year, and again for every new province",
     size=18, italic=True, color=WARM)
box(s, 8.0, 1.45, CW + L - 8.0, 5.1, fill=PANEL, name="Cost panel")
text(s, 8.3, 1.65, 4.2, 0.5, "What this costs us", size=20, bold=True)
text(s, 8.3, 2.3, 4.2, 4.1, [
    [("Manual: ", {"bold": True}), ("renames and recodes are re-done by hand each year", {})],
    [("Spread out: ", {"bold": True}), ("knowledge sits in separate scripts, spreadsheets and people", {})],
    [("Inconsistent: ", {"bold": True}), ("each province is checked differently; problems surface late", {})],
    [("Hard to audit: ", {"bold": True}), ("no single record of what mapped to what", {})],
    [("Hard to grow: ", {"bold": True}), ("a new province means a new code path", {})],
], size=15, bullets=True, space_after=9)

# =============================================================================
# 4. Proposed
s = content("Proposed: one process, driven by metadata", "§3 · Build", notes=(
    "The alternative: put everything province-specific into a small mapping database, and run "
    "one generic load against it. The code never mentions a province. In the notebook, "
    "section 3 is a single call, build.build(con, META, RAW), and it loads every province "
    "and year the same way."))
for i, (prov, col) in enumerate(PROV.items()):
    y = 1.95 + i * 1.0
    box(s, L, y, 1.5, 0.75, fill=col, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    text(s, L, y, 1.5, 0.75, f"{prov} files", size=16, bold=True, color="FFFFFF",
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
stages = [
    ("Mapping database", "What each province calls each variable, and how its codes translate. "
                         "Small, dated tables, with production versions kept in OneLake.", TEAL),
    ("One generic load", "The same steps for every province and every year. "
                         "No province names anywhere in the code.", NAVY),
    ("Consistent output", "One labelled model with full lineage and built-in quality checks.", NAVY),
]
bx, bw, gap = 2.75, 2.95, 0.45
for k, (hd, body, col) in enumerate(stages):
    x = bx + k * (bw + gap)
    box(s, x, 1.95, bw, 2.75, fill=PANEL, name=f"Stage {hd}")
    text(s, x + 0.25, 2.15, bw - 0.5, 0.5, hd, size=19, bold=True, color=col)
    text(s, x + 0.25, 2.75, bw - 0.5, 1.85, body, size=15, color=INK2)
    ax = (L + 1.5) if k == 0 else (x - gap)
    arrow(s, ax + 0.08, 3.32, x - 0.08, 3.32)
box(s, L, 5.25, CW, 1.15, fill="FFFFFF", line=LINE, name="Tagline")
text(s, L + 0.3, 5.25, CW - 0.6, 1.15,
     [[("Province differences become rows of data, ", {"bold": True}),
       ("not branches in code. The yearly job is editing a few reviewed rows.", {})]],
     size=20, color=INK, anchor=MSO_ANCHOR.MIDDLE)

# =============================================================================
# 5. Data model (ER diagram) - presented as a proposal
s = content("Proposed data model: a starting point", "§2 · The metadata  ·  README · The model", notes=(
    "This is the data model the demo uses: eight tables in three groups. It's a proposal, "
    "not a final design. The structure is what makes the process generic: variable is the hub, "
    "the mapping tables are the only place province differences live, and the production "
    "tables are written by the load. The panel on the right lists changes we already expect to "
    "discuss. In the demo the reference and mapping tables are CSV files; in production they "
    "would be maintained tables, with the production versions saved in OneLake."))

ER_COLS = {"Mapping": (0.6, TEAL, "what each province calls it"),
           "Reference": (3.65, NAVY, "what we mean"),
           "Production": (6.7, NAVY, "what was loaded")}
BW = 2.45
for g, (x, col, sub) in ER_COLS.items():
    text(s, x, 1.38, BW, 0.55, [[(g, {"bold": True, "size": 16, "color": col})],
                                [(sub, {"size": 12, "color": INK2})]], size=12)


def entity(name, x, y, fields, hub=False):
    hh, lh = 0.34, 0.2
    h = hh + lh * len(fields) + 0.12
    box(s, x, y, BW, h, fill="FFFFFF", line=TEAL if hub else LINE, name=f"Entity {name}")
    box(s, x, y, BW, hh, fill=TEAL if hub else NAVY, name=f"Entity {name} header")
    text(s, x + 0.12, y, BW - 0.2, hh, name, size=12, bold=True, color="FFFFFF",
         font=MONO, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.12, y + hh + 0.05, BW - 0.2, lh * len(fields) + 0.05, fields,
         size=11, color=INK, font=MONO, line_spacing=0.95)
    return (x, y, BW, h)


def line(x1, y1, x2, y2):
    c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(MUTED)
    c.line.width = Pt(1.5)


TOP = 2.05
mx, rx_, px = ER_COLS["Mapping"][0], ER_COLS["Reference"][0], ER_COLS["Production"][0]
code_ = entity("code", rx_, TOP, ["codeset_id", "code", "label_en", "valid_from / to"])
cset = entity("codeset", rx_, code_[1] + code_[3] + 0.3, ["codeset_id", "codeset_name", "description"])
var = entity("variable", rx_, cset[1] + cset[3] + 0.3,
             ["variable_id", "label · role · type", "codeset_id", "unit", "valid_from / to"], hub=True)
scm = entity("source_column_map", mx, 2.75, ["source_system", "source_column", "variable_id", "valid_from / to"])
sco = entity("source_code_map", mx, scm[1] + scm[3] + 0.35,
             ["source_system", "variable_id", "source_value → code", "valid_from / to"])
ds = entity("dataset", px, TOP, ["dataset_id", "source_system", "reference_year", "source_path"])
dc = entity("dataset_column", px, ds[1] + ds[3] + 0.3, ["dataset_id", "source_column", "variable_id"])
rv = entity("record_value", px, dc[1] + dc[3] + 0.3,
            ["dataset_id · record_id", "variable_id", "source_value", "value"])

cx = rx_ + BW / 2
line(cx, code_[1] + code_[3], cx, cset[1])                     # codeset - code
line(cx, cset[1] + cset[3], cx, var[1])                        # codeset - variable
vy1, vy2 = var[1] + 0.55, var[1] + var[3] - 0.35
line(rx_, vy1, mx + BW, scm[1] + scm[3] / 2)                   # variable - source_column_map
line(rx_, vy2, mx + BW, sco[1] + sco[3] / 2)                   # variable - source_code_map
line(rx_ + BW, vy1, px, dc[1] + dc[3] / 2)                     # variable - dataset_column
line(rx_ + BW, vy2, px, rv[1] + rv[3] / 2)                     # variable - record_value
dx = px + BW / 2
line(dx, ds[1] + ds[3], dx, dc[1])                             # dataset - dataset_column
line(dx, dc[1] + dc[3], dx, rv[1])                             # dataset - record_value (via column)
text(s, 0.6, 6.45, 8.55, 0.35,
     "Lines join tables on shared ids.  variable (teal) is the hub every mapping and value points to.",
     size=11, color=INK2, italic=True)

ppx = 9.55
pw = CW + L - ppx
box(s, ppx, 1.38, pw, 5.4, fill=PANEL, name="Draft panel")
text(s, ppx + 0.25, 1.55, pw - 0.5, 0.75, "Open for discussion", size=18, bold=True)
text(s, ppx + 0.25, 2.1, pw - 0.5, 1.2,
     "This is the model the demo uses. Expect changes as we test it on real deliveries.",
     size=14, color=INK2)
text(s, ppx + 0.25, 3.25, pw - 0.5, 3.4, [
    "An “ignore” option for known extra columns",
    "Recode when queried, rather than at load",
    "More than one record type (person, dwelling)",
    "Production metadata tables kept in OneLake",
], size=14, color=INK, bullets=True, space_after=8)

# =============================================================================
# 6. Rows
s = content("Everything province-specific is a row", "§2 · The metadata", notes=(
    "Three groups of tables. Reference says what we mean, mapping says what each province "
    "calls it, and production is written by the pipeline. On the right are real rows from "
    "the demo's metadata/source_column_map.csv and source_code_map.csv (CSV in the demo; maintained tables in OneLake in production). Ontario's 2025 rename is two "
    "dated rows, not an if-statement. Nova Scotia already uses our codes, so it has no "
    "code-map rows and its values pass straight through."))
groups = [
    ("Reference", "what we mean", "codeset · code · variable", "Owned by subject-matter experts"),
    ("Mapping", "what each province calls it", "source_column_map · source_code_map", "Owned by the data team"),
    ("Production", "what was loaded", "dataset · dataset_column · record_value", "Written by the pipeline"),
]
for i, (g, what, tables, owner) in enumerate(groups):
    y = 1.5 + i * 1.7
    box(s, L, y, 5.5, 1.5, fill=PANEL, name=f"Group {g}")
    text(s, L + 0.25, y + 0.15, 5.0, 1.25, [
        [(g, {"bold": True, "size": 19, "color": TEAL if g == "Mapping" else INK}),
         (f"  {what}", {"color": INK2, "size": 15})],
        [(tables, {"font": MONO, "size": 13, "color": INK})],
        [(owner, {"color": INK2})],
    ], size=14, space_after=5)
rx, rw = 6.5, CW + L - 6.5
text(s, rx, 1.5, rw, 0.45, "A column rename is two dated rows", size=18, bold=True)
table(s, rx, 2.0, rw, [0.75, 2.4, 1.55, 0.67, 0.86], [
    ["prov", "source_column", "variable", "from", "to"],
    ["ON", "shelter_cost_mth", "SHELTER_COST", "2020", "2024"],
    ["ON", "monthly_shelter_cost", "SHELTER_COST", "2025", "–"],
], size=13, mono_cols=(1, 2))
text(s, rx, 3.6, rw, 0.45, "A code translation is one row per value", size=18, bold=True)
table(s, rx, 4.1, rw, [0.75, 1.75, 1.6, 1.2, 0.93], [
    ["prov", "variable", "source value", "code", "from"],
    ["ON", "TENURE", "O", "1  Owner", "2020"],
    ["ON", "TENURE", "R", "2  Renter", "2020"],
    ["ON", "TENURE", "X", "9  Not stated", "2020"],
], size=13, mono_cols=(1, 2))
text(s, rx, 5.95, rw, 0.6, "Nova Scotia already uses our codes, so it needs no code-map rows at all.",
     size=14, italic=True, color=INK2)

# =============================================================================
# 6. Annual changes
s = content("Yearly changes become small metadata edits", "§2 · The metadata  ·  §8 · The annual cycle", notes=(
    "The whole sample model is 70 rows of metadata, and no province-specific code. "
    "The table on the right is the maintenance playbook from the README. Time is handled by one "
    "rule: a row applies when the reference year falls inside its valid_from / valid_to range."))
for i, (num, label) in enumerate([("70", "rows of metadata hold every\nprovince-specific fact"),
                                  ("0", "lines of province-specific\ncode")]):
    y = 1.5 + i * 2.45
    text(s, L, y, 3.6, 1.2, num, size=72, bold=True, color=TEAL, anchor=MSO_ANCHOR.BOTTOM)
    text(s, L, y + 1.25, 3.6, 0.8, label, size=15, color=INK2)
tx, tw = 4.7, CW + L - 4.7
table(s, tx, 1.6, tw, [3.2, tw - 3.2], [
    ["When this happens…", "…you edit this"],
    ["A new province joins", "Add its column (and code) mapping rows"],
    ["A province renames a column", "End the old row, add a new one"],
    ["A new variable is collected", "Add a variable row, plus a mapping per province"],
    ["A province sends a new code", "Add a code-map row (QA tells you when)"],
    ["A code is retired", "Set an end year on the code row"],
], size=15, row_h=0.58, first_col_bold=True)
text(s, tx, 5.35, tw, 1.0,
     [[("One rule handles time: ", {"bold": True}),
       ("a row applies when the reference year is between its ", {}),
       ("valid_from_year", {"font": MONO, "size": 14}), (" and ", {}),
       ("valid_to_year", {"font": MONO, "size": 14}), (".", {})]],
     size=15, color=INK2)

# =============================================================================
# 7. Lineage
s = content("We can see where every value came from", "§4 · Lineage", notes=(
    "Notebook section 4. Every load records each physical column it saw and what it mapped to. "
    "This map is drawn straight from that table. Highlighted cells are the churn the mapping "
    "tables absorbed: Ontario's rename, period built arriving in 2025 under three different "
    "names, and BC's unmapped column."))
iw, ih = image(s, "fig02.png", L, 1.4, h=5.3, alt="Lineage map: source column per variable, province and year")
nx = L + iw + 0.35
nw = CW + L - nx
text(s, nx, 1.6, nw, 4.8, [
    [("Recorded automatically ", {"bold": True}), ("on every load, for every province and year", {})],
    [("Blue: ", {"bold": True}), ("a renamed column (Ontario, 2025)", {})],
    [("Green: ", {"bold": True}), ("a new variable (period built, 2025)", {})],
    [("Red: ", {"bold": True}), ("delivered but not mapped (BC)", {})],
], size=15, color=INK2, space_after=14)

# =============================================================================
# 8. Harmonised
s = content("One labelled standard for every province", "§5 · Harmonised output", notes=(
    "Notebook section 5. The same chart as slide 2, now drawn from the harmonised view. "
    "The query only says variable_id = 'TENURE'; there's no province dictionary. The original "
    "value is kept next to the canonical one for audit. BC's code 3 had no mapping, so it's "
    "shown as unmapped rather than guessed."))
_, ih = image(s, "fig03.png", L, 1.4, w=CW, alt="Harmonised tenure by province, 2025")
pts = [
    ("Three codings in", "O/R/X, 1/2/3 and 1/2/9 become Owner, Renter and Not stated"),
    ("Nothing lost", "Each value keeps what the province sent, for audit"),
    ("Nothing guessed", "A code with no mapping is flagged, not forced into a category"),
]
cw3 = (CW - 2 * 0.3) / 3
for k, (hd, body) in enumerate(pts):
    x = L + k * (cw3 + 0.3)
    box(s, x, 5.45, cw3, 1.15, fill=PANEL)
    text(s, x + 0.2, 5.55, cw3 - 0.4, 1.0, [[(hd, {"bold": True, "size": 16})], body],
         size=14, space_after=3)

# =============================================================================
# 9. Analysis
s = content("Analysis no longer needs province logic", "§6 · Analysis", notes=(
    "Notebook section 6. Every chart here runs one query over all provinces and years, using "
    "only canonical names and the code tables. Ontario's 2025 column rename and BC's integer "
    "codes needed no special handling. When 2026 and a new province arrive, they just show up "
    "in the same chart (slide 13)."))
iw, ih = image(s, "fig04.png", L, 1.45, w=7.5, alt="Dwelling type share by province and year")
image(s, "fig05.png", L, 1.45 + ih + 0.15, w=7.5, alt="Median monthly shelter cost by tenure, province and year")
cx = L + 7.5 + 0.5
cwid = CW + L - cx
text(s, cx, 1.5, cwid, 0.45, "The whole query", size=18, bold=True)
box(s, cx, 2.05, cwid, 1.9, fill=PANEL, name="Query")
text(s, cx + 0.2, 2.15, cwid - 0.3, 1.7, [
    "SELECT source_system,", "       value_label,", "       count(*)",
    "FROM v_record_labelled", "WHERE variable_id", "    = 'TENURE'", "GROUP BY ALL",
], size=12, font=MONO, color=INK, anchor=MSO_ANCHOR.MIDDLE)
text(s, cx, 4.2, cwid, 2.4, [
    [("No ", {}), ("CASE province WHEN …", {"font": MONO, "size": 13}), (" anywhere.", {})],
    "Ontario's renamed column and BC's numeric codes needed no special handling.",
    "New provinces and years appear automatically.",
], size=15, color=INK2, space_after=10)

# =============================================================================
# 10. QA
s = content("Quality checks say exactly what to fix", "§7 · QA", notes=(
    "Notebook section 7. Because the load is generic, so are the checks. Every build runs all "
    "five. Each one points at a specific metadata edit, so QA results turn straight into a "
    "to-do list. In the sample data two planted problems are caught: BC's extra column and BC's "
    "unmapped tenure code."))
table(s, L, 1.5, CW, [2.6, 4.9, CW - 7.5], [
    ["Check", "Fires when", "Fix"],
    ["Unmapped columns", "A province sent a column nobody mapped", "Add a column-map row"],
    ["Missing variables", "An expected variable isn't in the file", "Usually a rename: end the old row, add a new one"],
    ["Unmapped values", "A province sent a code with no mapping", "Add a code-map row"],
    ["Invalid codes", "A value isn't in our code list for that year", "Fix the target code or its dates"],
    ["Bad numerics", "A number column has values that don't parse", "Raise with the province"],
], size=15, row_h=0.6, first_col_bold=True)
box(s, L, 5.4, CW, 1.15, fill=PANEL, name="QA tip")
text(s, L + 0.3, 5.4, CW - 0.6, 1.15,
     [[("Read together, they explain themselves: ", {"bold": True}),
       ("an unmapped column plus a missing variable in the same file is almost always a rename.", {})]],
     size=16, anchor=MSO_ANCHOR.MIDDLE)

# =============================================================================
# 11. Annual cycle
s = content("The 2026 cycle, in practice", "§8 · The annual cycle", notes=(
    "Notebook section 8 simulates a realistic 2026: Ontario renames tenure, BC still sends "
    "its new code, and Manitoba joins with its own names and codes. The first load doesn't "
    "crash; QA lists every gap. After 24 metadata rows, everything is clean except BC's "
    "internal column, which needs a decision (see next steps). No Python changed."))
flow = [("Load", "Drop in the new files and run the build"),
        ("Read QA", "Each failing check names what to map"),
        ("Edit rows", "Update the mapping tables; save the reviewed version to OneLake"),
        ("Reload", "Run again until QA is clean")]
for k, (hd, body) in enumerate(flow):
    x = L + k * (cw4 + 0.3)
    c = box(s, x, 1.5, 0.55, 0.55, fill=TEAL, shape=MSO_SHAPE.OVAL)
    text(s, x, 1.5, 0.55, 0.55, str(k + 1), size=18, bold=True, color="FFFFFF",
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.7, 1.5, cw4 - 0.7, 0.55, hd, size=18, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x, 2.15, cw4 - 0.1, 0.7, body, size=14, color=INK2)
iw, ih = image(s, "fig07.png", L, 3.05, h=3.6, alt="QA issue counts for the 2026 load before and after metadata edits")
sx = L + iw + 0.5
sw = CW + L - sx
for i, (num, label) in enumerate([("24", "metadata rows changed"), ("0", "lines of Python changed")]):
    y = 3.05 + i * 1.45
    text(s, sx, y, sw, 0.9, num, size=60, bold=True, color=TEAL, anchor=MSO_ANCHOR.BOTTOM)
    text(s, sx, y + 0.9, sw, 0.45, label, size=15, color=INK2)
text(s, sx, 6.0, sw, 0.7, "Ontario rename · BC new code · Manitoba joins",
     size=14, italic=True, color=INK2)

# =============================================================================
# 12. New province
s = content("A new province, without new code", "§8 · The annual cycle", notes=(
    "The lineage map after the 2026 edits. Manitoba arrived with completely different column "
    "names and codes. It took 8 column mappings and 12 code mappings, and from then on it "
    "flows into every view, chart and QA check like the others."))
iw, ih = image(s, "fig08.png", L, 1.4, w=CW, alt="Lineage map for 2024 to 2026 including Manitoba")
text(s, L, 1.4 + ih + 0.25, CW, 0.8,
     [[("Manitoba: ", {"bold": True}),
       ("its own column names and codes, handled with 8 column mappings and 12 code mappings. "
        "It then appears in every view, chart and check.", {})]],
     size=16, color=INK2)

# =============================================================================
# 13. Before / after
s = content("What changes for the team", notes=(
    "The summary. Left is how the work has typically been done: per province, by hand, with the "
    "knowledge spread out. Right is the same work as a maintained database that is handled "
    "the same way for every province, every year."))
table(s, L, 1.5, CW, [CW / 2, CW / 2], [
    ["Today: per province, by hand", "With the model: one consistent process"],
    ["A separate script and recodes for each province", "Mapping rows in one shared database"],
    ["Renames handled with year-specific code", "A dated row: valid from / valid to"],
    ["New province = a new code path", "New province = new mapping rows"],
    ["Knowledge spread across people and files", "One maintained set of tables, kept in OneLake"],
    ["Problems found late, by analysts", "Problems flagged at load, with the fix"],
    ["Lineage rebuilt by hand", "Lineage recorded on every load"],
], size=17, row_h=0.74)

# =============================================================================
# 14. Next steps
s = dark(notes=(
    "Proposed next steps. The design points in item 3 come from the review in the README, "
    "under Known limitations: an explicit 'ignore this column' mapping, reloading earlier years "
    "when a mapping change reaches back, and provinces that send several files per year."))
text(s, L + 0.2, 0.7, 11.5, 0.9, "Next steps", size=40, bold=True, color="FFFFFF",
     anchor=MSO_ANCHOR.MIDDLE)
nsteps = [
    ("Port", "Move the model into our local repo with the real metadata folders"),
    ("Prove", "Load one year of real deliveries and compare with current outputs"),
    ("Decide", "Agree the data model, including ignored columns, reload rules and multi-file deliveries"),
    ("Automate", "Run the QA checks in CI so a failing check blocks a release"),
    ("Own", "Keep production metadata in OneLake, with a named owner and a review step"),
]
for i, (hd, body) in enumerate(nsteps):
    y = 1.95 + i * 0.82
    text(s, L + 0.2, y, 0.5, 0.6, str(i + 1), size=24, bold=True, color=ICE, anchor=MSO_ANCHOR.MIDDLE)
    text(s, L + 0.8, y, 11.0, 0.6, [[(hd + "  ", {"bold": True, "color": "FFFFFF"}), (body, {})]],
         size=18, color=ICE, anchor=MSO_ANCHOR.MIDDLE)
text(s, L + 0.2, 6.4, 11.5, 0.4,
     [[("Try it: ", {"bold": True}), ("uv sync --group demo", {"font": MONO, "size": 13}),
       (", then open ", {}), ("notebooks/walkthrough.ipynb", {"font": MONO, "size": 13})]],
     size=14, color=ICE)

prs.core_properties.title = "One metadata model for every province"
prs.core_properties.author = "Chris Birrell"
OUT.parent.mkdir(parents=True, exist_ok=True)
prs.save(OUT)
print("wrote", OUT)
