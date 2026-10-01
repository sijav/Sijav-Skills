Browser fixture QA (2026-10-01)

Only a synthetic board in Windows TEMP was opened and mutated.
- Full board showed 12/12 rows, recorded api/web areas, complete multiline and Persian story, canonical todo.py order and parent/blocker reasons.
- Task detail kept small metadata at the top; timestamps appeared as human dates/times.
- MP-004 moved backlog -> in_progress with the real helper in the TEMP project. The existing browser session received Changes 1 via its connected WebSocket without a reload.
- Comparison showed separate Status and Updated fields with git-style removed/added lines and date formatting.
- Light and dark modes visibly changed; Relax showed both recorded Doing items and the canonical next eligible backlog item.
- Screenshot: todo-dashboard-relax.png (synthetic fixture only).
Final integrated preview QA at 2026-10-01 04:51 UTC: URL http://127.0.0.1:45428, synthetic TEMP project fixture project g9MESy. After restoring the briefly renamed synthetic DB, the same connected page returned to successful read and actual todo.py next. During the failed read it retained explicitly labelled last successful data and withdrew current picker/Next claims rather than inventing an empty board. Moving MP-004 with the unchanged source helper pushed Changes1 without reloading. Expanded comparison displayed removed backlog/added in_progress and human date lines for Updated. Dark theme and Relax showed two recorded Doing cards, stored stories/areas and MP-001 as next not-started. Screenshot refreshed at work/todo-dashboard-relax.png. Final52-test changes affect npm caller context and the Python mixed-parent reason probe; browser assets are byte-identical. All26 reviewed package files were recopied and hash matched. No actual project board was used for these tests.
