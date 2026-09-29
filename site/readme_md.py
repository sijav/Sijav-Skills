"""The README's guide, written from the same page as the website, so the two never drift apart.

The page's HTML is a small, known subset (headings, paragraphs, lists, tables, code, rule
ledgers and figures); each part becomes its GitHub Markdown twin. A figure's drawing is
inline SVG on the page; the README gets the same flow as a Mermaid chart, which GitHub draws.
"""

from __future__ import annotations

import html.parser
import re

VOID = {"br", "hr", "img", "meta", "link", "input", "source", "wbr"}
SKIP = {"script", "style", "nav", "title", "svg", "head", "figcaption"}


class Node:
    def __init__(self, tag: str, attrs=(), parent: Node | None = None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.kids: list = []

    @property
    def cls(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())


class _Tree(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.at = Node("root")

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.at)
        self.at.kids.append(node)
        if tag not in VOID:
            self.at = node

    def handle_startendtag(self, tag, attrs):
        self.at.kids.append(Node(tag, attrs, self.at))

    def handle_endtag(self, tag):
        node = self.at
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.at = node.parent

    def handle_data(self, data):
        self.at.kids.append(data)


def walk(node):
    for k in node.kids:
        if isinstance(k, Node):
            yield k
            yield from walk(k)


def text(node) -> str:
    return node if isinstance(node, str) else "".join(text(k) for k in node.kids)


def inline(node) -> str:
    if isinstance(node, str):
        return re.sub(r"\s+", " ", node)
    if node.tag in SKIP:
        return ""
    if node.tag == "code":
        return f"`{text(node)}`"
    if node.tag == "br":
        return "<br>"
    inner = "".join(inline(k) for k in node.kids)
    if node.tag in ("strong", "b"):
        return f"**{inner.strip()}**"
    if node.tag in ("em", "i"):
        return f"*{inner.strip()}*"
    if node.tag == "a":
        return f"[{inner.strip()}]({node.attrs.get('href', '')})"
    return inner


def heading(h: Node) -> str:
    level = int(h.tag[1])
    words = "".join(f" ({inline(k).strip()})" if isinstance(k, Node) and "tag" in k.cls else inline(k) for k in h.kids)
    return "#" * level + " " + re.sub(r"\s+", " ", words).strip()


def listing(node: Node) -> str:
    items = [k for k in node.kids if isinstance(k, Node) and k.tag == "li"]
    return "\n".join(f"{f'{n}.' if node.tag == 'ol' else '-'} {inline(li).strip()}" for n, li in enumerate(items, 1))


def table(t: Node) -> str:
    rows = [[inline(c).strip().replace("|", "\\|") for c in r.kids if isinstance(c, Node) and c.tag in ("td", "th")]
            for r in walk(t) if r.tag == "tr"]
    has_head = any(n.tag == "thead" for n in walk(t))
    if not has_head and all(len(r) == 1 and r[0].startswith("`") for r in rows):
        return "```sh\n" + "\n".join(r[0].strip("`") for r in rows) + "\n```"  # a list of commands
    width = max(len(r) for r in rows)
    if not has_head:
        rows.insert(0, [""] * width)
    lines = ["| " + " | ".join(r + [""] * (width - len(r))) + " |" for r in rows]
    lines.insert(1, "|" + "---|" * width)
    return "\n".join(lines)


def rule(d: Node) -> list[str]:
    """One rule of a ledger: its id and title as a heading, then its text."""
    parts = [k for k in d.kids if isinstance(k, Node)]
    rid = text(next(k for k in parts if "id" in k.cls)).strip()
    body = next(k for k in parts if "body" in k.cls)
    title = next(k for k in body.kids if isinstance(k, Node) and k.tag == "h4")
    rest = Node("div")
    rest.kids = [k for k in body.kids if k is not title]
    name = inline(title).strip()
    return [f"#### {rid}. {name}" if re.fullmatch(r"[A-Z]\d+", rid) else f"#### {name}", *blocks(rest)]


def figure(f: Node, section: str | None, charts: dict[str, str]) -> list[str]:
    if section not in charts:
        raise SystemExit(f"the README has no chart for the figure in the page's {section!r} section")
    caption = next((n for n in walk(f) if n.tag == "figcaption"), None)
    out = [f"```mermaid\n{charts[section].strip()}\n```"]
    if caption is not None:
        words = re.sub(r"\s+", " ", "".join(inline(k) for k in caption.kids)).strip()
        out.append(f"*{words}*")
    return out


def blocks(node: Node, section: str | None = None, charts: dict[str, str] | None = None) -> list[str]:
    out: list[str] = []
    for k in node.kids:
        if isinstance(k, str):
            if k.strip():
                out.append(re.sub(r"\s+", " ", k).strip())
            continue
        if k.tag in SKIP:
            continue
        if k.tag == "section":
            out += blocks(k, k.attrs.get("id"), charts)
        elif k.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            out.append(heading(k))
        elif k.tag == "p":
            out.append(inline(k).strip())
        elif k.tag in ("ul", "ol"):
            out.append(listing(k))
        elif k.tag == "table":
            out.append(table(k))
        elif k.tag == "pre":
            body = text(k).strip("\n")
            out.append(f"```{'json' if body.lstrip().startswith('{') else ''}\n{body}\n```")
        elif k.tag == "figure":
            out += figure(k, section, charts or {})
        elif k.tag == "div" and "rule" in k.cls:
            out += rule(k)
        else:
            out += blocks(k, section, charts)
    return out


def page_parts(page: str) -> tuple[str, list[Node]]:
    """The page's lede and its sections, in order."""
    tree = _Tree()
    tree.feed(page)
    tree.close()
    lede = next((n for n in walk(tree.root) if n.tag == "p" and "lede" in n.cls), None)
    sections = [n for n in walk(tree.root) if n.tag == "section"]
    return (inline(lede).strip() if lede is not None else ""), sections


def guide(page: str, charts: dict[str, str]) -> str:
    """Every section of the page, as Markdown."""
    _, sections = page_parts(page)
    wrapper = Node("root")
    wrapper.kids = sections
    return "\n\n".join(b for b in blocks(wrapper, None, charts) if b)
