"""Keep only the HTML a reader would actually see, so hidden text never reaches the model.

Walks the tree like a browser would, carrying each element's inherited font size, text color and background:
- hidden: the hidden attribute, display:none, visibility:hidden, near-zero opacity or box size, off-screen
  positioning, clip / clip-path, mso-hide
- unreadable: effective font size under MIN_FONT_PX (nested 10% inside 10% compounds), or text whose color
  is invisible or nearly the same as its background (white-on-white)
- <style> rules count as well as inline styles (tag / .class / #id selectors, matched on the last part).

When a rule can't be evaluated (calc(), var()) it fails closed. Known limits: specificity is approximate,
pseudo-classes (:hover, :nth-child) are ignored, images and background-images aren't seen.
"""
import colorsys
import re
from dataclasses import dataclass
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
INVISIBLE_TEXT = {"script", "style", "title", "iframe", "noembed", "noframes"}  # raw-text content never shown
MIN_FONT_PX = 3
MIN_CONTRAST = 1.3  # WCAG contrast ratio; #ccc on white is 1.6, #f5f5f5 on white is 1.1
BASE_FONT_PX = 16

_COMPUTED = re.compile(r"\b(calc|var|min|max|clamp|env|attr)\s*\(", re.I)
_NUMBER = re.compile(r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)\s*(%|[a-z]*)", re.I)
_PX = {"": 1, "px": 1, "pt": 4 / 3, "pc": 16, "in": 96, "cm": 37.8, "mm": 3.78, "q": 0.945,
       "vw": 4, "vh": 7, "vmin": 4, "vmax": 7}  # viewport ≈ a 400×700 phone
_REL = {"em": 1, "%": 0.01, "ex": 0.5, "ch": 0.5}  # × the parent's font size
_FONT_WORDS = {"xx-small": 9, "x-small": 10, "small": 13, "medium": 16, "large": 18, "x-large": 24,
               "xx-large": 32, "xxx-large": 48}
_BOX_PROPS = {"height", "width", "max-height", "max-width", "line-height"}
_STYLE_PROPS = {"font-size", "font", "zoom", "color", "-webkit-text-fill-color", "background", "background-color"}
MAX_DEPTH, MAX_ELEMENTS = 512, 20_000  # past either, the rest of the email is dropped (fail closed)
_INTERACTIVE = re.compile(r":(hover|focus|focus-within|focus-visible|active|visited|target|checked)\b", re.I)
_FAINT = (0, 0, 0, 0.0)  # a color we can't read is treated as invisible
MAX_CASCADE_RULES = 100  # font/color come from the last N matching rules; hiding checks every rule

# Named colors: the light ones are the white-on-white risk, the dark ones the black-on-black risk.
# ponytail: unknown names (e.g. "teal") skip the contrast check; add names here if one is abused.
_LIGHT = ("white snow honeydew mintcream azure aliceblue ghostwhite whitesmoke seashell beige oldlace floralwhite "
          "ivory antiquewhite linen lavenderblush mistyrose lightyellow lightcyan cornsilk lemonchiffon papayawhip "
          "blanchedalmond bisque gainsboro lavender lightgoldenrodyellow moccasin wheat peachpuff navajowhite").split()
_DARK = ("black navy darkblue midnightblue darkslategray darkslategrey dimgray dimgrey darkgreen maroon indigo "
         "darkred purple darkolivegreen").split()
_NAMED = {"white": (255, 255, 255, 1.0), "black": (0, 0, 0, 1.0), "transparent": (0, 0, 0, 0.0),
          **{n: (250, 250, 245, 1.0) for n in _LIGHT[1:]}, **{n: (20, 20, 30, 1.0) for n in _DARK[1:]}}


def css_unescape(value: str) -> str:
    """Undo CSS comments and escapes so "displ\\61y:/**/none" reads as "display:none"."""
    value = re.sub(r"(?s)/\*.*?\*/", "", value)
    value = re.sub(r"\\([0-9a-fA-F]{1,6})\s?", lambda m: chr(min(int(m.group(1), 16), 0x10FFFF)), value)
    return re.sub(r"\\(.)", r"\1", value)


