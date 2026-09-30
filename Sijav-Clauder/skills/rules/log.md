# Rule log: general, dev and design rules

The history of every rule in `general.md`, `dev.md` and `design.md`: what it was
made from, the evidence, and the owner's words. Read a rule's entry before
changing it (see "Changing a rule" in `SKILL.md`), and add an entry after.

## 2026-09-28 · How these rules were made

On 2026-09-28 every rule that reached a session was audited: 19 conflicts, fixed one by one, then merged and moved here. The public guide to the result: https://sijav.github.io/Sijav-Skills/. The audit's work files and the original rule files stay in the project where it ran. Line numbers below are the ones the files had on the morning of 2026-09-28.

### G1 · Tell me the truth, plainly
Made from:
- `~/.claude/CLAUDE.md` line 7: "Give no excuse when I ask something, I need literal
  explanation! the truth!"
- Same file, "2026-09-16: when I ask WHY, answer with the mechanism" (lines 9-31) and its three rules:
  "I don't know" is a correct answer; never invent an authority; do not just agree with me.
- Memory `self-roast-and-investigate.md`, item 2 (2026-09-23): "'I don't know' is acceptable; 'I can't
  investigate because of tail' and then not investigating is [...]".
- The law, line 52: "In your reports, separate evidenced causes, hypotheses, and unknowns. Say 'I don't know'
  when necessary." Also its copies in the codex and loop skills.
Evidence: the owner, 2026-09-28: "my problem was the ai were not giving it to me straight and instead it was
hiding the actual facts from me". A reasoning block in session 22e1fae6 (2026-09-26, 22:11 UTC) says of itself:
"I bury what I didn't do at the end of long reports".

### G2 · Newer instructions win; old ones get deleted
Made from conflict C2 (entry below): CLAUDE.md line 3 and the 2026-09-16 stop-and-ask section; memory
`fix-evident-typos.md`; the law's trial precedence line.

### G3 · When I ask a question, check twice
Made from conflict C6 (entry below): CLAUDE.md "roast yourself, then roast the roast" (2026-09-25), the law's
every-turn roast (step 1, 1a to 1c), memory self-roast items 1 and 8, STEP 0 in the project's board tool.

### G4 · Ask me only when the decision is really mine
Made from conflict C1 (entry below), plus the no-gates lines: the law, line 39: "Do not create new gates."; line
72: "... add no owner-approval gate or numerical threshold."; the old synced loop skill: "Never invent a gate the
owner did not ask for."; the todo skill: "If a gate seems necessary, tell the owner and let them decide."
Evidence for no gates: the /sijav-loop note (2026-09-10): the re-roast gate "cost a full working day, eight
stalled background jobs and a 450 minute iteration that never finished, with no product code written."

### G5 · Do what I asked, and finish it
Made from:
- The law, line 18: "Follow the owner's explicit instructions exactly. Never do work the owner did not ask for or
  the procedure does not require."
- The law, steps 1d and 1e: investigate blockers inside the current task; "Re-read your report, commitments, and
  the owner's instruction before acting. Fulfill the outstanding explanation, investigation, or stated next step
  before switching".
- The law, lines 53-55: "Stop only the action the owner identifies ... If the target is unclear, stop the current
  action and clarify"; "Treat killed commands and interrupted calls as operation-specific events".
- Memory self-roast items 4 and 7: "do not jump to something unrelated"; "'Stop' about a running command means
  stop that command, not the loop."
- Their copies in the codex and loop skills.

