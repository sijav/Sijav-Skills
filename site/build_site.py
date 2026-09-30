"""Build the Skills on Call site: docs/index.html, served by GitHub Pages.

The owner, 2026-09-28: skills are project independent, and the page is "a guide to the bundle"
with no owner-needed or fixed flags, no paths and no project-specific names. The rule text comes
from the bundle's own files, so the page matches them; the build refuses to write a page that
names a project, a person or a path, or carries a status flag.

  python site/build_site.py                      # writes docs/index.html and README.md
  python site/build_site.py --fragment page.html # also a copy for the Claude page viewer
"""

import html
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SK = REPO / "Sijav-Clauder" / "skills"
OUT = REPO / "docs" / "index.html"
SITE = "https://sijav.github.io/Sijav-Skills/"
SOURCE = "https://github.com/sijav/Sijav-Skills"
DESCRIPTION = (
    "Eight Claude Code skills that work in any project: rule sets that load only when "
    "called, a dev round, codex sessions, web search and deep research with checked citations, "
    "roasts judged by jev, a loop and a to-do board."
)

FORBIDDEN = {
    "a path": r"(?<![A-Za-z])[A-Za-z]:[\\/]|[\\/]Users[\\/]|~[\\/]",  # a drive letter, not https:/
    "a status flag": r"\bFixed\b|waiting on you|owner needed|Changed today|\bKeep, watch\b",
    "an item number": r"\bitem \d{3,4}\b|\b(?:KN|SB)-\d+\b",
}
# Names that must never reach the public page (people, private projects) stay
# out of this public file too: one "label: regex" per line in
# site/private-words.txt, which git ignores. Lines starting with # are notes.
PRIVATE = REPO / "site" / "private-words.txt"
PRIVATE_RULES: dict[str, str] = {}
if PRIVATE.is_file():
    for _line in PRIVATE.read_text(encoding="utf-8").splitlines():
        _label, _sep, _rx = _line.partition(":")
        if _sep and _rx.strip() and not _line.lstrip().startswith("#"):
            PRIVATE_RULES[_label.strip()] = _rx.strip()
FORBIDDEN.update(PRIVATE_RULES)