def _decls(style: str) -> list[tuple[str, str, bool]]:
    """'a:b !important; c:d' → [(prop, value, important), …]"""
    out = []
    for decl in css_unescape(style).split(";"):
        prop, _, value = decl.partition(":")
        value = value.strip().lower()
        important = bool(re.search(r"!\s*important\s*$", value))
        out.append((prop.strip().lower(), re.sub(r"!\s*important\s*$", "", value).strip(), important))
    return [d for d in out if d[0]]


def _length(value: str, em: float) -> float | None:
    """A CSS length in px, or None for keywords like auto / normal."""
    m = _NUMBER.match(value)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2).lower()
    if unit in _PX:
        return n * _PX[unit]
    if unit == "rem":
        return n * BASE_FONT_PX
    return n * _REL[unit] * em if unit in _REL else None


def _color(value: str) -> tuple[float, float, float, float] | None:
    v = value.strip().lower()
    if v in _NAMED:
        return _NAMED[v]
    if m := re.fullmatch(r"#([0-9a-f]{3,8})", v):
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            return None
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16) / 255 if len(h) == 8 else 1.0)
    if m := re.fullmatch(r"rgba?\(\s*([\d.]+%?)[\s,]+([\d.]+%?)[\s,]+([\d.]+%?)(?:[\s,/]+([\d.]+%?))?\s*\)", v):
        ch = [min(255.0, float(x[:-1]) * 2.55 if x.endswith("%") else float(x)) for x in m.groups()[:3]]
        a = m.group(4)
        alpha = 1.0 if a is None else float(a[:-1]) / 100 if a.endswith("%") else float(a)
        return (*ch, min(1.0, alpha))
    if m := re.fullmatch(r"hsla?\(\s*([-\d.]+)(?:deg)?[\s,]+([\d.]+)%[\s,]+([\d.]+)%(?:[\s,/]+([\d.]+%?))?\s*\)", v):
        clamp = lambda x: max(0.0, min(1.0, x))  # browsers clamp out-of-range values; so must we
        h, sat, light = float(m.group(1)) % 360 / 360, clamp(float(m.group(2)) / 100), clamp(float(m.group(3)) / 100)
        r, g, b = colorsys.hls_to_rgb(h, light, sat)
        a = m.group(4)
        return (r * 255, g * 255, b * 255, clamp(1.0 if a is None else float(a[:-1]) / 100 if a.endswith("%") else float(a)))
    if "(" in v:
        return _FAINT  # lab(), oklch(), color(), …: can't check contrast, fail closed
    return None  # unknown names, currentcolor, inherit: caller keeps the inherited color


def _first_color(value: str):
    """The color inside a 'background:' shorthand."""
    for tok in re.findall(r"#[0-9a-f]{3,8}\b|rgba?\([^)]*\)|[a-z]+", value.lower()):
        if (c := _color(tok)) is not None:
            return c
    return None


