"""Emit the shape model as a native Visio .vsdx (OPC zip), an SVG preview and CSV lists."""
import csv, math, os, textwrap, zipfile
from xml.sax.saxutils import escape, quoteattr

from pidlib import LAYERS, PT, shapes, instr_index, line_list, valve_list

NS = ('xmlns="http://schemas.microsoft.com/office/visio/2012/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xml:space="preserve"')


def f(v):
    return f"{v:.5f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def cell(n, v, extra=""):
    return f'<Cell N="{n}" V={quoteattr(str(v))}{extra}/>'


def geom_xml(ix, rows, nofill, noline):
    out = [f'<Section N="Geometry" IX="{ix}">', cell("NoFill", 1 if nofill else 0), cell("NoLine", 1 if noline else 0),
           cell("NoShow", 0), cell("NoSnap", 0), cell("NoQuickDrag", 0)]
    for ri, r in enumerate(rows, start=1):
        if r[0] == "M":
            out.append(f'<Row T="MoveTo" IX="{ri}">{cell("X", f(r[1]))}{cell("Y", f(r[2]))}</Row>')
        elif r[0] == "L":
            out.append(f'<Row T="LineTo" IX="{ri}">{cell("X", f(r[1]))}{cell("Y", f(r[2]))}</Row>')
        elif r[0] == "A":
            out.append(f'<Row T="ArcTo" IX="{ri}">{cell("X", f(r[1]))}{cell("Y", f(r[2]))}{cell("A", f(r[3]))}</Row>')
        elif r[0] == "E":
            cx, cy, rx, ry = r[1:]
            out.append(f'<Row T="Ellipse" IX="{ri}">{cell("X", f(cx))}{cell("Y", f(cy))}{cell("A", f(cx + rx))}'
                       f'{cell("B", f(cy))}{cell("C", f(cx))}{cell("D", f(cy + ry))}</Row>')
    out.append("</Section>")
    return "".join(out)


def shape_xml(s):
    name = quoteattr(s.name or "Shape")
    parts = [f'<Shape ID="{s.id}" NameU={name} Name={name} Type="Shape" LineStyle="0" FillStyle="0" TextStyle="0">',
             cell("PinX", f(s.x0 + s.w / 2)), cell("PinY", f(s.y0 + s.h / 2)), cell("Width", f(s.w)), cell("Height", f(s.h)),
             cell("LocPinX", f(s.w / 2), ' F="Width*0.5"'), cell("LocPinY", f(s.h / 2), ' F="Height*0.5"'),
             cell("Angle", 0), cell("FlipX", 0), cell("FlipY", 0), cell("ResizeMode", 0),
             cell("LineWeight", f(s.weight * PT)), cell("LineColor", s.line), cell("LinePattern", s.pattern),
             cell("EndArrow", s.end_arrow), cell("BeginArrow", s.begin_arrow), cell("EndArrowSize", 1), cell("BeginArrowSize", 1),
             cell("FillForegnd", s.fill or "#FFFFFF"), cell("FillPattern", 1 if s.fill else 0),
             cell("LayerMember", str(LAYERS.index(s.layer))),
             cell("VerticalAlign", s.valign), cell("LeftMargin", 0.02), cell("RightMargin", 0.02),
             cell("TopMargin", 0.02), cell("BottomMargin", 0.02)]
    parts.append(f'<Section N="Character"><Row IX="0">{cell("Font", "Arial")}{cell("Color", s.text_color)}'
                 f'{cell("Style", 1 if s.bold else 0)}{cell("Size", f(s.size * PT))}</Row></Section>')
    parts.append(f'<Section N="Paragraph"><Row IX="0">{cell("HorzAlign", s.halign)}{cell("SpLine", -1.1)}</Row></Section>')
    if s.props:
        rows = "".join(f'<Row N={quoteattr(k)}>{cell("Value", v, " U=\"STR\"")}{cell("Label", k)}{cell("Type", 0)}'
                       f'{cell("Invisible", 0)}</Row>' for k, v in s.props.items())
        parts.append(f'<Section N="Property">{rows}</Section>')
    for ix, (rows, nofill, noline) in enumerate(s.geoms):
        parts.append(geom_xml(ix, rows, nofill, noline))
    if s.text:
        parts.append(f"<Text>{escape(s.text)}</Text>")
    parts.append("</Shape>")
    return "".join(parts)