def private_in_repo() -> list[str]:
    """Every tracked file, checked against the private words: the page is not the
    only thing the public repository publishes."""
    import subprocess

    tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True,
                             check=True).stdout.split("\n")
    found = []
    for name in filter(None, tracked):
        try:
            text = (REPO / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for no, line in enumerate(text.splitlines(), 1):
            for what, rx in PRIVATE_RULES.items():
                if re.search(rx, line):
                    found.append(f"{what} in {name}:{no}")
    return found


def inline(text: str) -> str:
    t = html.escape(text, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)


def blocks(lines: list[str]) -> str:
    """Paragraphs, bullet lists and numbered lists, with indented continuation lines."""
    out, para, items, kind = [], [], [], None

    def flush():
        nonlocal para, items, kind
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
        if items:
            out.append(f"<{kind}>" + "".join(f"<li>{inline(i)}</li>" for i in items) + f"</{kind}>")
        para, items, kind = [], [], None

    for line in lines:
        if not line.strip():
            flush()
            continue
        m = re.match(r"^([-*]|\d+\.)\s+(.*)$", line)
        if m:
            new_kind = "ol" if m.group(1)[0].isdigit() else "ul"
            if para or (kind and kind != new_kind):
                flush()
            kind = new_kind
            items.append(m.group(2))
        elif items and line.startswith(" "):
            items[-1] += " " + line.strip()
        else:
            if items:
                flush()
            para.append(line.strip())
    flush()
    return "".join(out)


def sections(path: Path) -> list[tuple[str, str]]:
    """(heading, body lines) for every `## ` section of a markdown file."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"\A---.*?\n---\s*\n", "", text, flags=re.S)
    parts = re.split(r"(?m)^## ", text)
    return [(p.partition("\n")[0].strip(), p.partition("\n")[2].splitlines()) for p in parts[1:]]


def ledger(path: Path) -> str:
    rows = []
    for head, body in sections(path):
        m = re.match(r"([A-Z]\d+)\.\s+(.*)", head)
        rid, title = (m.group(1), m.group(2)) if m else ("", head)
        rows.append(f'<div class="rule"><div class="id">{rid}</div><div class="body">'
                    f"<h4>{inline(title)}</h4>{blocks(body)}</div></div>")
    return '<div class="ledger">' + "".join(rows) + "</div>"


def dev_round() -> str:
    path = SK / "dev-round" / "SKILL.md"
    text = re.sub(r"\A---.*?\n---\s*\n", "", path.read_text(encoding="utf-8"), flags=re.S)
    intro = [line for line in text.split("\n## ")[0].splitlines() if not line.startswith("# ")]
    labels = {"Each to-do": "each", "When the round is done": "end"}
    rows = "".join(f'<div class="rule"><div class="id">{labels.get(head, "")}</div><div class="body">'
                   f"<h4>{inline(head)}</h4>{blocks(body)}</div></div>" for head, body in sections(path))
    return blocks(intro) + f'<div class="ledger">{rows}</div>'


def changing_a_rule() -> str:
    for head, body in sections(SK / "rules" / "SKILL.md"):
        if head == "Changing a rule":
            return blocks(body)
    raise SystemExit("rules/SKILL.md has no 'Changing a rule' section")


def design_note() -> str:
    body = [h for h, _ in sections(SK / "rules" / "design.md")]
    return ledger(SK / "rules" / "design.md") if body else '<p class="note">No design rules yet.</p>'


SKILLS = [
    ("rules", "Your rule sets: general, dev and design, plus the current project's own set, and how to change a rule.",
     "Only when you call it", "/sijav-clauder:rules · /sijav-clauder:rules dev · /sijav-clauder:rules design"),
    ("dev-round", "How to work through a round of code to-dos: each to-do writes its tests without running them; when "
                  "the area's to-dos are done, its full suite runs at 100% coverage with the end-to-end scenarios, the "
                  "tests are checked, and failures are fixed at the root.", "When you call it or load the dev rules",
     "/sijav-clauder:dev-round"),
    ("codex", "Calls codex (GPT-6) through one session per kind of work, 6.1 sol at medium effort unless told otherwise.",
     "When a task needs codex", "/sijav-clauder:codex"),
    ("search", "A plain web search: codex on luna at low effort answers one question in a few sentences, with the "
               "links it used.", "When a fact, a version or a doc page is needed", "/sijav-clauder:search"),
    ("research", "Deep research on astra, like ChatGPT's or Gemini's: questions and a plan you approve, web searches side "
                 "by side, gap rounds, one cited report, and every quote checked on the page it cites, then judged by jev.",
     "When a question needs many sources", "/sijav-clauder:research"),
    ("roast", "Codex checks a plan or finished work and writes typed questions; jev judges them; codex writes its "
              "reading. One record per run.", "When a check is wanted", "/sijav-clauder:roast"),
    ("loop", "Engages a project's own loop and follows its law: start, turns, pause, finish.",
     "When you call it", "/sijav-clauder:loop"),
    ("todo", "A project board in a small database, for projects that have opted in.", "In those projects", "/sijav-clauder:todo"),
]


def skills_table() -> str:
    missing = [s for s, *_ in SKILLS if not (SK / s / "SKILL.md").is_file()]
    extra = sorted(p.parent.name for p in SK.glob("*/SKILL.md") if p.parent.name not in {s for s, *_ in SKILLS})
    if missing or extra:
        raise SystemExit(f"the page's skill list is out of date: missing folders {missing}, unlisted skills {extra}")
    rows = "".join(f'<tr><td class="skill">{s}</td><td>{inline(w)}</td><td>{inline(when)}</td>'
                   f'<td class="cmd">{"<br>".join(f"<code>{html.escape(c.strip())}</code>" for c in call.split("·"))}</td></tr>'
                   for s, w, when, call in SKILLS)
    return ('<div class="tablewrap"><table><thead><tr><th>Skill</th><th>What it does</th><th>When it loads</th>'
            f'<th>How you call it</th></tr></thead><tbody>{rows}</tbody></table></div>')


TEMPLATE = r"""<title>Skills on Call</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Schibsted+Grotesk:wght@500;600;700&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap">
<style>
:root {
  --paper: #F5F7F4; --ink: #17201C; --muted: #56625C; --line: #D3DAD5; --panel: #EAEEEA; --panel-2: #E0E6E1;
  --accent: #1F5A8A; --codex: #2D6E4E; --judge: #96560F; --tool: #5A6068; --stop: #A23A2A;
  --display: "Schibsted Grotesk", "Segoe UI", system-ui, sans-serif;
  --body: "Source Serif 4", Georgia, "Times New Roman", serif;
  --mono: "JetBrains Mono", ui-monospace, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --paper: #111513; --ink: #E1E8E3; --muted: #9AA69F; --line: #2B3430; --panel: #19201C; --panel-2: #212A25;
    --accent: #8BB7E0; --codex: #86C6A1; --judge: #E3AA62; --tool: #B3B8BF; --stop: #E68A7A;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --paper: #111513; --ink: #E1E8E3; --muted: #9AA69F; --line: #2B3430; --panel: #19201C; --panel-2: #212A25;
  --accent: #8BB7E0; --codex: #86C6A1; --judge: #E3AA62; --tool: #B3B8BF; --stop: #E68A7A;
}
body { margin: 0; background: var(--paper); color: var(--ink); font-family: var(--body); font-size: 17px; line-height: 1.6;
  padding-inline: 20px; padding-block: 0 72px; }
.wrap { max-width: 1000px; margin: 0 auto; }
header { padding-block: 52px 22px; }
.eyebrow { font-family: var(--mono); font-size: .74rem; letter-spacing: .09em; text-transform: uppercase; color: var(--muted); margin: 0 0 10px; }
h1 { font-family: var(--display); font-weight: 700; font-size: clamp(2.1rem, 5.5vw, 3.2rem); line-height: 1.05; letter-spacing: -0.015em; margin: 0; text-wrap: balance; }
.lede { max-width: 60ch; color: var(--muted); font-size: 1.12rem; margin: 14px 0 0; }
nav.toc {
  position: sticky; top: env(safe-area-inset-top, 0px); z-index: 20;
  display: flex; flex-wrap: wrap; gap: 4px 20px;
  margin-inline: -20px; padding: 11px 20px;
  background: var(--paper);
  background: color-mix(in srgb, var(--paper) 86%, transparent);
  -webkit-backdrop-filter: saturate(1.4) blur(12px);
  backdrop-filter: saturate(1.4) blur(12px);
  border-block: 1px solid var(--line);
  font-family: var(--display); font-size: .93rem;
}
nav.toc a { color: var(--muted); text-decoration: none; white-space: nowrap; padding-block: 3px; border-bottom: 2px solid transparent; }
nav.toc a:hover { color: var(--ink); }
nav.toc a.on { color: var(--accent); border-bottom-color: var(--accent); }
@media (max-width: 700px) {
  nav.toc { flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none; }
  nav.toc::-webkit-scrollbar { display: none; }
}
section { padding-block: 44px 6px; scroll-margin-top: 64px; }
pre.code { font-family: var(--mono); font-size: .86rem; line-height: 1.5; background: var(--panel); padding: 12px 14px; border-radius: 8px; overflow-x: auto; margin: 12px 0 0; max-width: 68ch; }
h2 { font-family: var(--display); font-weight: 650; font-size: 1.62rem; line-height: 1.2; margin: 0 0 10px; text-wrap: balance; }
h3 { font-family: var(--display); font-weight: 600; font-size: 1.14rem; margin: 30px 0 8px; display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: baseline; }
h3 .tag { font-family: var(--mono); font-weight: 400; font-size: .8rem; color: var(--muted); }
h4 { font-family: var(--display); font-weight: 600; font-size: 1.02rem; margin: 0; }
p, li { max-width: 68ch; }
p { margin: 10px 0 0; }
ul, ol { margin: 8px 0 0; padding-left: 1.3em; }
li + li { margin-top: 4px; }
code { font-family: var(--mono); font-size: .84em; background: var(--panel); padding: .08em .36em; border-radius: 4px; }
.note { color: var(--muted); }
.tablewrap { overflow-x: auto; margin-top: 16px; }
table { border-collapse: collapse; width: 100%; font-size: .95rem; line-height: 1.45; }
th, td { text-align: left; vertical-align: top; padding: 11px 12px; border-bottom: 1px solid var(--line); }
th { font-family: var(--display); font-weight: 600; font-size: .84rem; color: var(--muted); border-bottom-color: var(--ink); }
td.skill { font-family: var(--mono); font-weight: 600; white-space: nowrap; color: var(--accent); }
td.cmd { white-space: nowrap; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.ledger { border-top: 1px solid var(--line); margin-top: 12px; }
.rule { display: grid; grid-template-columns: 4.5rem minmax(0, 1fr); gap: 2px 18px; padding-block: 16px; border-bottom: 1px solid var(--line); }
.rule .id { font-family: var(--mono); font-weight: 600; color: var(--accent); font-size: .95rem; padding-top: 1px; }
figure { margin: 22px 0 0; }
.figwrap { overflow-x: auto; background: var(--panel); border-radius: 10px; padding: 14px; }
.figwrap svg { display: block; width: 100%; min-width: 700px; height: auto; }
figcaption { color: var(--muted); font-size: .93rem; margin-top: 10px; max-width: 74ch; }
.bx { fill: var(--paper); stroke: var(--line); stroke-width: 1.3; }
.bx-accent { fill: var(--paper); stroke: var(--accent); stroke-width: 1.5; }
.bx-hot { fill: var(--paper); stroke: var(--stop); stroke-width: 1.5; }
.bx-codex { fill: var(--paper); stroke: var(--codex); stroke-width: 1.5; }
.bx-judge { fill: var(--paper); stroke: var(--judge); stroke-width: 1.5; }
.bx-tool { fill: var(--paper); stroke: var(--tool); stroke-width: 1.5; }
.panel-ok { fill: var(--panel-2); stroke: var(--line); stroke-width: 1; }
.lane { fill: var(--paper); fill-opacity: .55; stroke: var(--line); stroke-width: 1; }
.ln { stroke: var(--muted); stroke-width: 1.3; fill: none; }
.ln-hot { stroke: var(--stop); stroke-width: 1.4; fill: none; stroke-dasharray: 5 4; }
.ah { fill: var(--muted); }
.ah-hot { fill: var(--stop); }
.t { fill: var(--ink); font-family: var(--display); font-size: 14px; }
.tb { fill: var(--ink); font-family: var(--display); font-size: 15px; font-weight: 600; }
.t2 { fill: var(--ink); font-family: var(--display); font-size: 13px; }
.ts { fill: var(--muted); font-family: var(--body); font-size: 12.5px; }
.tm { fill: var(--ink); font-family: var(--mono); font-size: 13px; }
.tm-s { font-size: 12px; }
.th { fill: var(--stop); }
.lane-h { font-family: var(--mono); font-size: 11.5px; letter-spacing: .08em; }
.c-claude { fill: var(--accent); } .c-codex { fill: var(--codex); } .c-tool { fill: var(--tool); } .c-judge { fill: var(--judge); }
.cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 8px 36px; }
a { color: var(--accent); }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 3px; }
@media (max-width: 560px) {
  body { font-size: 16px; }
  .rule { grid-template-columns: minmax(0, 1fr); }
}
</style>

