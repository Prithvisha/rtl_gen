# RTL Gen — Roadmap

A running log of what's shipped and what's next, so nothing discussed gets lost between sessions.
Sahastra Mudra Semiconductors.

## Shipped

**Phase 1 — Core generation + verification**
- Describe a chip in plain English → Gemini generates Verilog + self-checking testbench
- Auto-compile/simulate with Icarus Verilog, auto-fix loop on failure (configurable retry count)
- Distinguishes compile errors from functional (self-check) failures and feeds the right context back to the fixer

**Phase 1.5 — Debug & sharing tools**
- Bug explainer: plain-English root-cause explanation on every failed attempt (separate Gemini call, never blocks the main flow if it fails)
- Waveform viewer: real VCD capture from the testbench, rendered as an interactive digital timing diagram (Plotly), bus values shown as decimal with hover detail
- Waveform readability polish: labels on fast-changing buses skip when they'd overlap, transition gridlines mark every value change, full detail still available on hover
- Verified Gallery: every fully-passing design auto-saved for the session, exportable as a single shareable standalone HTML file (no login needed to view)

**Phase 2 — Gate-level synthesis**
- Yosys open-source synthesis of the generated design (testbench automatically stripped out first)
- Cell/wire statistics and cell-type breakdown shown inline
- Real gate-level schematic (SVG, via Yosys + Graphviz), embedded and downloadable
- Synthesized netlist viewable and downloadable
- "How to read this schematic" guide embedded next to every schematic (live app + exported gallery HTML) — shapes glossary, how to trace signal flow, why gates don't match the original operators 1:1
- Gallery export parity: the standalone exported HTML now carries the schematic + stats + netlist + guide, not just code/sim output

## Under consideration — Phase 3 and beyond

Not started. Flagging trade-offs so we can decide deliberately rather than by default.

- **Physical layout (OpenLane / GDSII)** — full RTL-to-GDSII flow. Needs a feasibility check against whatever hosting we're using (OpenLane's toolchain is heavy — Docker-based, large images, long run times); likely not viable on Streamlit Community Cloud as-is. Worth scoping as a separate service if we want it.
- **Skip-synthesis toggle** — auto-synthesis adds a few seconds per generation. Fine for typical designs; worth a checkbox to disable it once designs get big enough that it's noticeably slow.
- **A real "is this schematic worth viewing inline" heuristic, not raw cell count** — confirmed with a real test (32-bit shift register with parallel load → 65 cells: 32 `$_DFFE_PP0P_` + 32 `$_MUX_` + 1 `$_OR_`) that cell count alone is the wrong signal. That design *looks* dense in the schematic (32 repeated flip-flop+mux blocks side by side) purely because it's wide, not because it's logically complex — each bit is independent. A genuinely tangled design (like the carry-chain logic in the counter example) is harder to read at a much lower cell count. A better heuristic would look at something like unique cell *types* or max fan-in/logic depth per output, not total cells, before suggesting "download the SVG instead of viewing inline."
- **Multi-module / hierarchical designs** — current synthesis step assumes a single flat target module. Larger asks (a design that instantiates sub-modules) would need the module-extraction step to pull in dependencies, not just the top module.
- **Signal search/grouping in the waveform view** — fine for now, but a design with many signals (wide buses, many submodule ports) could use a search box or hierarchy filter instead of one flat multiselect.
- **Parameterized designs** (Verilog `parameter`/`localparam` driven by the prompt, e.g. "an 8-bit vs 16-bit version of this ALU") — not yet handled explicitly in the generation prompt.

## How to use this file

Add to "Shipped" once something is live and tested against the real toolchain (not just planned). Add to "Under consideration" when we discuss an idea worth remembering but haven't committed to yet. Revisit before starting new work so decisions don't get made twice.