def layer_xml():
    rows = "".join(
        f'<Row IX="{i}">{cell("Name", n)}{cell("Color", 255)}{cell("Status", 0)}{cell("Visible", 1)}{cell("Print", 1)}'
        f'{cell("Active", 0)}{cell("Lock", 0)}{cell("Snap", 1)}{cell("Glue", 1)}{cell("NameUniv", n)}{cell("ColorTrans", 0)}</Row>'
        for i, n in enumerate(LAYERS))
    return f'<Section N="Layer">{rows}</Section>'


_STYLE_CELLS = [
    ("EnableLineProps", 1), ("EnableFillProps", 1), ("EnableTextProps", 1), ("HideForApply", 0),
    ("LineWeight", 0.01), ("LineColor", "#000000"), ("LinePattern", 1), ("Rounding", 0), ("EndArrowSize", 2),
    ("BeginArrow", 0), ("EndArrow", 0), ("LineCap", 0), ("BeginArrowSize", 2), ("LineColorTrans", 0), ("CompoundType", 0),
    ("FillForegnd", "#FFFFFF"), ("FillBkgnd", "#000000"), ("FillPattern", 1), ("ShdwForegnd", "#000000"), ("ShdwPattern", 0),
    ("FillForegndTrans", 0), ("FillBkgndTrans", 0), ("ShdwForegndTrans", 0), ("ShapeShdwType", 0), ("ShapeShdwOffsetX", 0),
    ("ShapeShdwOffsetY", 0), ("ShapeShdwObliqueAngle", 0), ("ShapeShdwScaleFactor", 1), ("ShapeShdwBlur", 0), ("ShapeShdwShow", 0),
    ("LeftMargin", 0.05555556), ("RightMargin", 0.05555556), ("TopMargin", 0.05555556), ("BottomMargin", 0.05555556),
    ("VerticalAlign", 1), ("DefaultTabStop", 0.5), ("TextDirection", 0), ("TextBkgndTrans", 0),
    ("SelectMode", 1), ("DisplayMode", 2), ("IsDropTarget", 0), ("IsSnapTarget", 1), ("IsTextEditTarget", 1),
    ("DontMoveChildren", 0), ("ShapePermeableX", 0), ("ShapePermeableY", 0), ("ShapePermeablePlace", 0), ("Relationships", 0),
    ("ShapeFixedCode", 0), ("ShapePlowCode", 0), ("ShapeRouteStyle", 0), ("ShapePlaceStyle", 0), ("ConFixedCode", 0),
    ("ConLineJumpCode", 0), ("ConLineJumpStyle", 0), ("ConLineJumpDirX", 0), ("ConLineJumpDirY", 0), ("ShapePlaceFlip", 0),
    ("ConLineRouteExt", 0), ("ShapeSplit", 0), ("ShapeSplittable", 0), ("DisplayLevel", 0),
    ("NoObjHandles", 0), ("NonPrinting", 0), ("NoCtlHandles", 0), ("NoAlignBox", 0), ("UpdateAlignBox", 0), ("HideText", 0),
    ("DynFeedback", 0), ("GlueType", 0), ("WalkPreference", 0), ("ObjType", 0), ("Comment", ""), ("IsDropSource", 0),
    ("NoLiveDynamics", 0), ("LocalizeMerge", 0), ("NoProofing", 0), ("Calendar", 0), ("LangID", "en-US"), ("ShapeKeywords", ""),
    ("DropOnPageScale", 1), ("HelpTopic", ""), ("Copyright", ""), ("LayerMember", ""),
    ("Gamma", 1), ("Contrast", 0.5), ("Brightness", 0.5), ("Sharpen", 0), ("Blur", 0), ("Denoise", 0), ("Transparency", 0),
    ("LockWidth", 0), ("LockHeight", 0), ("LockMoveX", 0), ("LockMoveY", 0), ("LockAspect", 0), ("LockDelete", 0),
    ("LockBegin", 0), ("LockEnd", 0), ("LockRotate", 0), ("LockCrop", 0), ("LockVtxEdit", 0), ("LockTextEdit", 0),
    ("LockFormat", 0), ("LockGroup", 0), ("LockCalcWH", 0), ("LockSelect", 0), ("LockCustProp", 0), ("LockFromGroupFormat", 0),
    ("LockThemeColors", 0), ("LockThemeEffects", 0), ("LockThemeConnectors", 0), ("LockThemeFonts", 0), ("LockThemeIndex", 0),
    ("LockReplace", 0), ("LockVariation", 0),
    ("TxtPinX", 0), ("TxtPinY", 0), ("TxtWidth", 0), ("TxtHeight", 0), ("TxtLocPinX", 0), ("TxtLocPinY", 0), ("TxtAngle", 0),
    ("QuickStyleLineColor", 100), ("QuickStyleFillColor", 100), ("QuickStyleShadowColor", 100), ("QuickStyleFontColor", 100),
    ("QuickStyleLineMatrix", 100), ("QuickStyleFillMatrix", 100), ("QuickStyleEffectsMatrix", 100), ("QuickStyleFontMatrix", 100),
    ("QuickStyleType", 0), ("QuickStyleVariation", 0),
]