<div class="wrap">
<header>
  <p class="eyebrow">Claude Code plugin · sijav-clauder · any project</p>
  <h1>Skills on Call</h1>
  <p class="lede">Eight skills that work in any project. A new session starts with no rules at all: the rule sets load only when you call them, and each skill loads when its work comes up.</p>
</header>
<nav class="toc" aria-label="Sections">
  <a href="#skills">Skills</a><a href="#loading">Rule loading</a><a href="#rules">Rule sets</a>
  <a href="#loop">Loop</a><a href="#roast">Roast</a><a href="#codex">Codex</a><a href="#research">Research</a><a href="#todo">Board</a>
  <a href="#project">Your project</a><a href="#setup">Setup</a><a href="#install">Install</a><a href="#change">Changing a rule</a>
</nav>

<section id="skills">
  <h2>The eight skills</h2>
  <p>They ship as one Claude Code plugin, <code>sijav-clauder</code>, from a folder that is its own git repository. Plugin skills are called with the plugin's name first. None of them holds a project's rules, names or paths; each project keeps those itself.</p>
  {{SKILLS_TABLE}}
</section>

<section id="loading">
  <h2>How rules load</h2>
  <p>Nothing loads by itself: there is no global rules file and no rule in memory. You call a set when you want it.</p>
  <figure>
    <div class="figwrap">
      <svg viewBox="0 0 960 272" role="img" aria-label="A session starts with no rules. You type /sijav-clauder:rules, with dev or design if you want those sets. Each reads the general rules and the set you named, then the project's own rule set if the project has one; a project rule wins a clash.">
        <defs>
          <marker id="f1-ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah"/></marker>
        </defs>
        <text x="96" y="106" text-anchor="middle" class="ts">you type one</text>
        <rect x="16" y="116" width="160" height="56" rx="8" class="bx-accent"/>
        <text x="96" y="140" text-anchor="middle" class="t">A session starts</text>
        <text x="96" y="159" text-anchor="middle" class="ts">no rules loaded</text>
        <path d="M176,144 H196 V58 H212" class="ln" marker-end="url(#f1-ah)"/>
        <path d="M176,144 H212" class="ln" marker-end="url(#f1-ah)"/>
        <path d="M176,144 H196 V230 H212" class="ln" marker-end="url(#f1-ah)"/>
        <rect x="216" y="36" width="216" height="44" rx="22" class="bx"/>
        <text x="324" y="63" text-anchor="middle" class="tm tm-s">/sijav-clauder:rules</text>
        <rect x="216" y="122" width="216" height="44" rx="22" class="bx"/>
        <text x="324" y="149" text-anchor="middle" class="tm tm-s">/sijav-clauder:rules dev</text>
        <rect x="216" y="208" width="216" height="44" rx="22" class="bx"/>
        <text x="324" y="235" text-anchor="middle" class="tm tm-s">/sijav-clauder:rules design</text>
        <text x="447" y="44" text-anchor="middle" class="ts">reads</text>
        <path d="M432,58 H458" class="ln" marker-end="url(#f1-ah)"/>
        <path d="M432,144 H458" class="ln" marker-end="url(#f1-ah)"/>
        <path d="M432,230 H458" class="ln" marker-end="url(#f1-ah)"/>
        <rect x="462" y="36" width="262" height="44" rx="6" class="bx"/>
        <text x="593" y="63" text-anchor="middle" class="t">general: G1 to G6</text>
        <rect x="462" y="122" width="262" height="44" rx="6" class="bx"/>
        <text x="593" y="149" text-anchor="middle" class="t">general + dev: D1 to D5, dev-round</text>
        <rect x="462" y="208" width="262" height="44" rx="6" class="bx"/>
        <text x="593" y="235" text-anchor="middle" class="t">general + design (none yet)</text>
        <path d="M724,58 H754 V132 H780" class="ln" marker-end="url(#f1-ah)"/>
        <path d="M724,144 H780" class="ln" marker-end="url(#f1-ah)"/>
        <path d="M724,230 H754 V156 H780" class="ln" marker-end="url(#f1-ah)"/>
        <text x="770" y="98" text-anchor="middle" class="ts">then</text>
        <rect x="784" y="106" width="160" height="76" rx="8" class="bx-accent"/>
        <text x="864" y="132" text-anchor="middle" class="t">the project's own</text>
        <text x="864" y="151" text-anchor="middle" class="t">rule set, if any</text>
        <text x="864" y="170" text-anchor="middle" class="ts">wins a clash</text>
      </svg>
    </div>
    <figcaption>You call one command. It reads the general rules and the set you name, then the project's own rule set when the project has one. Inside that project, a project rule wins over a general rule it clashes with.</figcaption>
  </figure>
</section>