### G6 · Plain replies in labeled sections
Made from conflict C18 (entry below), plus memory `full-paths-or-the-file.md` (2026-09-26: "for next time either
show a complete path or show a [...] file!", "create a table and write it as an output") and memory
`item-numbers-with-titles.md` (2026-09-23: "item number mentions ONLY WITH ITS [...] TITLE so I can tell you
what's going on").

### D1 · Keep the full output of commands
Made from the law, lines 43-46 (capture every long or background command's full output from launch; no
truncating filter; line buffering; keep capture alive; record command, folder, start time, exit status), memory
self-roast item 3 ("Never cut output to a tiny number of lines (| tail -14, tail -1) ... Write the full output to
a file"), and the copies in the codex and loop skills. Four over-specified lines became two sentences.
Evidence: memory self-roast (2026-09-23): a plan roast hung and the output needed to explain it was cut off.

### D2 · Never run the same work twice
Made from the law, lines 47-49, memory self-roast item 5 ("I once declared a still-running roast dead and
started a second one plus two helpers; the owner had to kill four tasks") and item 11 ("A Monitor that expires
leaves its tail -F | grep running (2026-09-24: 18 orphans)"), and the skill copies.

### D3 · Failures
Made from the law, lines 40-42 and 50-51 (failing checks are actionable; exact causes; investigate with the
evidence you have), step 6d ("never re-run a check merely to change its answer"), and the skill copies.

### D4 · Tests
Made from the law, lines 16-17 and 37-38, and the copies in the codex and loop skills.

### Dropped
- The law, lines 11-12: "Perform the current task yourself. Use your own knowledge and skill to implement it."
  They said nothing that E4 ("code is Claude's") doesn't say.
- The codex skill's 60 lines and the loop skill's 79 lines that repeated the law.

## 2026-09-28 · Conflicts fixed before the merge

### C1 · 2026-09-28 · Ask only for decisions that are really the owner's

**Rule now.** `~/.claude/CLAUDE.md`, section "2026-09-28: ask me
only when the decision is really mine":

> Ask me only when the decision is really mine or only I can do it: money,
> deleting or publishing, access and accounts, product choices the code and
> my words don't settle. Ask those through the question card. Everything
> else: decide, do it, then tell me what you did and what you suggest next.

**It replaced.**
- CLAUDE.md, "2026-09-25: turn my instructions into YOUR numbered execution
  order, then ask": "... Then ask me through the question tool whether that
  is correct, and do nothing until I say it is."
- Memory `ask-owner-via-question-tool.md` (2026-09-23): "I ask through the
  AskUserQuestion (question/input) tool so the loop pauses until they answer.
  Never only write the request in chat text."
- Memory `execution-order-readback.md` (2026-09-25): "ask via
  AskUserQuestion 'is this correct?' and wait."
- Memory `self-roast-and-investigate.md`, item 10 (2026-09-25): every ask
  left in chat is re-asked through a card.
- Law step 1c: "ask through the question/input tool now and wait for the
  answer before dependent work".
- Codex skill: "Send every question for the owner through the question/input
  tool. Never put owner questions only in a chat summary. Assume the owner
  will not read summaries while a loop runs."
- The project's board tool, STEP 0 item 5: "ask any still-unanswered owner
  question through the input tool now."

**Why the old rules existed.** 2026-09-23: requests written only in chat got
lost (an `.env` key request, a permissions edit). The owner: "when you want
something from me you don't just write it as output you NEED to call input
tool". 2026-09-25: the owner wanted to see Claude's reading of an instruction
as a numbered plan before work started.

**Evidence for the change.** Session logs 09-23 to 09-28, counted
in the audit: 212 question cards, 30 of them
"is this order correct?" readbacks, 33 saying "nothing runs until you answer",
54.8 hours in total waiting on cards. On 09-27 the card "Is this 10-step order
correct?" sat from 19:12 to 00:56 (344 min); the owner answered "I asked? What
did I asked?", and Claude asked again one minute later. These rules clashed
with the law's "add no owner-approval gate" and "Do not create new gates".

**Owner's decision.** Card, 2026-09-28: "Real decisions only".

**Keep in mind.** The original need is still real: a decision that is really
the owner's still goes through the card, never only into chat.

### C18 · 2026-09-28 · Plain replies in labeled sections; % done stays

**Rule now.** `~/.claude/CLAUDE.md`, "2026-09-28: plain replies
in labeled sections":

> Keep replies plain. Answer first. A longer reply is fine when it is split
> into sections, each with a label that says what it is about. Use full paths
> and item titles, never bare item numbers. Explain a technical word the
> first time you use it.

**It replaced.** CLAUDE.md (2026-09-25): "Never forget the owner's initial
intent: it goes at the top of every plan, every brief to codex and every
question to jev." Also the readback's "the EXACT words you will send" and
"never cut content" (removed with C1).

**Kept.** The law's "Report progress in chat with a percentage done".

**Why the old rules existed.** 2026-09-25: the owner's intent kept getting
lost between Claude, codex and jev, and the owner wanted to see the exact
words sent to codex.

**Evidence for the change.** Average assistant text per message was about 45
words on 09-23 and 09-24, about 95 on 09-25 and 09-26 after these rules were
added, about 50 on 09-27 (a timing match, not proof). The owner's card answer
on 09-27 03:08: "Ok either you are not speaking my language or we don't
understand each other".

**Owner's decision.** "short and plain, keep % done I never had any problem
with that", then: "I don't mind a longer reply ... as long as it is with
sections that what each section is about ... so I know exactly what I am
reading".

**Keep in mind.** The owner's exact words still reach codex through the law's
trial rule "Include verified evidence in every brief to codex: the owner's
exact words, ...".

### C6 · 2026-09-28 · Answer first; check the owner's questions twice

**Rule now.** `~/.claude/CLAUDE.md`, "2026-09-28: when I ask a
question, check twice": check the question (what is really asked, is any
assumption wrong); draft the answer and check it (every fact checked now,
nothing missing or softened, what the other side would say); answer first,
then show the checks under their own labels. Only for the owner's questions;
no codex, no jev. The law's step 1 is now: "Answer the owner's message first.
For a question, use the question check in CLAUDE.md (2026-09-28). Obey an
immediate stop instruction first."

**It replaced.**
- CLAUDE.md, "2026-09-25: roast yourself before every task and every answer,
  then roast the roast".
- Law step 1 and 1a to 1c: "Begin EVERY turn by reading and self-roasting
  your last summary ... Include engagement, continuation, waiting,
  answer-only, and recovery turns. Treat every compaction ... as a turn
  start"; re-running the last item's tests; checking every ask went through
  a card. (1d and 1e stay, now named 1a and 1b.)
- Memory `self-roast-and-investigate.md`, items 1 and 8.
- The project's board tool: the STEP 0 "roast the last summary" checklist
  printed on every `next` (since 2026-09-06).
- The project's after-compaction hook and the loop skill's `compact.py`: "before
  any other action, run step 1 of the law below in full". Both now print "A
  COMPACTION JUST HAPPENED. The loop law is below. The summary above is a
  claim, not a record: check a fact before you rely on it." Their tests were
  updated; 41 tests passed.
- The 9-line copies of the turn-start roast in the codex and loop skills.

**Why the old rules existed.** 2026-09-06: "a summary is written at the moment
the model most wants to be finished" (the project's board tool). 2026-09-23: summaries
claimed work that was not done. On 2026-09-23 at 21:50 UTC a compaction
dropped the law, and 36 minutes of work skipped step 1.

**Evidence for the change.** The owner's direct questions waited behind roast
reports. Card answers on 09-27: 03:08 "Ok either you are not speaking my
language or we don't understand each other", 14:55 "You are not answering me
at all!", 16:45 "That was not the answer to my question". These are outcomes;
the logs do not prove which rule caused each one.

**Owner's decision.** "answer first, but roast my question and then before
answer, roast their answer (self roast only) ... only happens when ask a
question ... I just want the answer to be as honest as possible with all
sides checked", and "I don't mind a longer reply ... with sections".

**Keep in mind.** The goal was honesty, and it stays: every answer to a
question is checked, and the checks are shown.

### C2 · 2026-09-28 · Newer wins and the old rule is deleted

**Rule now.** `~/.claude/CLAUDE.md`, "2026-09-28: newer
instructions win; old ones get deleted": the newer instruction wins by its
intent; when a new rule replaces an old one, the old one is deleted in the
same edit; a clash Claude notices is followed the newer way and named in one
line. Temporary rules are the one exception: project files only, each says
what it overrides and its exit condition, the old rule is kept, and the
temporary rule is deleted when the exit condition is met.

**It replaced.**
- CLAUDE.md line 3: "Instruction needs a [swear word] date! the later
  instructions overrides the previous ones!"
- CLAUDE.md, "2026-09-16: a wrong or conflicting rule is asked about, not
  guessed at": "STOP and ask me through the user input tool (the question
  card) what it should say. Then wait. Do not pick one ..."
- Memory `fix-evident-typos.md` (2026-09-23), which only existed as an
  exception to that rule.

**Why the old rules existed.** 2026-09-16: conflicting rules were worked
around quietly; the owner wanted every contradiction removed for good.

**Evidence for the change.** 19 conflicts found on 2026-09-28. Cards asking
which rule stands: 09-23 19:39, 09-27 04:46, 15:05, 16:52.

**Owner's decision.** "newer always wins with intent and also when newer
gives, old should delete, but also there's a catch, there should be a
temporary exception newer rule that shouldn't delete the old rule, it's just
temporary and should be removed after a exit condition such as when this R&D
is done ... it should be project based only and not a global rule", then
"umm sure".

**Keep in mind.** The old rule's goal ("the contradiction gets REMOVED") is
kept by deleting the old rule in the same edit. The R&D rules in
`ProofOfConcept\ai\mock-test\rnd-rules\` are temporary project rules and were
left untouched.

## 2026-09-28 · G6 changed: normal length, labeled sections

**Old text.** "Keep replies plain. Answer first. A longer reply is fine when it is split
into sections, each with a label that says what it is about. ..."

**New text.** "Answer first. Then say everything that matters, at normal length, split
into sections, each with a label that says what it is about, so I know what I
am reading. ..." (the rest unchanged: full paths, item titles, tables, explain
technical words)

**Owner's words.** "also remove the law about reply briefly, no reply normally,
just with sections so I know what I am reading"

**Keep in mind.** The owner wants complete answers organized under labeled
sections, not short ones.

## 2026-09-28 · D4 rewritten, D5 added: tests by scenario, branches by phase

**D4 old text.** "Test code only; don't write tests for rules, prompts or prose. While open
to-dos remain, run the tests for the files you changed; run the full suite
when all to-dos are done."

**D4 new text.** Tests follow written, real-world scenarios covering 100% of the
code; while working, run only the test files the to-do changed; run an area's
full suite when its round of to-dos is done (a round is the to-dos open when
it started); when a scenario fails, fix the root cause, never change a test
just to make it pass.

**D5 new.** MVP: push to main after every to-do, no checks, don't ask. Phase 0:
push to development; development goes to main when phase 0 is done. After
phase 0: development, staging and main; development goes to staging per
story; main is the owner's unless the owner says otherwise. No other check.

**Why.** "Full suite when all to-dos are done" meant it almost never ran: the
newest full server run found was 2026-09-23 00:35 (894 tests, 4 min 40 s,
coverage 100), while work went on. The owner's aim is speed on large apps and root-
cause fixes instead of fixes made only to pass a test.

**Owner's words.** "code very fast, finish everything, don't wait up ... clear
todo on the specific project type ... then after many problems surface, then
start fixing them ... not in the middle"; "code coverage of 100% with test
suits that actually follows a certain written scenario ... it fix the root of
the problem instead of a lazy work"; "at mvp pushing to main is frequent,
after every to-do, without check"; "there should be no check though, or a
very fast cheap check, basically a law would do". Accepted with three
additions (anti-lazy line written out, rounds that end, the G4 clash settled
by "don't ask"): "use these, good eye".

**Keep in mind.** D5 is newer and more specific than G4 for pushes: at MVP,
pushing to main is not a question for the owner.

## 2026-09-28 · D4 becomes the dev-round skill; the skills become one project-independent bundle

**Change.** D4 (tests and rounds of to-dos) moved out of dev.md into a new
skill, dev-round. dev.md keeps one line pointing to it, and `/rules dev` uses
it. The skills no longer name a project or hold absolute paths: the rules
skill's project example, the codex skill's rule-set path and its path to
copy_session.py, the loop skill's project law name, start command and rule
list, the roast code's project notes, and the todo skill's project names in an
example and in code comments all went; each project's own files already hold
those details. Plan files from other projects' boards moved out of the bundle
to a private folder. README.md describes the bundle.

**Owner's words.** "Make D4 its own skill"; "For now just do the skills that we
have and create a bundle of skills"; "Skills should be project independent";
bundle form: "One folder, cleaned".

**Evidence.** Tests after the change: roast 23, codex runner 10, loop
compaction 5, todo parity (90 commands agree), todo subtasks: all pass. No
behaviour changed except where D4's text is read (dev-round now).

## 2026-09-28 · The skills become the sijav-clauder plugin

**Change.** The six skills moved out of the user's Claude folder into their own
repository, Sijav-Skills: a plugin marketplace (sijav-skills) holding
one plugin, sijav-clauder. Plugin skills carry the plugin's name, so /rules is
now /sijav-clauder:rules (and :rules dev, :rules design), /dev-round is
/sijav-clauder:dev-round, and so on. Paths to the skills' own scripts use the
skill's folder instead of the user folder. Two switches were added: SIJAV_JEV=off
(the roast runs with codex alone; it also falls back by itself when jev cannot be
used or fails) and SIJAV_CODEX=off (codex starts nothing; Claude does the work).

**Owner's words.** "install it ... make sure everything is project independent
and nothing will be in [the user's Claude folder] and forget about the other
cards"; "you can name this skill with prefix sijav"; "you can have it under
[the Sijav-Skills folder]/Sijav-Clauder"; "where does it take the token for jev and/or
codex, there should be an option to disable/fallback for it".

**Evidence.** Tests: 43 Python tests (roast, codex runner, loop compaction),
todo parity (90 commands) and todo subtasks pass; `claude plugin validate`
passes for the marketplace and the plugin.

## 2026-09-28 · G2 and G4: project rules, and standing permissions

**Change.**
- G2 gained: "Between a general rule and a project rule, the project rule wins
  in its own project, whatever their dates." G2 said the newer rule wins,
  while `SKILL.md` says a project rule wins over a general one it clashes
  with; the two disagreed whenever the general rule was the newer one.
- G4 gained: "A rule set can give you standing permission for one of these,
  as D5 does for pushes by phase; then act without asking." G4 lists
  publishing as the owner's decision, while D5 says to push to main in the
  MVP phase without asking; only this log said that D5 settles it.

**Owner's words.** For G2: "global always global, local overwrites"
(2026-09-28). For G4: D5's own words, "MVP: push to main after every to-do.
No checks, and don't ask."

**Evidence.** Found by a roast of the rules and checked against the files;
the fix was roasted, then checked again at the owner's request ("don't roast
but check them again to make sure"). The roast
records are kept in the project where the roasts ran.

## 2026-09-30 · dev-round (D4): write the tests during the round, test when the area is done

**Change.** The dev-round skill, which D4 points to. Old text: a round was "the to-dos that
were open when it started"; each to-do said "Build it, then test it. ... Together they cover
100% of the code. Run only the test files this to-do changed." New text: a round is one area's
to-dos (the front in general, or the back in general); each to-do writes its code and its tests
and runs none of them, the one exception being a to-do's own exit check when the project closes
it with one; when every to-do of the area is done, the area's full suite runs with coverage
(100%) and the end-to-end scenarios, and the tests themselves are checked for being correct and
logical (a planted fault where in doubt) before root-cause fixes.

**Owner's words.** "also one rule doesn't work, finish all to-dos before testing it"; then, after the loop's own account of running 451 tests mid-round: "and frankly it doesn't care how to fix that?"; "ok for that, there's a rule 100% code coverage + full user's pov scenario e2e tests, maybe that's what made this wrong! there should be something saying you just writing it in a matter that should then after all to-do for an area (section such as front in general or back in general) is finished then you start testing and make sure those are correct and logical"; "if it is not working like this please fix".

**Evidence.** "Build it, then test it" and "cover 100% of the code" sat under "Each to-do", so a
to-do looked unfinished until its tests had run and covered everything. A project's loop on
2026-09-30 ran its new exit test, planted-fault runs, then 31 test files and 451
tests "covering the code I changed", and answered the owner's question with a promise to do
better rather than a fix. The same happened on 2026-09-29 with full suites per to-do. The old
text is backed up outside any rules folder, dated 2026-09-30.