def _contrast(fg, bg) -> float:
    def lum(c):
        r, g, b = (x / 255 for x in c[:3])
        lin = lambda x: x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _hides(prop: str, value: str) -> bool:
    """Does this one declaration hide the element, whatever else applies? (fail closed)"""
    if prop == "display":
        return value.startswith("none")
    if prop == "visibility":
        return value in {"hidden", "collapse"}
    if prop == "mso-hide":
        return value == "all"
    if prop in {"clip", "clip-path"}:
        return value not in {"", "auto", "none"}
    if prop in {"opacity", "text-indent", "left", "top", "right", "bottom", "margin-left", "margin-top",
                "transform", "translate", "scale"} | _BOX_PROPS:
        if _COMPUTED.search(value):
            return True
    if prop == "zoom":
        if _COMPUTED.search(value):
            return True
        m = _NUMBER.match(value)
        return bool(m) and float(m.group(1)) / (100 if m.group(2) == "%" else 1) <= 0.05
    if prop == "opacity":
        m = _NUMBER.match(value)
        return bool(m) and (float(m.group(1)) / (100 if m.group(2) == "%" else 1)) <= 0.05
    if prop in _BOX_PROPS:  # ≤ 1px, or ≤ 1%
        m = _NUMBER.match(value)
        if m and m.group(2) == "%":
            return float(m.group(1)) <= 1
        px = _length(value, BASE_FONT_PX)
        return px is not None and px <= 1
    if prop == "text-indent":
        return (_length(value, BASE_FONT_PX) or 0) <= -100
    if prop in {"left", "top", "right", "bottom"}:
        return abs(_length(value, BASE_FONT_PX) or 0) >= 2000
    if prop in {"margin-left", "margin-top"}:
        return (_length(value, BASE_FONT_PX) or 0) <= -500
    if prop in {"transform", "scale"}:
        scales = re.findall(r"scale[xyz3d]*\(\s*([-+]?[\d.]+(?:e[-+]?\d+)?)", value) if prop == "transform" else [value]
        moves = re.findall(r"translate[xyz3d]*\(\s*([-+]?[\d.]+(?:e[-+]?\d+)?\s*[a-z%]*)", value)
        return (any(_NUMBER.match(s) and abs(float(_NUMBER.match(s).group(1))) <= 0.05 for s in scales)
                or any(abs(_length(t, BASE_FONT_PX) or 0) >= 2000 for t in moves))
    if prop == "translate":
        return any(abs(_length(t, BASE_FONT_PX) or 0) >= 2000 for t in value.split())
    return False


@dataclass
class _Rule:
    tag: str | None
    ids: set
    classes: set
    attrs: set
    specificity: tuple
    order: int
    decls: list
    alt_scheme: bool  # inside @media prefers-color-scheme/print: may hide, but its colors don't count
    hides: bool = False  # worked out once: does any declaration in this rule hide the element?

    def __post_init__(self):
        self.hides = any(_hides(p, v) for p, v, _ in self.decls)
        self.decls = [d for d in self.decls if d[0] in _STYLE_PROPS]  # only what the cascade below reads


def _rules(css: str) -> list[_Rule]:
    """Parse a stylesheet linearly (no regex backtracking); @media blocks are flattened in, @-rules skipped."""
    rules, pending, selectors = [], "", []
    for tok in re.split(r"([{}])", css_unescape(css)):
        if tok == "{":
            selectors.append(pending)
            pending = ""
        elif tok == "}":
            sel = selectors.pop() if selectors else ""
            if not sel.strip().startswith("@"):
                alt = any(re.search(r"prefers-color-scheme|print", s, re.I) for s in selectors if s.strip().startswith("@"))
                for one in sel.split(","):
                    if rule := _rule(one, pending, len(rules), alt):
                        rules.append(rule)
            pending = ""
        else:
            pending = tok
        if len(rules) > 2000:  # ponytail: cap work per email; a real newsletter has a few hundred
            break
    return rules


def _rule(selector: str, body: str, order: int, alt_scheme: bool = False) -> _Rule | None:
    selector = selector.strip()
    last = re.split(r"\s*[>+~]\s*|\s+", selector)[-1] if selector else ""
    last = re.sub(r"::?(first-line|first-letter)\b", "", last, flags=re.I)  # styles the element's own text
    outside_not = re.sub(r":not\([^)]*\)", "", last, flags=re.I)  # ":not(:hover)" always applies
    if not last or "::" in last or _INTERACTIVE.search(outside_not):  # other pseudo-elements; hover/focus-only rules
        return None
    last = re.sub(r":[\w-]+(\([^)]*\))?", "", last)  # :first-child, :not(…), …: apply anyway (fail closed)
    if not last:
        last = "*"
    tag = re.match(r"[a-z][\w-]*", last, re.I)
    spec = (selector.count("#"), len(re.findall(r"[.\[:]", selector)), len(re.findall(r"(?:^|[\s>+~])[a-z]", selector, re.I)))
    return _Rule(tag.group(0).lower() if tag else None, set(re.findall(r"#([\w-]+)", last)),
                 set(re.findall(r"\.([\w-]+)", last)), {a.lower() for a in re.findall(r"\[\s*([\w-]+)", last)},
                 spec, order, _decls(body), alt_scheme)