<section id="rules">
  <h2>The rule sets</h2>
  <p>Each rule has an ID, so you can say which one to keep, change or drop. The text below is read from the rule files themselves.</p>
  <h3>General <span class="tag">G · /sijav-clauder:rules</span></h3>
  {{GENERAL}}
  <h3>Development <span class="tag">D · /sijav-clauder:rules dev</span></h3>
  {{DEV}}
  <h3>dev-round <span class="tag">skill · /sijav-clauder:dev-round, or with the dev rules</span></h3>
  {{DEVROUND}}
  <h3>Design <span class="tag">S · /sijav-clauder:rules design</span></h3>
  {{DESIGN}}
</section>

<section id="loop">
  <h2>The loop</h2>
  <p>A project can run a loop: after each reply, its Stop hook gives the project's law back as the next prompt, until the work is done. The loop skill engages that loop and follows the law. Starting it is the project's own command, run from the session that should do the work: that session becomes the loop's only session, and the pause file goes.</p>
  <figure>
    <div class="figwrap">
      <svg viewBox="0 0 960 556" role="img" aria-label="After every reply the Stop hook asks five questions in order: is there a .stop file, does the project have a law file, is this the loop's own session, did the reply end with the finish promise, has the counter hit its cap. Any exit lets the session stop; otherwise the law comes back as the next prompt.">
        <defs>
          <marker id="f2-ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah"/></marker>
          <marker id="f2-ahh" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah-hot"/></marker>
        </defs>
        <rect x="120" y="16" width="280" height="44" rx="22" class="bx-accent"/>
        <text x="260" y="43" text-anchor="middle" class="t">Claude finishes a reply</text>
        <path d="M260,60 V82" class="ln" marker-end="url(#f2-ah)"/>
        <rect x="100" y="84" width="320" height="46" rx="6" class="bx"/>
        <text x="260" y="112" text-anchor="middle" class="t">Is there a .stop file?</text>
        <path d="M260,130 V162" class="ln" marker-end="url(#f2-ah)"/><text x="270" y="152" class="ts">no</text>
        <rect x="100" y="164" width="320" height="46" rx="6" class="bx"/>
        <text x="260" y="192" text-anchor="middle" class="t">Does the project have a law file?</text>
        <path d="M260,210 V242" class="ln" marker-end="url(#f2-ah)"/><text x="270" y="232" class="ts">yes</text>
        <rect x="100" y="244" width="320" height="46" rx="6" class="bx"/>
        <text x="260" y="272" text-anchor="middle" class="t">Is this the loop's own session?</text>
        <path d="M260,290 V322" class="ln" marker-end="url(#f2-ah)"/><text x="270" y="312" class="ts">yes</text>
        <rect x="100" y="324" width="320" height="46" rx="6" class="bx"/>
        <text x="260" y="352" text-anchor="middle" class="t">Did the reply end with the finish promise?</text>
        <path d="M260,370 V402" class="ln" marker-end="url(#f2-ah)"/><text x="270" y="392" class="ts">no</text>
        <rect x="100" y="404" width="320" height="46" rx="6" class="bx"/>
        <text x="260" y="432" text-anchor="middle" class="t">Has the reply counter hit its cap?</text>
        <path d="M260,450 V482" class="ln" marker-end="url(#f2-ah)"/><text x="270" y="472" class="ts">no</text>
        <rect x="100" y="484" width="320" height="56" rx="8" class="bx-hot"/>
        <text x="260" y="507" text-anchor="middle" class="t th">The stop is blocked</text>
        <text x="260" y="526" text-anchor="middle" class="ts">the law comes back as the next prompt</text>
        <path d="M100,512 H56 V38 H116" class="ln-hot" marker-end="url(#f2-ahh)"/>
        <text x="44" y="275" text-anchor="middle" transform="rotate(-90 44 275)" class="ts th">Claude works on it; this runs again</text>
        <rect x="560" y="36" width="384" height="430" rx="10" class="panel-ok"/>
        <text x="752" y="64" text-anchor="middle" class="tb">The session may stop</text>
        <path d="M420,107 H556" class="ln" marker-end="url(#f2-ah)"/><text x="488" y="100" text-anchor="middle" class="ts">yes</text>
        <text x="580" y="104" class="t">Paused, for every session</text><text x="580" y="122" class="ts">only when you ask for a pause</text>
        <path d="M420,187 H556" class="ln" marker-end="url(#f2-ah)"/><text x="488" y="180" text-anchor="middle" class="ts">no</text>
        <text x="580" y="184" class="t">No loop in this project</text><text x="580" y="202" class="ts">the hook does nothing</text>
        <path d="M420,267 H556" class="ln" marker-end="url(#f2-ah)"/><text x="488" y="260" text-anchor="middle" class="ts">no</text>
        <text x="580" y="264" class="t">Any other session stops normally</text><text x="580" y="282" class="ts">it never gets the law</text>
        <path d="M420,347 H556" class="ln" marker-end="url(#f2-ah)"/><text x="488" y="340" text-anchor="middle" class="ts">yes</text>
        <text x="580" y="344" class="t">The loop is finished</text><text x="580" y="362" class="ts">only when all of its work is done</text>
        <path d="M420,427 H556" class="ln" marker-end="url(#f2-ah)"/><text x="488" y="420" text-anchor="middle" class="ts">yes</text>
        <text x="580" y="424" class="t">The cap is reached</text><text x="580" y="442" class="ts">set in the law; rarely reached</text>
      </svg>
    </div>
    <figcaption>What a project's Stop hook checks after every reply, in this order. Every "may stop" exit ends the loop for that reply; only the session that started the loop ever gets the law back.</figcaption>
  </figure>
  <div class="cols">
    <div><h4>Start</h4><p>Type <code>/sijav-clauder:loop</code> in the session that should do the work. The project's start command makes it the loop's only session and removes the pause file.</p></div>
    <div><h4>Pause</h4><p>A <code>.stop</code> file in the project pauses the loop. Claude makes one only when you ask; a used-up codex allowance means asking you to switch accounts, not pausing.</p></div>
    <div><h4>Finish</h4><p>The reply ends with the law's finish promise only when all work is closed: no open, parked or failing items left.</p></div>
  </div>
</section>