def style0():
    return ('<StyleSheet ID="0" NameU="No Style" IsCustomNameU="1" Name="No Style" IsCustomName="1">'
            + "".join(cell(n, v) for n, v in _STYLE_CELLS)
            + f'<Section N="Character"><Row IX="0">{cell("Font", "Arial")}{cell("Color", "#000000")}{cell("Style", 0)}{cell("Case", 0)}'
              f'{cell("Pos", 0)}{cell("FontScale", 1)}{cell("Size", 0.1111111)}{cell("DblUnderline", 0)}{cell("Overline", 0)}'
              f'{cell("Strikethru", 0)}{cell("Letterspace", 0)}{cell("ColorTrans", 0)}{cell("AsianFont", "Arial")}'
              f'{cell("ComplexScriptFont", "Arial")}{cell("ComplexScriptSize", -1)}{cell("LangID", "en-US")}</Row></Section>'
            + f'<Section N="Paragraph"><Row IX="0">{cell("IndFirst", 0)}{cell("IndLeft", 0)}{cell("IndRight", 0)}{cell("SpLine", -1.2)}'
              f'{cell("SpBefore", 0)}{cell("SpAfter", 0)}{cell("HorzAlign", 1)}{cell("Bullet", 0)}{cell("BulletStr", "")}'
              f'{cell("BulletFont", 0)}{cell("LocalizeBulletFont", 0)}{cell("TextPosAfterBullet", 0)}{cell("Flags", 0)}'
              f'{cell("BulletFontSize", -1)}</Row></Section>'
            + '<Section N="Tabs"><Row IX="0"/></Section></StyleSheet>')