@dataclass
class _State:
    tag: str
    hidden: bool  # this element and everything inside it is gone (display:none, …)
    faint: bool  # only this element's own text is unreadable (tiny or no contrast); children can override
    font: float
    color: tuple
    bg: tuple


class _Visible(HTMLParser):
    # Raw-text elements as browsers parse them ("</div>" inside a textarea closes nothing). Set explicitly
    # because Python 3.12's default list lacks textarea/title.
    CDATA_CONTENT_ELEMENTS = ("script", "style", "xmp", "iframe", "noembed", "noframes", "textarea", "title")

    def __init__(self, rules: list[_Rule] | None):
        super().__init__(convert_charrefs=False)
        self.rules, self.css, self.out, self.raw = rules, [], [], None
        # Index rules by one class, else id, else tag, so each element only checks rules that could match.
        self.index: dict[str, list[_Rule]] = {}
        for r in rules or []:
            key = ("." + min(r.classes)) if r.classes else ("#" + min(r.ids)) if r.ids else (r.tag or "*")
            self.index.setdefault(key, []).append(r)
        self.stack = [_State("#root", False, False, BASE_FONT_PX, (0, 0, 0, 1.0), (255, 255, 255, 1.0))]
        self.open: dict[str, int] = {}  # open elements per tag, so an unmatched end tag is skipped in O(1)
        self.elements, self.dropped = 0, False

    def _cascade(self, tag: str, attrs: list) -> tuple[bool, list[tuple[str, str, bool]]]:
        ids = {v for n, v in attrs if n == "id" and v}
        classes = {c for n, v in attrs if n == "class" and v for c in v.split()}
        names = {n for n, _ in attrs}
        keys = {"*", tag} | {"." + c for c in classes} | {"#" + i for i in ids}
        candidates = [r for k in keys for r in self.index.get(k, ())]
        matched = sorted((r for r in candidates if r.tag in (None, "*", tag)
                          and r.ids <= ids and r.classes <= classes and r.attrs <= names),
                         key=lambda r: (r.specificity, r.order))
        sheet_hides = any(r.hides for r in matched)
        matched = matched[-MAX_CASCADE_RULES:]
        # Dark-mode / print colors are dropped (light mode is the default view); their hiding rules still count.
        sheet = [d for r in matched for d in r.decls
                 if not (r.alt_scheme and d[0] in {"color", "background", "background-color", "-webkit-text-fill-color"})]
        inline = [d for n, v in attrs if n == "style" for d in _decls(v or "")]
        # Lowest to highest priority: sheet, inline, sheet !important, inline !important.
        return sheet_hides, ([d for d in sheet if not d[2]] + [d for d in inline if not d[2]]
                             + [d for d in sheet if d[2]] + [d for d in inline if d[2]])

    def handle_starttag(self, tag, attrs):
        if tag in INVISIBLE_TEXT:
            self.raw = tag
        if self.rules is None or self.dropped:  # first pass only collects <style> text
            return
        self.elements += 1
        if self.elements > MAX_ELEMENTS or len(self.stack) > MAX_DEPTH:
            self.dropped = True
            return
        parent = self.stack[-1]
        sheet_hides, decls = self._cascade(tag, attrs)
        a = {n: v for n, v in reversed(attrs)}  # first occurrence wins, like browsers
        inline_hides = any(_hides(p, v) for n, val in attrs if n == "style" for p, v, _ in _decls(val or ""))
        hidden = parent.hidden or (tag not in VOID and ("hidden" in a or sheet_hides or inline_hides))
        font, color, bg = parent.font, parent.color, parent.bg
        if tag == "font" and (c := _color(a.get("color") or "")):
            color = c
        if (c := _color(a.get("bgcolor") or "")) and c[3] > 0:
            bg = c
        for prop, value, _ in decls:  # in priority order: the last one wins
            if prop == "font-size":
                if _COMPUTED.search(value):
                    hidden = True
                elif value in _FONT_WORDS:
                    font = _FONT_WORDS[value]
                elif value in {"smaller", "larger"}:
                    font = parent.font * (0.83 if value == "smaller" else 1.2)
                elif (px := _length(value, parent.font)) is not None:
                    font = px
            elif prop == "font":  # shorthand: "italic bold 12px/1.4 Arial" or the "0/0 a" hiding trick
                if _COMPUTED.search(value):
                    hidden = True
                for tok in re.split(r"\s+", value):
                    size = tok.split("/")[0]
                    if size in _FONT_WORDS:
                        font = _FONT_WORDS[size]
                        break
                    m = _NUMBER.match(size)
                    unit = m.group(2).lower() if m else ""
                    is_length = unit in _PX or unit in _REL or unit == "rem"
                    if m and is_length and (unit or float(m.group(1)) == 0 or "/" in tok):  # 700 is a weight, 10deg an angle
                        font = _length(size, parent.font) if m.group(2) != "rem" else float(m.group(1)) * BASE_FONT_PX
                        font = font if font is not None else parent.font
                        break
            elif prop in {"color", "-webkit-text-fill-color"} and (c := _color(value)):
                color = c
            elif prop in {"background", "background-color"} and (c := _first_color(value)) and c[3] > 0:
                bg = c
        for prop, value, _ in decls:  # zoom scales everything inside, so it compounds into the effective size
            if prop == "zoom" and (m := _NUMBER.match(value)) and m.group(2) in ("", "%"):
                font *= float(m.group(1)) / (100 if m.group(2) == "%" else 1)
        faint = font < MIN_FONT_PX or color[3] <= 0.05 or _contrast(color, bg) < MIN_CONTRAST
        if tag not in VOID:
            self.stack.append(_State(tag, hidden, faint, font, color, bg))
            self.open[tag] = self.open.get(tag, 0) + 1
        if not hidden:
            self.out.append(self.get_starttag_text())

    # Browsers ignore the "/" in "<div hidden/>": the element stays open, so treat it as a plain start tag.
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == self.raw:
            self.raw = None
        if self.dropped or not self.open.get(tag):
            return
        for i in range(len(self.stack) - 1, 0, -1):  # close the nearest open element with this name
            if self.stack[i].tag == tag:
                closed = self.stack[i]
                for st in self.stack[i:]:
                    self.open[st.tag] -= 1
                del self.stack[i:]
                if not closed.hidden:
                    self.out.append(f"</{tag}>")
                return

    def handle_data(self, data):
        if self.rules is None:
            if self.raw == "style":
                self.css.append(data)
        elif not self.dropped and not self.raw and not self.stack[-1].hidden and not self.stack[-1].faint:
            self.out.append(data)

    handle_entityref = lambda self, name: self.handle_data(f"&{name};")
    handle_charref = lambda self, name: self.handle_data(f"&#{name};")


def visible(s: str) -> str:
    # Everything after <plaintext> is raw text to a browser (no tag ever closes again), so a later "</div>"
    # can't end a hidden ancestor. Obsolete tag: drop the rest rather than model it.
    s = re.split(r"(?i)<plaintext\b", s, maxsplit=1)[0]
    first = _Visible(None)  # pass 1: a <style> anywhere in the email applies to the whole email
    first.feed(s)
    p = _Visible(_rules("".join(first.css)))
    p.feed(s)
    # No close(): it would flush an unfinished trailing tag ("<div style=...secret") as visible text.
    return "".join(p.out)