<section id="roast">
  <h2>The roast</h2>
  <p>A second engineer pushes back on the work. Codex does the pushing, jev (TypeSafe's judge model) weighs the logic, and Claude decides. It runs once per plan before building; small, clear fixes skip it.</p>
  <figure>
    <div class="figwrap">
      <svg viewBox="0 0 960 488" role="img" aria-label="A roast in seven steps across four lanes: Claude runs it; codex reads the plan and the code and frames questions; the roast keeps code away from jev and adds your critical question; jev judges every question in one batch; codex writes its reading; the roast keeps a record; Claude decides each finding.">
        <defs>
          <marker id="f3-ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah"/></marker>
        </defs>
        <rect x="8" y="8" width="224" height="472" rx="10" class="lane"/>
        <rect x="248" y="8" width="224" height="472" rx="10" class="lane"/>
        <rect x="488" y="8" width="224" height="472" rx="10" class="lane"/>
        <rect x="728" y="8" width="224" height="472" rx="10" class="lane"/>
        <text x="120" y="34" text-anchor="middle" class="lane-h c-claude">CLAUDE</text>
        <text x="360" y="34" text-anchor="middle" class="lane-h c-codex">CODEX</text>
        <text x="600" y="34" text-anchor="middle" class="lane-h c-tool">THE ROAST TOOL</text>
        <text x="840" y="34" text-anchor="middle" class="lane-h c-judge">JEV</text>
        <rect x="16" y="52" width="208" height="46" rx="6" class="bx-accent"/>
        <text x="120" y="71" text-anchor="middle" class="t2">Runs the roast</text><text x="120" y="88" text-anchor="middle" class="ts">on the plan, before building</text>
        <path d="M120,98 V106 H360 V111" class="ln" marker-end="url(#f3-ah)"/>
        <rect x="256" y="112" width="208" height="46" rx="6" class="bx-codex"/>
        <text x="360" y="131" text-anchor="middle" class="t2">Reads the plan and the code</text><text x="360" y="148" text-anchor="middle" class="ts">frames 2 to 5 questions</text>
        <path d="M360,158 V166 H600 V171" class="ln" marker-end="url(#f3-ah)"/>
        <rect x="496" y="172" width="208" height="46" rx="6" class="bx-tool"/>
        <text x="600" y="191" text-anchor="middle" class="t2">Keeps code away from jev</text><text x="600" y="208" text-anchor="middle" class="ts">adds your critical question</text>
        <text x="600" y="238" text-anchor="middle" class="ts">code found: back to codex once</text>
        <path d="M600,218 V226 H840 V231" class="ln" marker-end="url(#f3-ah)"/>
        <rect x="736" y="232" width="208" height="46" rx="6" class="bx-judge"/>
        <text x="840" y="251" text-anchor="middle" class="t2">Judges every question</text><text x="840" y="268" text-anchor="middle" class="ts">in one batch</text>
        <text x="840" y="298" text-anchor="middle" class="ts">too long: codex shortens it once</text>
        <text x="840" y="314" text-anchor="middle" class="ts">limit: 32k tokens for the state</text>
        <text x="840" y="330" text-anchor="middle" class="ts">plus the longest question</text>
        <path d="M840,278 V286 H360 V291" class="ln" marker-end="url(#f3-ah)"/>
        <rect x="256" y="292" width="208" height="46" rx="6" class="bx-codex"/>
        <text x="360" y="311" text-anchor="middle" class="t2">Writes its reading</text><text x="360" y="328" text-anchor="middle" class="ts">failing scenarios, findings</text>
        <path d="M360,338 V346 H600 V351" class="ln" marker-end="url(#f3-ah)"/>
        <rect x="496" y="352" width="208" height="46" rx="6" class="bx-tool"/>
        <text x="600" y="371" text-anchor="middle" class="t2">Keeps a record of the run</text><text x="600" y="388" text-anchor="middle" class="ts">never overwritten</text>
        <path d="M600,398 V406 H120 V411" class="ln" marker-end="url(#f3-ah)"/>
        <rect x="16" y="412" width="208" height="46" rx="6" class="bx-accent"/>
        <text x="120" y="431" text-anchor="middle" class="t2">Decides each finding</text><text x="120" y="448" text-anchor="middle" class="ts">the number is only an alarm</text>
      </svg>
    </div>
    <figcaption>One plan roast. Jev only ever sees logic in plain words: the plan's steps and reasons, the facts codex checked, and what you asked for in your own words. Code stays with codex and Claude.</figcaption>
  </figure>

  <h3>Reading a roast</h3>
  <ul>
    <li>The record has five parts: what was asked, the facts codex checked with their sources, jev's answers as numbers, codex's reading (its own interpretation, not jev's), and a place to write what happened to each finding.</li>
    <li>A yes-or-no answer from jev is a probability and has no confidence. The confidence on a choice or a score only says how concentrated its probabilities are.</li>
    <li>A finding is critical only if it would definitely break the whole thing asked for, must be fixed right away, and is neither a later task nor a feature. The failing scenario decides that, not jev's number.</li>
    <li>No automatic second roast. A changed plan is checked against the facts before building.</li>
    <li>Without jev (switched off, no key, or jev failing during the run), codex reviews alone and the record says why. With codex switched off there is no roast: Claude reviews the work itself.</li>
  </ul>

  <h3>What a replay on 12 past plans showed</h3>
  <div class="tablewrap">
    <table>
      <thead><tr><th></th><th class="num">Old judge-only check</th><th class="num">Codex alone</th><th class="num">Codex with jev</th></tr></thead>
      <tbody>
        <tr><td>Real problems named, of 18</td><td class="num">0</td><td class="num">4</td><td class="num">5</td></tr>
        <tr><td>False objections</td><td class="num">3</td><td class="num">14</td><td class="num">11 to 12</td></tr>
        <tr><td>Critical call right, of 12</td><td class="num">7</td><td class="num">7</td><td class="num">6</td></tr>
        <tr><td>Time per plan</td><td class="num">0.4 s</td><td class="num">40 s</td><td class="num">82 s</td></tr>
      </tbody>
    </table>
  </div>
  <p class="note">Each check saw only the plan as it was before its first check. Two blind scorers agreed on 53 of 54 verdicts. Jev's critical number was 0.5 or more on all three sound plans, which is why it only ever raises an alarm. Twelve plans is a small sample, and each check ran once.</p>
</section>

<section id="codex">
  <h2>Codex</h2>
  <ul>
    <li>One codex session per kind of work, such as research or a roast mode. A call with the same purpose resumes that session, so codex keeps the context of that line of work.</li>
    <li>Models: <code>gpt-6.1-sol</code> at medium effort by default (it needs codex 0.159 or newer), <code>gpt-6-astra</code> for research (the roast's search mode and the research skill, at high effort) or when you ask for it, <code>gpt-6-luna</code> for fast, cheap tasks, such as a plain web search at low effort. A session keeps its thread when the model changes.</li>
    <li>All GPT-6 models share one allowance. When it runs out, Claude asks you to switch the codex account, then continues in the same session. No pause, and no reserve model, except for a plain web search: it runs once more on <code>gpt-reserve</code>, a luna that stays free when the allowance is used up.</li>
    <li>Every call keeps its full log in the project it ran for.</li>
    <li>This skill holds no codex token: codex signs in with its own login, which you manage.</li>
  </ul>
</section>

<section id="research">
  <h2>Search and research</h2>
  <p><strong>Search</strong> and <strong>research</strong> are separate skills. Search is a plain web lookup: one question to codex on <code>gpt-6-luna</code> at low effort, answered in a few sentences with the links it used; when the allowance is used up, it runs on <code>gpt-reserve</code>, which stays free. Research is deep research on <code>gpt-6-astra</code>, like ChatGPT's and Gemini's, for a question that needs many sources and judgement; it checks every citation on the page it cites before you read the report.</p>
  <figure>
    <div class="figwrap">
      <svg viewBox="0 0 960 656" role="img" aria-label="A research run in ten steps across four lanes: Claude asks the question; codex plans the sub-questions and asks what is unclear; you answer and approve the plan; codex searches the web for each sub-question side by side; codex finds the gaps; codex searches the follow-ups; codex writes the report once; the check finds each quote on the page it cites; jev judges whether each quote supports its claim; Claude reads the check.">
        <defs>
          <marker id="f4-ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ah"/></marker>
        </defs>
        <rect x="8" y="8" width="224" height="640" rx="10" class="lane"/>
        <rect x="248" y="8" width="224" height="640" rx="10" class="lane"/>
        <rect x="488" y="8" width="224" height="640" rx="10" class="lane"/>
        <rect x="728" y="8" width="224" height="640" rx="10" class="lane"/>
        <text x="120" y="34" text-anchor="middle" class="lane-h c-claude">CLAUDE AND YOU</text>
        <text x="360" y="34" text-anchor="middle" class="lane-h c-codex">CODEX, THINKING</text>
        <text x="600" y="34" text-anchor="middle" class="lane-h c-codex">CODEX ON THE WEB</text>
        <text x="840" y="34" text-anchor="middle" class="lane-h c-judge">THE CHECK</text>
        <rect x="16" y="52" width="208" height="46" rx="6" class="bx-accent"/>
        <text x="120" y="71" text-anchor="middle" class="t2">Asks the question</text><text x="120" y="88" text-anchor="middle" class="ts">with why, and its limits</text>
        <path d="M120,98 V106 H360 V111" class="ln" marker-end="url(#f4-ah)"/>
        <rect x="256" y="112" width="208" height="46" rx="6" class="bx-codex"/>
        <text x="360" y="131" text-anchor="middle" class="t2">Plans, asks what is unclear</text><text x="360" y="148" text-anchor="middle" class="ts">3, 5 or 7 sub-questions</text>
        <path d="M360,158 V166 H120 V171" class="ln" marker-end="url(#f4-ah)"/>
        <rect x="16" y="172" width="208" height="46" rx="6" class="bx-accent"/>
        <text x="120" y="191" text-anchor="middle" class="t2">You answer and approve</text><text x="120" y="208" text-anchor="middle" class="ts">edit the plan if you like</text>
        <path d="M120,218 V226 H600 V231" class="ln" marker-end="url(#f4-ah)"/>
        <rect x="496" y="232" width="208" height="46" rx="6" class="bx-codex"/>
        <text x="600" y="251" text-anchor="middle" class="t2">Searches each, side by side</text><text x="600" y="268" text-anchor="middle" class="ts">link, date, exact quote</text>
        <path d="M600,278 V286 H360 V291" class="ln" marker-end="url(#f4-ah)"/>
        <rect x="256" y="292" width="208" height="46" rx="6" class="bx-codex"/>
        <text x="360" y="311" text-anchor="middle" class="t2">Finds the gaps</text><text x="360" y="328" text-anchor="middle" class="ts">what is missing or disputed</text>
        <path d="M360,338 V346 H600 V351" class="ln" marker-end="url(#f4-ah)"/>
        <rect x="496" y="352" width="208" height="46" rx="6" class="bx-codex"/>
        <text x="600" y="371" text-anchor="middle" class="t2">Searches the follow-ups</text><text x="600" y="388" text-anchor="middle" class="ts">up to 3 a round</text>
        <text x="600" y="428" text-anchor="middle" class="ts">deep: gaps and follow-ups again</text>
        <path d="M600,398 V406 H360 V411" class="ln" marker-end="url(#f4-ah)"/>
        <rect x="256" y="412" width="208" height="46" rx="6" class="bx-codex"/>
        <text x="360" y="431" text-anchor="middle" class="t2">Writes the report once</text><text x="360" y="448" text-anchor="middle" class="ts">every claim cites a quote</text>
        <path d="M360,458 V466 H840 V471" class="ln" marker-end="url(#f4-ah)"/>
        <rect x="736" y="472" width="208" height="46" rx="6" class="bx-tool"/>
        <text x="840" y="491" text-anchor="middle" class="t2">Finds each quote on its page</text><text x="840" y="508" text-anchor="middle" class="ts">the page, fetched afresh</text>
        <path d="M840,518 V531" class="ln" marker-end="url(#f4-ah)"/>
        <rect x="736" y="532" width="208" height="46" rx="6" class="bx-judge"/>
        <text x="840" y="551" text-anchor="middle" class="t2">jev judges each quote</text><text x="840" y="568" text-anchor="middle" class="ts">does it support its claim?</text>
        <path d="M840,578 V586 H120 V591" class="ln" marker-end="url(#f4-ah)"/>
        <rect x="16" y="592" width="208" height="46" rx="6" class="bx-accent"/>
        <text x="120" y="611" text-anchor="middle" class="t2">Reads the check</text><text x="120" y="628" text-anchor="middle" class="ts">unconfirmed stays unconfirmed</text>
      </svg>
    </div>
    <figcaption>One research run at standard depth. It stops after the plan so you can answer codex's questions and approve the plan. Each search is its own fresh codex conversation, so they run side by side, and every step is saved, so a run that stops resumes where it stopped.</figcaption>
  </figure>
  <ul>
    <li><strong>Questions and the plan first:</strong> codex lists what is unclear and writes the plan, and the run stops there, as ChatGPT asks first and Gemini shows its plan. Your answers make a new plan; an approved plan, edited if you like, goes on with <code>--resume</code>. <code>--go</code> skips the stop.</li>
    <li><strong>One pass:</strong> the report is written once, from the findings only, so it reads as one piece. It says where sources disagree and what stays uncertain.</li>
    <li><strong>The check:</strong> each cited page is fetched afresh and the quote is looked for on it; then jev judges whether the quote supports its claim. Jev's number is a probability: 0.65 or more is supported, 0.35 or less is not, and between is unclear. A quote that is not on its own page never counts, whatever jev says.</li>
    <li><strong>Nothing is lost:</strong> every step is saved in the project. A run that stops, because codex fails or its allowance runs out, resumes from the last finished step without searching again.</li>
    <li><strong>Pages are data:</strong> the findings reach the later steps fenced off as quoted material, and an instruction found on a web page is never followed.</li>
    <li>Without jev the quotes are still checked on their pages. With codex switched off nothing is sent, and Claude researches with its own tools and says so.</li>
  </ul>
</section>

<section id="todo">
  <h2>The board</h2>
  <ul>
    <li>A small database inside each project that opts in. The skill refuses to make a board where none exists.</li>
    <li>In those projects it replaces the built-in to-do list: the next task, new tasks, status changes, and anything found along the way.</li>
    <li>Ids keep the board's own prefix. A roast's findings become children of the task they came from.</li>
    <li>Two versions of the tool, one for Node and one for Python, give the same output byte for byte.</li>
  </ul>
</section>

<section id="project">
  <h2>What a project adds</h2>
  <p>The bundle holds none of these; each project keeps its own, inside its <code>.claude</code> folder.</p>
  <ul>
    <li><strong>Its rules:</strong> a file in <code>.claude/rulesets</code>, read by <code>/sijav-clauder:rules</code> in that project. A project rule wins over a general rule it clashes with.</li>
    <li><strong>Its loop:</strong> a law file named <code>&lt;name&gt;-loop.local.md</code>, the hooks that give it back after each reply and after a compaction, and the command that starts it.</li>
    <li><strong>Its board:</strong> <code>todo.db</code>, for the todo skill.</li>
    <li><strong>Its records:</strong> codex sessions and roast records are written into the project, never into the skills folder.</li>
  </ul>
</section>

<section id="setup">
  <h2>Set up codex and jev</h2>
  <p>Most of the skills need nothing more than Claude Code. Two need a service you set up once: <strong>codex</strong>, which reviews work and searches the web (the codex, search, research and roast skills), and <strong>jev</strong>, TypeSafe's judge (the roast, and the research's citation check). Either can be switched off, and the skills then work without it. Also needed: Python 3.13, Node 22.13 or newer, and <code>uv</code> for the tests.</p>
  <div class="cols">
    <div>
      <h3>codex</h3>
      <ol>
        <li>Install the codex command line: <code>npm install -g @openai/codex</code>.</li>
        <li>Sign in once, yourself: run <code>codex</code> in a terminal and follow its sign-in. The skills never sign in for you and hold no codex token.</li>
        <li>Check it: <code>codex --version</code> prints a version. The skills find <code>codex</code> on your PATH.</li>
      </ol>
      <p><strong>Switch it off</strong> with <code>SIJAV_CODEX=off</code>: the codex runner, search, research and the roast start nothing, and Claude does the work itself with its own tools. <strong>Switch it back on</strong> by removing the setting, or setting it to <code>on</code>.</p>
    </div>
    <div>
      <h3>jev (TypeSafe)</h3>
      <ol>
        <li>Get an API key from TypeSafe (docs.typesafe.ai).</li>
        <li>Save it in a file of its own, <strong>outside the plugin's folder</strong>: Claude Code copies an installed plugin into its own cache, files and all.</li>
        <li>Install the library into the Python that runs the skills, the <code>python</code> on your PATH: <code>python -m pip install typesafe-sdk</code>. Check it with <code>python -c "import typesafe_sdk"</code>, which prints nothing when it works.</li>
        <li>Point the skills at the key: set <code>SIJAV_JEV_KEY_FILE</code> to the key file's full path. Without it, the roast reads <code>TYPESAFE_API_KEY</code>.</li>
        <li>Check it end to end: run one roast (<code>/sijav-clauder:roast</code>). The record's jev line names the jev model that answered, or says why jev was not used.</li>
      </ol>
      <p><strong>Switch it off</strong> with <code>SIJAV_JEV=off</code>: the roast runs with codex alone, and its record says why; research still checks every quote on its page, and its check says jev did not judge them. It does the same by itself when there is no key, the library is missing, or jev fails during a run. <strong>Switch it back on</strong> by removing the setting, or setting it to <code>on</code>.</p>
    </div>
  </div>
  <h3>Where the settings go</h3>
  <p>In Claude Code's settings, under <code>env</code>: your user settings file for every project, or a project's <code>.claude/settings.json</code> for that project only. For example, with the key set and codex switched off:</p>
  <pre class="code">{
  "env": {
    "SIJAV_JEV_KEY_FILE": "/full/path/to/typesafe.key",
    "SIJAV_CODEX": "off"
  }
}</pre>
  <p><code>off</code>, <code>0</code>, <code>false</code> or <code>no</code> switches a service off; any other value, or no setting at all, leaves it on. Start a new session after changing a setting.</p>
</section>

<section id="install">
  <h2>Install</h2>
  <p>The plugin comes from the <code>Sijav-Skills</code> marketplace, which holds it and its git history. Run these once in a terminal, then start a new session:</p>
  <div class="tablewrap"><table><tbody>
    <tr><td class="cmd"><code>claude plugin marketplace add sijav/Sijav-Skills</code></td></tr>
    <tr><td class="cmd"><code>claude plugin install sijav-clauder@sijav-skills</code></td></tr>
  </tbody></table></div>
  <p>To work on the skills themselves, clone the repository and add the local folder instead: <code>claude plugin marketplace add &lt;your clone&gt;</code>. The source is at <a href="{{SOURCE}}">{{SOURCE}}</a>.</p>
  <p>To pick up a newer version later, run these, then start a new session or run <code>/reload-plugins</code>:</p>
  <div class="tablewrap"><table><tbody>
    <tr><td class="cmd"><code>claude plugin marketplace update sijav-skills</code></td></tr>
    <tr><td class="cmd"><code>claude plugin update sijav-clauder@sijav-skills</code></td></tr>
  </tbody></table></div>
  <p>Claude Code keeps its own copy of an installed plugin, every file in the plugin's folder included, so keep keys outside it. After changing the skills in a clone, raise the plugin's version and run the update.</p>
</section>

<section id="change">
  <h2>Changing a rule</h2>
  {{CHANGING}}
</section>
</div>
<script>
/* The menu marks the section you are reading, and on a narrow screen keeps
   that link in view. */
(() => {
  const links = [...document.querySelectorAll('nav.toc a[href^="#"]')];
  const sections = links.map(a => document.getElementById(a.hash.slice(1))).filter(Boolean);
  const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let current = null, queued = false;
  const mark = () => {
    queued = false;
    let active = sections[0];
    for (const s of sections) if (s.getBoundingClientRect().top <= 110) active = s;
    if (!active || active === current) return;
    current = active;
    for (const a of links) a.classList.toggle('on', a.hash === '#' + active.id);
    const on = links.find(a => a.classList.contains('on'));
    const bar = on && on.parentElement;
    if (bar && bar.scrollWidth > bar.clientWidth) {
      bar.scrollTo({left: on.offsetLeft - (bar.clientWidth - on.offsetWidth) / 2, behavior: calm ? 'auto' : 'smooth'});
    }
  };
  addEventListener('scroll', () => { if (!queued) { queued = true; requestAnimationFrame(mark); } }, {passive: true});
  mark();
})();
</script>
"""


README = REPO / "README.md"
# The page draws each flow as inline SVG; the README gets the same flow as a Mermaid chart,
# which GitHub draws. One chart per section that has a figure; the build refuses a figure
# without one.
CHARTS = {
    "loading": """
flowchart LR
  S["A session starts<br/>no rules loaded"] -->|you type one| R1["/sijav-clauder:rules"]
  S --> R2["/sijav-clauder:rules dev"]
  S --> R3["/sijav-clauder:rules design"]
  R1 -->|reads| G["general: G1 to G6"]
  R2 -->|reads| D["general + dev: D1 to D5, dev-round"]
  R3 -->|reads| X["general + design, none yet"]
  G -->|then| P["the project's own rule set, if any<br/>it wins a clash"]
  D --> P
  X --> P
""",
    "loop": """
flowchart TD
  A(["Claude finishes a reply"]) --> B{"Is there a .stop file?"}
  B -->|yes| B1["Paused, for every session<br/>only when you ask for a pause"]
  B -->|no| C{"Does the project have a law file?"}
  C -->|no| C1["No loop in this project<br/>the hook does nothing"]
  C -->|yes| D{"Is this the loop's own session?"}
  D -->|no| D1["Any other session stops normally<br/>it never gets the law"]
  D -->|yes| E{"Did the reply end with the finish promise?"}
  E -->|yes| E1["The loop is finished<br/>only when all of its work is done"]
  E -->|no| F{"Has the reply counter hit its cap?"}
  F -->|yes| F1["The cap is reached<br/>set in the law, rarely reached"]
  F -->|no| G["The stop is blocked<br/>the law comes back as the next prompt"]
  G -->|Claude works on it, and this runs again| A
""",
    "roast": """
sequenceDiagram
  participant C as Claude
  participant X as codex
  participant T as The roast tool
  participant J as jev
  C->>X: runs the roast on the plan, before building
  X->>T: reads the plan and the code, frames 2 to 5 questions
  Note over T: keeps code away from jev and adds your critical question<br/>code found, back to codex once
  T->>J: every question, in one batch
  Note over J: too long, codex shortens it once<br/>limit 32k tokens for the state plus the longest question
  J->>X: the answers, as numbers
  X->>T: writes its reading, failing scenarios and findings
  T->>C: keeps a record of the run, never overwritten
  Note over C: decides each finding, the number is only an alarm
""",
    "research": """
sequenceDiagram
  participant C as Claude and you
  participant X as codex, thinking
  participant W as codex on the web
  participant K as The check
  C->>X: the question, why it is asked, and its limits
  X->>C: questions for you, and a plan of 3, 5 or 7 sub-questions
  Note over C: you answer, or approve the plan, edited if you like
  C->>W: the approved plan
  Note over W: one search per sub-question, side by side<br/>each claim with its link, date and exact quote
  W->>X: the findings
  X->>W: the gaps, as up to 3 follow-up questions
  Note over X,W: at deep depth, gaps and follow-ups once more
  W->>X: more findings
  Note over X: writes the report once, every claim citing a quote
  X->>K: every claim, with its link and quote
  Note over K: fetches each cited page and looks for the quote on it<br/>then jev judges whether the quote supports the claim
  K->>C: the report and its citation check
""",
}


def readme(page: str) -> str:
    """The README: the same guide as the website, plus what a clone of this repository needs."""
    import readme_md

    lede, _ = readme_md.page_parts(page)
    tests = " ".join(p.relative_to(SK).as_posix() for p in sorted(SK.glob("*/test_*.py")))
    names = ", ".join(s for s, *_ in SKILLS)
    return f"""# Sijav Skills

**[Open the guide as a web page: sijav.github.io/Sijav-Skills]({SITE})**

A Claude Code plugin marketplace with one plugin, **sijav-clauder**. {lede}

This README and [the web page]({SITE}) are the same guide: `python site/build_site.py` writes
both from the skills' own files. Change the skills or `site/build_site.py`, never this file by
hand.

```
Sijav-Skills/                     the marketplace (this git repository)
  .claude-plugin/marketplace.json
  Sijav-Clauder/                  the sijav-clauder plugin
    .claude-plugin/plugin.json
    skills/{names}
  site/build_site.py              writes the website and this README
  docs/index.html                 the website, served by GitHub Pages
```

{readme_md.guide(page, CHARTS)}

## Tests

From `Sijav-Clauder/skills`:

- `uv run --no-project --with pytest --with typesafe-sdk python -m pytest {tests} -q`
- `python todo/test-parity.py`
- `node todo/test-subtasks.mjs`

## The website and this README

`python site/build_site.py` writes `docs/index.html`, which GitHub Pages serves from the
`docs` folder of `main`, and this README, both from the skills' own files. Rebuild and commit
them after changing a rule or a skill. The build refuses to write either when it finds a
project's name, a person's name or a local path, and checks every tracked file for private
words.

## Keeping it clean

- No project names, paths or project rules go into the skills; they belong to the project.
- Change a rule the way `rules/SKILL.md` says, and log it in `rules/log.md`. That log is the
  history of each rule, so it keeps the evidence it was written from.
"""


def main(argv: list[str]) -> int:
    page = (TEMPLATE.replace("{{SOURCE}}", SOURCE)
            .replace("{{SKILLS_TABLE}}", skills_table())
            .replace("{{GENERAL}}", ledger(SK / "rules" / "general.md"))
            .replace("{{DEV}}", ledger(SK / "rules" / "dev.md"))
            .replace("{{DEVROUND}}", dev_round())
            .replace("{{DESIGN}}", design_note())
            .replace("{{CHANGING}}", changing_a_rule()))
    left = re.findall(r"\{\{[A-Z_]+\}\}", page)
    guide = readme(page)
    hits = [(what, m.group(0), text[max(0, m.start() - 50):m.end() + 30].replace("\n", " "))
            for text in (page, guide) for what, rx in FORBIDDEN.items() for m in re.finditer(rx, text)]
    in_repo = private_in_repo() if PRIVATE_RULES else []
    if left or hits or in_repo:
        for what, found, ctx in hits:
            print(f"REFUSED: {what} {found!r} in: ...{ctx}...")
        if left:
            print(f"REFUSED: unfilled placeholders {left}")
        for where in in_repo:
            print(f"REFUSED: {where} (a tracked file; the repository is public)")
        return 1
    if not PRIVATE_RULES:
        print("note: no site/private-words.txt here, so private words were not checked")
    head, sep, body = page.partition("</style>\n")
    document = (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f'<meta name="description" content="{html.escape(DESCRIPTION)}">\n'
        f'<link rel="canonical" href="{SITE}">\n'
        + head + sep + "</head>\n<body>\n" + body + "</body>\n</html>\n"
    )
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(document, encoding="utf-8")
    print(f"wrote {OUT} ({len(document):,} characters)")
    with open(README, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(guide)
    print(f"wrote {README} ({len(guide):,} characters)")
    if "--fragment" in argv:
        frag = Path(argv[argv.index("--fragment") + 1])
        frag.write_text(page, encoding="utf-8")
        print(f"wrote {frag} (for the Claude page viewer)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