def write_vsdx(path, page_name, page_w, page_h, title):
    page1 = (f'<?xml version="1.0" encoding="utf-8"?><PageContents {NS}><Shapes>'
             + "".join(shape_xml(s) for s in shapes) + "</Shapes></PageContents>")
    ps = [("PageWidth", f(page_w)), ("PageHeight", f(page_h)), ("ShdwOffsetX", 0.125), ("ShdwOffsetY", -0.125),
          ("DrawingSizeType", 3), ("DrawingScaleType", 0), ("InhibitSnap", 0), ("PageLockReplace", 0), ("PageLockDuplicate", 0),
          ("UIVisibility", 0), ("ShdwType", 0), ("ShdwObliqueAngle", 0), ("ShdwScaleFactor", 1), ("DrawingResizeType", 1),
          ("PageShapeSplit", 1), ("PaperKind", 8), ("PrintPageOrientation", 2), ("PageLeftMargin", 0.25), ("PageRightMargin", 0.25),
          ("PageTopMargin", 0.25), ("PageBottomMargin", 0.25), ("ScaleX", 1), ("ScaleY", 1), ("PagesX", 1), ("PagesY", 1),
          ("CenterX", 0), ("CenterY", 0), ("OnPage", 0), ("PrintGrid", 0)]
    pages = (f'<?xml version="1.0" encoding="utf-8"?><Pages {NS}>'
             f'<Page ID="0" NameU="{page_name}" Name="{page_name}" ViewScale="0.35" ViewCenterX="{f(page_w / 2)}" ViewCenterY="{f(page_h / 2)}">'
             f'<PageSheet LineStyle="0" FillStyle="0" TextStyle="0">' + "".join(cell(n, v) for n, v in ps)
             + cell("PageScale", 1, ' U="IN_F"') + cell("DrawingScale", 1, ' U="IN_F"')
             + f'{layer_xml()}</PageSheet><Rel r:id="rId1"/></Page></Pages>')
    document = (f'<?xml version="1.0" encoding="utf-8"?><VisioDocument {NS}>'
                '<DocumentSettings TopPage="0" DefaultTextStyle="0" DefaultLineStyle="0" DefaultFillStyle="0" DefaultGuideStyle="0">'
                '<GlueSettings>9</GlueSettings><SnapSettings>65847</SnapSettings><SnapExtensions>34</SnapExtensions>'
                '<SnapAngles/><DynamicGridEnabled>1</DynamicGridEnabled><ProtectStyles>0</ProtectStyles><ProtectShapes>0</ProtectShapes>'
                '<ProtectMasters>0</ProtectMasters><ProtectBkgnds>0</ProtectBkgnds></DocumentSettings>'
                '<Colors><ColorEntry IX="0" RGB="#000000"/><ColorEntry IX="1" RGB="#FFFFFF"/></Colors>'
                '<FaceNames><FaceName NameU="Arial" UnicodeRanges="-536859905 -1073711037 9 0" CharSets="1073742335 -65536" '
                'Panos="2 11 6 4 2 2 2 2 2 4" Flags="325"/></FaceNames>'
                f'<StyleSheets>{style0()}</StyleSheets>'
                f'<DocumentSheet NameU="TheDoc" Name="TheDoc" LineStyle="0" FillStyle="0" TextStyle="0">{cell("OutputFormat", 0)}'
                f'{cell("LockPreview", 0)}{cell("AddMarkup", 0)}{cell("ViewMarkup", 0)}{cell("PreviewQuality", 0)}{cell("PreviewScope", 0)}'
                f'{cell("DocLangID", "en-US")}</DocumentSheet></VisioDocument>')
    content_types = (
        '<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/visio/document.xml" ContentType="application/vnd.ms-visio.drawing.main+xml"/>'
        '<Override PartName="/visio/pages/pages.xml" ContentType="application/vnd.ms-visio.pages+xml"/>'
        '<Override PartName="/visio/pages/page1.xml" ContentType="application/vnd.ms-visio.page+xml"/>'
        '<Override PartName="/visio/windows.xml" ContentType="application/vnd.ms-visio.windows+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        '</Types>')
    root_rels = (
        '<?xml version="1.0" encoding="utf-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/document" Target="visio/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>')
    doc_rels = (
        '<?xml version="1.0" encoding="utf-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/pages" Target="pages/pages.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.microsoft.com/visio/2010/relationships/windows" Target="windows.xml"/>'
        '</Relationships>')
    pages_rels = (
        '<?xml version="1.0" encoding="utf-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/page" Target="page1.xml"/></Relationships>')
    windows = (
        f'<?xml version="1.0" encoding="utf-8"?><Windows ClientWidth="1600" ClientHeight="900" {NS}>'
        f'<Window ID="0" WindowType="Drawing" WindowState="1073741824" WindowLeft="0" WindowTop="0" WindowWidth="1600" WindowHeight="900" '
        f'ContainerType="Page" Page="0" ViewScale="0.35" ViewCenterX="{f(page_w / 2)}" ViewCenterY="{f(page_h / 2)}">'
        '<ShowRulers>1</ShowRulers><ShowGrid>1</ShowGrid><ShowPageBreaks>0</ShowPageBreaks><ShowGuides>1</ShowGuides>'
        '<ShowConnectionPoints>1</ShowConnectionPoints><GlueSettings>9</GlueSettings><SnapSettings>65847</SnapSettings>'
        '<SnapExtensions>34</SnapExtensions><SnapAngles/><DynamicGridEnabled>1</DynamicGridEnabled>'
        '<TabSplitterPos>0.5</TabSplitterPos></Window></Windows>')
    core = (
        '<?xml version="1.0" encoding="utf-8"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<dc:title>{escape(title)}</dc:title><dc:creator>Senior P&amp;I Engineer</dc:creator>'
        '<dcterms:created xsi:type="dcterms:W3CDTF">2026-09-23T00:00:00Z</dcterms:created>'
        '<dcterms:modified xsi:type="dcterms:W3CDTF">2026-09-23T00:00:00Z</dcterms:modified></cp:coreProperties>')
    app = (
        '<?xml version="1.0" encoding="utf-8"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Microsoft Visio</Application>'
        '<AppVersion>16.0000</AppVersion></Properties>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("docProps/core.xml", core)
        z.writestr("docProps/app.xml", app)
        z.writestr("visio/document.xml", document)
        z.writestr("visio/_rels/document.xml.rels", doc_rels)
        z.writestr("visio/windows.xml", windows)
        z.writestr("visio/pages/pages.xml", pages)
        z.writestr("visio/pages/_rels/pages.xml.rels", pages_rels)
        z.writestr("visio/pages/page1.xml", page1)


# ------------------------------------------------------------------ SVG
def write_svg(path, page_w, page_h, scale=40.0):
    S = scale

    def X(x):
        return x * S

    def Y(y):
        return (page_h - y) * S

    def arc_path(x0, y0, x1, y1, bow):
        c = math.hypot(x1 - x0, y1 - y0)
        if c == 0 or bow == 0:
            return f"L{X(x1):.2f} {Y(y1):.2f}"
        r = (c * c / 4 + bow * bow) / (2 * abs(bow))
        large = 1 if abs(bow) > c / 2 else 0
        sweep = 1 if bow < 0 else 0  # same convention as libvisio (positive A bows right of travel)
        return f"A{r * S:.2f} {r * S:.2f} 0 {large} {sweep} {X(x1):.2f} {Y(y1):.2f}"

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{page_w * S:.0f}" height="{page_h * S:.0f}" '
           f'viewBox="0 0 {page_w * S:.0f} {page_h * S:.0f}" font-family="Arial, Liberation Sans, sans-serif">',
           '<rect width="100%" height="100%" fill="white"/>']
    for s in shapes:
        dash = {1: "", 2: "6,4", 3: "2,3", 0: ""}[s.pattern]
        stroke = "none" if s.pattern == 0 else s.line
        sw = s.weight * PT * S
        for ix, (rows, nofill, noline) in enumerate(s.geoms):
            fill = "none" if (nofill or not s.fill) else s.fill
            st = "none" if noline else stroke
            if rows[0][0] == "E":
                _, cx, cy, rx, ry = rows[0]
                out.append(f'<ellipse cx="{X(s.x0 + cx):.2f}" cy="{Y(s.y0 + cy):.2f}" rx="{rx * S:.2f}" ry="{ry * S:.2f}" '
                           f'fill="{fill}" stroke="{st}" stroke-width="{sw:.2f}"/>')
                continue
            d, prev = [], None
            for r in rows:
                if r[0] == "M":
                    d.append(f"M{X(s.x0 + r[1]):.2f} {Y(s.y0 + r[2]):.2f}")
                elif r[0] == "L":
                    d.append(f"L{X(s.x0 + r[1]):.2f} {Y(s.y0 + r[2]):.2f}")
                elif r[0] == "A":
                    d.append(arc_path(s.x0 + prev[1], s.y0 + prev[2], s.x0 + r[1], s.y0 + r[2], r[3]))
                prev = r
            out.append(f'<path d="{" ".join(d)}" fill="{fill}" stroke="{st}" stroke-width="{sw:.2f}" stroke-linejoin="round"'
                       + (f' stroke-dasharray="{dash}"' if dash else "") + '/>')
            if s.end_arrow and nofill and ix == len(s.geoms) - 1 and len(rows) >= 2:
                (x1, y1), (x2, y2) = rows[-2][1:3], rows[-1][1:3]
                ang = math.atan2(-(y2 - y1), x2 - x1)
                ax, ay, L = X(s.x0 + x2), Y(s.y0 + y2), max(6.0, sw * 3.2)
                p1 = (ax - L * math.cos(ang - 0.42), ay - L * math.sin(ang - 0.42))
                p2 = (ax - L * math.cos(ang + 0.42), ay - L * math.sin(ang + 0.42))
                out.append(f'<path d="M{ax:.2f} {ay:.2f} L{p1[0]:.2f} {p1[1]:.2f} L{p2[0]:.2f} {p2[1]:.2f} z" fill="{s.line}" stroke="none"/>')
        if s.text:
            maxc = max(8, int((s.w - 0.1) / (s.size * PT * 0.5)))
            lines = []
            for ln in s.text.split("\n"):
                lines += textwrap.wrap(ln, maxc) or [""]
            fs = s.size * PT * S
            lh = fs * 1.15
            anchor = {0: "start", 1: "middle", 2: "end"}[s.halign]
            tx = {0: s.x0 + 0.03, 1: s.x0 + s.w / 2, 2: s.x0 + s.w - 0.03}[s.halign]
            top = Y(s.y0 + s.h) + 0.03 * S if s.valign == 0 else Y(s.y0 + s.h / 2) - lh * len(lines) / 2
            for i, ln in enumerate(lines):
                out.append(f'<text x="{X(tx):.2f}" y="{top + lh * (i + 0.8):.2f}" font-size="{fs:.2f}" text-anchor="{anchor}" '
                           f'fill="{s.text_color}"' + (' font-weight="bold"' if s.bold else "") + f'>{escape(ln)}</text>')
    out.append("</svg>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


def write_csvs(prefix):
    def dump(suffix, header, rows):
        with open(prefix + suffix, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)

    dump("_InstrumentIndex.csv", ["Tag (ISA-5.1)", "Instrument type", "Service", "Location / Line", "Range / Setpoint", "I/O", "Alarm / Interlock / Note"], instr_index)
    dump("_LineList.csv", ["Line No.", "From", "To", "Size", "Material / Class", "Fluid", "Operating condition", "Note"], line_list)
    dump("_ValveList.csv", ["Tag", "Valve type", "Size", "Fail position", "Service", "Note"], valve_list)
