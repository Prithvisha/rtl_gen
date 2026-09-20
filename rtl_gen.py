import streamlit as st
from google import genai
import subprocess
import os
import tempfile
import re
import time
import shutil
import json
import glob
import html as html_lib
import base64
from datetime import datetime, timezone

def find_yosys():
    return shutil.which("yosys") or "yosys"

YOSYS_PATH = find_yosys()

def find_executable(name):
    """Find an executable, checking PATH first, then common Windows install locations."""
    found = shutil.which(name)
    if found:
        return found
    common_paths = [
        f"C:\\iverilog\\bin\\{name}.exe",
        f"C:\\Program Files\\iverilog\\bin\\{name}.exe",
        f"C:\\Program Files (x86)\\iverilog\\bin\\{name}.exe",
    ]
    for p in common_paths:
        if os.path.exists(p):
            return p
    return name  # fall back to bare name, will error clearly if truly missing

IVERILOG_PATH = find_executable("iverilog")
VVP_PATH = find_executable("vvp")

st.set_page_config(page_title="RTL Gen — AI Verilog Generator", page_icon="⚡", layout="wide")

st.markdown("""
<style>
.header {
    background: linear-gradient(135deg, #0D1B4B, #1A237E);
    padding: 24px 32px; border-radius: 8px; margin-bottom: 16px;
}
.header h1 { color: #C9A84C; margin: 0; font-size: 26px; }
.header p  { color: #90CAF9; margin: 4px 0 0 0; font-size: 13px; }
.bug-box {
    background: #2B1A1A; border-left: 4px solid #E57373; padding: 12px 16px;
    border-radius: 4px; margin: 8px 0;
}
.bug-box b { color: #E57373; }
</style>
<div class="header">
    <h1>⚡ RTL Gen &nbsp; <span style="font-size:14px;color:#90CAF9;font-weight:400">AI Verilog Generator + Auto-Verification</span></h1>
    <p>Describe a chip in English → get compiled, simulated Verilog RTL, a waveform, and a plain-English bug story &nbsp;·&nbsp; Sahastra Mudra Semiconductors</p>
</div>
""", unsafe_allow_html=True)

if "gallery" not in st.session_state:
    st.session_state["gallery"] = []
if "prompt_text" not in st.session_state:
    st.session_state["prompt_text"] = ""

# ── SIDEBAR ─────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🔑 Setup")
    api_key = st.text_input("Gemini API key", type="password",
                             help="Get a free key at aistudio.google.com/apikey")
    st.caption("Your key is only used in this session — never stored or sent anywhere else.")

    st.divider()
    st.markdown("### 🛠️ Tool Status")
    if os.path.exists(IVERILOG_PATH) or shutil.which("iverilog"):
        st.success(f"✅ Icarus Verilog found")
        st.caption(f"`{IVERILOG_PATH}`")
    else:
        st.error("❌ Icarus Verilog NOT found")
        st.caption("Install from bleyer.org/icarus and restart VS Code")

    try:
        import vcdvcd  # noqa: F401
        st.success("✅ Waveform engine ready")
    except ImportError:
        st.error("❌ 'vcdvcd' package missing — add it to requirements.txt")

    if shutil.which("yosys") or os.path.exists(YOSYS_PATH):
        st.success("✅ Yosys synthesis engine found")
    else:
        st.error("❌ Yosys NOT found")
        st.caption("Install Yosys (apt-get install yosys) to enable gate-level synthesis")

    st.divider()
    st.markdown("### 📚 Example prompts")
    examples = [
        "A 4-bit binary counter with synchronous reset and enable",
        "An 8-bit shift register with parallel load",
        "A 2-to-1 multiplexer with 8-bit data width",
        "A simple FIFO with 16-word depth and 8-bit width",
        "An 8-bit ALU supporting ADD, SUB, AND, OR, XOR",
    ]
    for ex in examples:
        if st.button(ex, use_container_width=True):
            st.session_state["prompt_text"] = ex

    st.divider()
    max_retries = st.slider("Max auto-fix attempts", 1, 5, 3,
                             help="How many times to retry if the generated code fails to compile")

    st.divider()
    st.caption(f"🖼️ {len(st.session_state['gallery'])} verified design(s) in this session's gallery")

# ── HELPER FUNCTIONS ────────────────────────────────────────────
def extract_verilog_code(text):
    """Pull Verilog code out of markdown code fences if present."""
    match = re.search(r"```(?:verilog)?\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()

def call_gemini(prompt, api_key, max_api_retries=4):
    client = genai.Client(api_key=api_key)
    last_error = None
    for i in range(max_api_retries):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt
            )
            return response.text
        except Exception as e:
            last_error = e
            if "503" in str(e) or "UNAVAILABLE" in str(e) or "overloaded" in str(e).lower():
                wait_time = (i + 1) * 3  # 3s, 6s, 9s, 12s
                time.sleep(wait_time)
                continue
            else:
                raise
    raise last_error

def build_generation_prompt(description, module_name):
    return f"""You are an expert Verilog RTL design engineer. Generate SYNTHESIZABLE, SIMULATION-READY Verilog-2001 code for the following design:

DESIGN DESCRIPTION: {description}

REQUIREMENTS:
- Module name must be exactly: {module_name}
- Use standard Verilog-2001 syntax (no SystemVerilog-only features)
- Include clear port declarations with appropriate widths
- Add brief comments explaining each major block
- Also generate a simple self-checking testbench module named {module_name}_tb that instantiates the design, applies a few test stimuli, and uses $display to print PASS or FAIL for each check, then $finish at the end
- Inside the testbench's initial block, BEFORE any stimulus is applied, add exactly these two lines so the waveform can be captured:
  $dumpfile("dump.vcd");
  $dumpvars(0, {module_name}_tb);
- Both modules must be in the same code block, module first then testbench
- Do not include any explanation text outside the code block — output ONLY the Verilog code inside a single ```verilog code fence
"""

def build_fix_prompt(original_code, error_message, description, module_name):
    is_functional_failure = "FUNCTIONAL VERIFICATION FAILURE" in error_message
    if is_functional_failure:
        guidance = """This is NOT a syntax or compilation error — the code compiled and ran fine, but some self-check assertions in the testbench reported FAIL.

Carefully analyse WHY each check failed. There are two possible causes:
1. The DESIGN (the module itself) has a real functional bug — fix the design logic.
2. The TESTBENCH has a bug in its stimulus generation (e.g. a control signal not deasserted between operations, wrong timing, wrong expected value calculation) — fix the testbench, not the design.

Trace through the signal timing carefully before deciding which one is wrong. Do not assume the design is at fault — testbench stimulus bugs (like a signal staying asserted for an extra clock edge) are equally common and must be checked first."""
    else:
        guidance = "Fix the syntax or compilation error shown below."

    return f"""The following Verilog code needs to be fixed.

ORIGINAL DESIGN REQUEST: {description}
MODULE NAME REQUIRED: {module_name}

CODE:
```verilog
{original_code}
```

ISSUE FOUND:
{error_message}

{guidance}

Keep the $dumpfile("dump.vcd"); and $dumpvars(0, {module_name}_tb); lines at the start of the testbench's initial block in the corrected version.

Output ONLY the corrected Verilog code (module + testbench) inside a single ```verilog code fence. No explanation text outside the code block.
"""

def build_explain_prompt(error_message, description, module_name):
    is_functional_failure = "FUNCTIONAL VERIFICATION FAILURE" in error_message
    kind = "a functional logic bug (code compiled and ran, but a self-check assertion failed)" if is_functional_failure else "a compile-time error"
    return f"""A Verilog design for "{description}" (module {module_name}) hit {kind}.

DETAILS:
{error_message}

In 2-4 short sentences of plain English (no jargon dump, no code), explain to a hardware engineer:
1. What went wrong, in plain terms.
2. Why it happened (the root cause).
Do not restate the raw error text. Do not include a code fence. Just the explanation, as prose.
"""

def explain_bug(error_message, description, module_name, api_key):
    """Ask Gemini for a plain-English root-cause explanation of a bug. Never raises — falls back gracefully."""
    try:
        prompt = build_explain_prompt(error_message, description, module_name)
        text = call_gemini(prompt, api_key, max_api_retries=2)
        return text.strip()
    except Exception:
        return None

def compile_and_simulate(verilog_code, module_name, work_dir):
    """Try to compile with iverilog and run with vvp. Returns (success, output_or_error, functional_pass, vcd_path_or_None)."""
    v_file = os.path.join(work_dir, f"{module_name}.v")
    vvp_file = os.path.join(work_dir, f"{module_name}.vvp")

    with open(v_file, "w") as f:
        f.write(verilog_code)

    compile_result = subprocess.run(
        [IVERILOG_PATH, "-o", vvp_file, v_file],
        capture_output=True, text=True, timeout=30
    )

    if compile_result.returncode != 0:
        return False, "COMPILATION ERROR:\n" + compile_result.stderr, False, None

    sim_result = subprocess.run(
        [VVP_PATH, vvp_file],
        capture_output=True, text=True, timeout=30, cwd=work_dir
    )

    vcd_candidate = os.path.join(work_dir, "dump.vcd")
    vcd_path = vcd_candidate if os.path.exists(vcd_candidate) else None
    # Fallback: some generations might name the dumpfile differently despite instructions
    if vcd_path is None:
        found = glob.glob(os.path.join(work_dir, "*.vcd"))
        vcd_path = found[0] if found else None

    if sim_result.returncode != 0:
        return False, "SIMULATION ERROR:\n" + sim_result.stderr, False, vcd_path

    # Compiled and ran cleanly — but did the self-checks actually pass?
    output_upper = sim_result.stdout.upper()
    has_fail = "FAIL" in output_upper

    if has_fail:
        # Ran fine mechanically, but functional self-checks failed.
        # This is NOT a compile error — feed the FAIL lines back as a functional bug report.
        fail_lines = "\n".join(
            line for line in sim_result.stdout.splitlines() if "FAIL" in line.upper()
        )
        functional_report = (
            "FUNCTIONAL VERIFICATION FAILURE (code compiled and ran, "
            "but self-check assertions failed):\n" + fail_lines +
            "\n\nFull simulation output:\n" + sim_result.stdout
        )
        return False, functional_report, False, vcd_path

    return True, sim_result.stdout, True, vcd_path

# ── WAVEFORM (VCD) HELPERS ──────────────────────────────────────
def load_vcd_signals(vcd_path):
    """Parse a VCD file and return (vcd_obj, default_signal_list, all_signal_list)."""
    from vcdvcd import VCDVCD
    vcd = VCDVCD(vcd_path)
    all_signals = list(vcd.signals)
    if not all_signals:
        return vcd, [], []
    # Default view = signals at the shallowest hierarchy depth (the testbench's own
    # top-level wires/regs, which mirror the DUT's ports) so the default chart isn't
    # cluttered with duplicated internal DUT signals. User can add more via multiselect.
    depths = [s.count(".") for s in all_signals]
    min_depth = min(depths)
    default_signals = [s for s, d in zip(all_signals, depths) if d == min_depth]
    return vcd, default_signals, all_signals

def render_waveform_figure(vcd, signal_names):
    """Build a plotly digital timing-diagram figure for the given signals."""
    import plotly.graph_objects as go

    if not signal_names:
        return None

    all_tvs = [vcd[s].tv for s in signal_names if vcd[s].tv]
    if not all_tvs:
        return None
    end_time = max(tv[-1][0] for tv in all_tvs)
    end_time = end_time + max(1, int(end_time * 0.05))

    fig = go.Figure()
    row_height = 1.3
    n = len(signal_names)

    for i, sig in enumerate(signal_names):
        tv = vcd[sig].tv
        y_base = (n - 1 - i) * row_height
        is_bus = "[" in sig or (tv and len(tv[0][1]) > 1)
        xs, ys = [], []
        labels = []
        for idx, (t, v) in enumerate(tv):
            v_clean = v.replace("x", "0").replace("z", "0") if v else "0"
            t_end = tv[idx + 1][0] if idx + 1 < len(tv) else end_time
            if is_bus:
                try:
                    val_disp = str(int(v_clean, 2))
                except ValueError:
                    val_disp = v
                xs += [t, t_end, None]
                ys += [y_base + 0.5, y_base + 0.5, None]
                labels.append((t, t_end, y_base + 0.85, val_disp))
            else:
                level = 1 if v_clean.strip() == "1" else 0
                xs += [t, t_end, t_end]
                ys += [y_base + level, y_base + level, y_base + level]

        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines", name=sig,
            line=dict(width=2, shape="hv"), showlegend=False
        ))
        if is_bus:
            for (t0, t1, ytxt, label) in labels:
                mid = t0 + (t1 - t0) / 2
                fig.add_annotation(x=mid, y=ytxt, text=label, showarrow=False,
                                    font=dict(size=10, color="#C9A84C"))
        fig.add_annotation(x=-end_time * 0.02, y=y_base + 0.5, text=f"<b>{sig}</b>",
                            showarrow=False, xanchor="right", font=dict(size=11))

    fig.update_layout(
        height=max(220, 70 * n),
        margin=dict(l=160, r=20, t=20, b=40),
        plot_bgcolor="#0D1117", paper_bgcolor="#0D1117",
        xaxis=dict(title="Time (simulation units)", showgrid=True, gridcolor="#222"),
        yaxis=dict(showticklabels=False, showgrid=False, range=[-0.3, n * row_height]),
        font=dict(color="#E6EDF3"),
    )
    return fig

# ── SYNTHESIS (YOSYS) HELPERS ────────────────────────────────────
def extract_design_module(combined_code, module_name):
    """Pull just the target design module (not the testbench) out of the combined code."""
    pattern = rf'\bmodule\s+{re.escape(module_name)}\b.*?endmodule'
    m = re.search(pattern, combined_code, re.DOTALL)
    if m:
        return m.group(0)
    return None

def synthesize_design(verilog_code, module_name, work_dir):
    """
    Run open-source Yosys synthesis on just the design module (testbench stripped out).
    Returns (success, message, stats_dict_or_None, svg_bytes_or_None, netlist_code_or_None).
    """
    design_only = extract_design_module(verilog_code, module_name)
    if design_only is None:
        return False, f"Could not isolate module '{module_name}' from the generated code for synthesis.", None, None, None

    synth_dir = tempfile.mkdtemp(dir=work_dir)
    design_file = os.path.join(synth_dir, f"{module_name}_design.v")
    netlist_file = os.path.join(synth_dir, f"{module_name}_netlist.v")
    svg_prefix = os.path.join(synth_dir, f"{module_name}_schem")

    with open(design_file, "w") as f:
        f.write(design_only)

    yosys_script = (
        f"read_verilog {design_file}; "
        f"synth -top {module_name}; "
        f"stat; "
        f"write_verilog -noattr {netlist_file}; "
        f"show -format svg -prefix {svg_prefix} {module_name}"
    )

    try:
        result = subprocess.run(
            [YOSYS_PATH, "-p", yosys_script],
            capture_output=True, text=True, timeout=60, cwd=synth_dir
        )
    except subprocess.TimeoutExpired:
        return False, "Synthesis timed out (design may be too large or complex for this quick pass).", None, None, None
    except FileNotFoundError:
        return False, "Yosys is not installed on this host.", None, None, None

    if result.returncode != 0:
        return False, "SYNTHESIS ERROR:\n" + result.stdout[-2000:] + "\n" + result.stderr[-2000:], None, None, None

    # Parse the cell/wire stats block out of Yosys's stdout
    stats = {"cells_total": None, "cell_types": {}, "wires": None}
    stat_section = re.search(r"=== .* ===\n(.*?)(?=\n\n|\Z)", result.stdout, re.DOTALL)
    if stat_section:
        block = stat_section.group(1)
        m_wires = re.search(r"Number of wires:\s+(\d+)", block)
        m_cells = re.search(r"Number of cells:\s+(\d+)", block)
        if m_wires:
            stats["wires"] = int(m_wires.group(1))
        if m_cells:
            stats["cells_total"] = int(m_cells.group(1))
        for line in block.splitlines():
            cm = re.match(r"\s+(\$\S+)\s+(\d+)", line)
            if cm:
                stats["cell_types"][cm.group(1)] = int(cm.group(2))

    svg_path = svg_prefix + ".svg"
    svg_bytes = None
    if os.path.exists(svg_path):
        with open(svg_path, "rb") as f:
            svg_bytes = f.read()

    netlist_code = None
    if os.path.exists(netlist_file):
        with open(netlist_file) as f:
            netlist_code = f.read()

    return True, result.stdout, stats, svg_bytes, netlist_code

def render_svg_html(svg_bytes, max_height=600):
    """Embed SVG bytes as a base64 <img> inside a scrollable, dark-backed container."""
    b64 = base64.b64encode(svg_bytes).decode()
    return f"""
    <div style="background:#ffffff; border:1px solid #30363D; border-radius:6px;
                padding:12px; overflow:auto; max-height:{max_height}px;">
        <img src="data:image/svg+xml;base64,{b64}" style="max-width:none;" />
    </div>
    """

# ── GALLERY EXPORT ──────────────────────────────────────────────
def export_gallery_html(gallery):
    cards = []
    for entry in gallery:
        bug_html = ""
        if entry.get("bug_stories"):
            stories = "".join(
                f"<div class='bug'><b>Attempt {b['attempt']}:</b> {html_lib.escape(b['explanation'])}</div>"
                for b in entry["bug_stories"]
            )
            bug_html = f"<h4>Bug story</h4>{stories}"
        cards.append(f"""
        <div class="card">
            <h3>{html_lib.escape(entry['module_name'])}</h3>
            <p class="desc">{html_lib.escape(entry['description'])}</p>
            <p class="meta">Verified {html_lib.escape(entry['timestamp'])} · {entry['attempts']} attempt(s)</p>
            {bug_html}
            <h4>Verilog code</h4>
            <pre>{html_lib.escape(entry['code'])}</pre>
            <h4>Simulation output</h4>
            <pre>{html_lib.escape(entry['sim_output'])}</pre>
        </div>
        """)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>RTL Gen — Verified Design Gallery</title>
<style>
body {{ background:#0D1117; color:#E6EDF3; font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin:0; padding:32px; }}
h1 {{ color:#C9A84C; }}
.card {{ background:#161B22; border:1px solid #30363D; border-radius:8px; padding:20px 24px; margin-bottom:24px; }}
.desc {{ color:#90CAF9; }}
.meta {{ color:#8B949E; font-size:12px; }}
pre {{ background:#0D1117; border:1px solid #30363D; border-radius:6px; padding:12px; overflow-x:auto; font-size:12px; }}
.bug {{ background:#2B1A1A; border-left:3px solid #E57373; padding:8px 12px; margin:6px 0; border-radius:4px; font-size:13px; }}
</style></head>
<body>
<h1>⚡ RTL Gen — Verified Design Gallery</h1>
<p class="meta">Sahastra Mudra Semiconductors · exported {html_lib.escape(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))}</p>
{"".join(cards) if cards else "<p>No verified designs yet.</p>"}
</body></html>"""

# ── MAIN INPUT ──────────────────────────────────────────────────
tab_generate, tab_gallery = st.tabs(["🛠️ Generate & Verify", f"🖼️ Verified Gallery ({len(st.session_state['gallery'])})"])

with tab_generate:
    description = st.text_area(
        "Describe the chip / module you want in plain English",
        value=st.session_state["prompt_text"],
        height=100,
        placeholder="e.g. A 4-bit binary counter with synchronous reset and enable input"
    )

    module_name = st.text_input("Module name (must match Verilog module name)", value="my_module")

    col_gen, col_clear = st.columns([1, 5])
    generate_clicked = col_gen.button("⚡ Generate & Verify", type="primary", use_container_width=True)

    if generate_clicked:
        if not api_key:
            st.error("⚠️ Please enter your Gemini API key in the sidebar first.")
            st.stop()
        if not description.strip():
            st.error("⚠️ Please describe the chip you want to generate.")
            st.stop()

        work_dir = tempfile.mkdtemp()
        attempt = 0
        success = False
        current_code = ""
        sim_output = ""
        vcd_path = None
        history = []
        bug_stories = []

        with st.status("Generating and verifying Verilog...", expanded=True) as status:
            st.write("🤖 Asking Gemini to generate initial Verilog code... (auto-retries if server is busy)")
            try:
                prompt = build_generation_prompt(description, module_name)
                raw_response = call_gemini(prompt, api_key)
                current_code = extract_verilog_code(raw_response)
            except Exception as e:
                status.update(label="❌ API call failed", state="error")
                st.error(f"Error calling Gemini API: {e}")
                st.stop()

            history.append({"attempt": 0, "code": current_code, "result": "Generated, testing now...", "explanation": None})

            while attempt < max_retries and not success:
                st.write(f"🔧 Attempt {attempt + 1}: compiling with Icarus Verilog...")
                success, output, functional_pass, this_vcd = compile_and_simulate(current_code, module_name, work_dir)

                if success:
                    sim_output = output
                    vcd_path = this_vcd
                    st.write("✅ Compiled, simulated, AND all self-checks passed!")
                    history[-1]["result"] = "✅ Success — fully verified"
                    break
                else:
                    is_functional = "FUNCTIONAL VERIFICATION FAILURE" in output
                    if is_functional:
                        st.write(f"⚠️ Attempt {attempt + 1}: compiled fine, but self-checks FAILED — asking Gemini to explain and debug...")
                        history[-1]["result"] = f"⚠️ Functional bug: {output[:200]}"
                    else:
                        st.write(f"❌ Attempt {attempt + 1} failed to compile — asking Gemini to explain and fix it...")
                        history[-1]["result"] = f"❌ Compile error: {output[:200]}"

                    explanation = explain_bug(output, description, module_name, api_key)
                    if explanation:
                        history[-1]["explanation"] = explanation
                        bug_stories.append({"attempt": attempt + 1, "explanation": explanation})
                        st.markdown(f"<div class='bug-box'><b>Root cause:</b> {explanation}</div>", unsafe_allow_html=True)

                    fix_prompt = build_fix_prompt(current_code, output, description, module_name)
                    try:
                        raw_response = call_gemini(fix_prompt, api_key)
                        current_code = extract_verilog_code(raw_response)
                        history.append({"attempt": attempt + 1, "code": current_code, "result": "Fixing...", "explanation": None})
                    except Exception as e:
                        st.error(f"Error calling Gemini API during fix attempt: {e}")
                        break
                attempt += 1

            if success:
                status.update(label="✅ Verified working Verilog generated!", state="complete")
            else:
                status.update(label=f"⚠️ Could not get working code after {max_retries} attempts", state="error")

        st.divider()

        if success:
            st.success(f"✅ **FULLY VERIFIED** — Compiled, simulated, and ALL self-check assertions passed after {attempt + 1} attempt(s)")

            if bug_stories:
                with st.expander(f"🩺 Bug story — {len(bug_stories)} issue(s) hit and fixed along the way", expanded=True):
                    for b in bug_stories:
                        st.markdown(f"<div class='bug-box'><b>Attempt {b['attempt']}:</b> {b['explanation']}</div>", unsafe_allow_html=True)

            col1, col2 = st.columns([3, 2])
            with col1:
                st.markdown("#### Generated Verilog Code")
                st.code(current_code, language="verilog", line_numbers=True)
                st.download_button("📥 Download .v file", data=current_code,
                                   file_name=f"{module_name}.v", mime="text/plain")
            with col2:
                st.markdown("#### Simulation Output")
                st.code(sim_output, language=None)
                st.success("All self-check assertions passed")

            st.divider()
            st.markdown("#### 📊 Waveform")
            vcd_bytes = None
            if vcd_path and os.path.exists(vcd_path):
                try:
                    vcd_obj, default_signals, all_signals = load_vcd_signals(vcd_path)
                    with open(vcd_path, "rb") as f:
                        vcd_bytes = f.read()
                    chosen = st.multiselect("Signals to plot", options=all_signals, default=default_signals, key="wave_signals")
                    fig = render_waveform_figure(vcd_obj, chosen)
                    if fig is not None:
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("No signal data to plot — select at least one signal above.")
                    st.download_button("📥 Download .vcd (open in GTKWave for a full-detail view)",
                                        data=vcd_bytes, file_name=f"{module_name}.vcd", mime="text/plain")
                except Exception as e:
                    st.warning(f"Waveform captured but could not be rendered here ({e}). You can still download the raw .vcd below.")
                    try:
                        with open(vcd_path, "rb") as f:
                            vcd_bytes = f.read()
                        st.download_button("📥 Download .vcd", data=vcd_bytes, file_name=f"{module_name}.vcd", mime="text/plain")
                    except Exception:
                        pass
            else:
                st.info("No waveform dump was found for this design — the AI-generated testbench may not have included the `$dumpfile`/`$dumpvars` lines. This doesn't affect the verification result above.")

            st.divider()
            st.markdown("#### 📐 Gate-Level Synthesis")
            st.caption("Synthesized from RTL to a real gate-level netlist using Yosys (open-source synthesis).")
            synth_stats, synth_svg, synth_netlist = None, None, None
            with st.spinner("Running Yosys synthesis..."):
                synth_ok, synth_msg, synth_stats, synth_svg, synth_netlist = synthesize_design(
                    current_code, module_name, work_dir
                )
            if synth_ok:
                if synth_stats and synth_stats.get("cells_total") is not None:
                    scol1, scol2 = st.columns(2)
                    scol1.metric("Total gate-level cells", synth_stats["cells_total"])
                    scol2.metric("Wires", synth_stats["wires"] if synth_stats["wires"] is not None else "—")
                    if synth_stats["cell_types"]:
                        with st.expander("Cell type breakdown"):
                            for cname, ccount in sorted(synth_stats["cell_types"].items(), key=lambda x: -x[1]):
                                st.markdown(f"- `{cname}` × {ccount}")
                if synth_svg:
                    st.markdown("**Schematic**")
                    st.components.v1.html(render_svg_html(synth_svg), height=620, scrolling=True)
                    st.download_button("📥 Download schematic (.svg)", data=synth_svg,
                                        file_name=f"{module_name}_schematic.svg", mime="image/svg+xml")
                if synth_netlist:
                    with st.expander("View synthesized gate-level netlist (Verilog)"):
                        st.code(synth_netlist, language="verilog")
                        st.download_button("📥 Download netlist (.v)", data=synth_netlist,
                                            file_name=f"{module_name}_netlist.v", mime="text/plain")
            else:
                st.warning(f"Synthesis could not be completed for this design: {synth_msg[:300]}")
                st.caption("This doesn't affect the simulation/verification result above — synthesis is an additional, separate check.")

            # Add to session gallery
            already_in_gallery = any(
                g["module_name"] == module_name and g["description"] == description and g["code"] == current_code
                for g in st.session_state["gallery"]
            )
            if not already_in_gallery:
                st.session_state["gallery"].append({
                    "module_name": module_name,
                    "description": description,
                    "code": current_code,
                    "sim_output": sim_output,
                    "attempts": attempt + 1,
                    "bug_stories": bug_stories,
                    "vcd_bytes": vcd_bytes,
                    "synth_stats": synth_stats,
                    "synth_svg": synth_svg,
                    "synth_netlist": synth_netlist,
                    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                })
                st.toast(f"Added '{module_name}' to the Verified Gallery →", icon="🖼️")
        else:
            st.error(f"⚠️ Could not produce fully passing code after {max_retries} attempts.")
            st.caption("This can happen with more complex designs — try increasing 'Max auto-fix attempts' in the sidebar, or simplify the description.")
            if bug_stories:
                with st.expander("🩺 What went wrong on each attempt", expanded=True):
                    for b in bug_stories:
                        st.markdown(f"<div class='bug-box'><b>Attempt {b['attempt']}:</b> {b['explanation']}</div>", unsafe_allow_html=True)
            st.markdown("#### Last attempted code (still has issues)")
            st.code(current_code, language="verilog")

        with st.expander("🔍 View full attempt history"):
            for h in history:
                st.markdown(f"**Attempt {h['attempt']}** — {h['result']}")
                if h.get("explanation"):
                    st.markdown(f"<div class='bug-box'><b>Root cause:</b> {h['explanation']}</div>", unsafe_allow_html=True)
                st.code(h["code"], language="verilog")
                st.divider()

    else:
        st.info("👈 Enter a description, paste your Gemini API key in the sidebar, and click **Generate & Verify** to get started. Try one of the example prompts in the sidebar first.")

with tab_gallery:
    gallery = st.session_state["gallery"]
    st.markdown("#### 🖼️ Verified Design Gallery")
    st.caption("Every design that passed compilation, simulation, AND all self-checks in this session — a running portfolio you can export and share.")

    if not gallery:
        st.info("No verified designs yet. Generate one in the **Generate & Verify** tab and it will show up here automatically.")
    else:
        export_html = export_gallery_html(gallery)
        st.download_button("📤 Export gallery as a shareable HTML page", data=export_html,
                            file_name="rtl_gen_gallery.html", mime="text/html",
                            help="A self-contained HTML file you can host on GitHub Pages, attach to an email, or send to anyone — no login needed to view it.")
        st.divider()

        for i, entry in enumerate(reversed(gallery)):
            with st.container(border=True):
                st.markdown(f"### {entry['module_name']}")
                st.caption(f"{entry['description']}")
                st.caption(f"Verified {entry['timestamp']} · {entry['attempts']} attempt(s)")

                if entry["bug_stories"]:
                    with st.expander(f"🩺 Bug story ({len(entry['bug_stories'])} issue(s) hit and fixed)"):
                        for b in entry["bug_stories"]:
                            st.markdown(f"<div class='bug-box'><b>Attempt {b['attempt']}:</b> {b['explanation']}</div>", unsafe_allow_html=True)

                gcol1, gcol2 = st.columns([3, 2])
                with gcol1:
                    with st.expander("View Verilog code"):
                        st.code(entry["code"], language="verilog")
                with gcol2:
                    with st.expander("View simulation output"):
                        st.code(entry["sim_output"], language=None)

                if entry.get("vcd_bytes"):
                    with st.expander("View waveform"):
                        try:
                            tmp_path = os.path.join(tempfile.mkdtemp(), "gallery.vcd")
                            with open(tmp_path, "wb") as f:
                                f.write(entry["vcd_bytes"])
                            vcd_obj, default_signals, all_signals = load_vcd_signals(tmp_path)
                            chosen = st.multiselect("Signals", options=all_signals, default=default_signals, key=f"gallery_wave_{i}")
                            fig = render_waveform_figure(vcd_obj, chosen)
                            if fig is not None:
                                st.plotly_chart(fig, use_container_width=True, key=f"gallery_chart_{i}")
                        except Exception as e:
                            st.caption(f"Waveform unavailable ({e})")
                        st.download_button("📥 Download .vcd", data=entry["vcd_bytes"],
                                            file_name=f"{entry['module_name']}.vcd", mime="text/plain",
                                            key=f"gallery_vcd_dl_{i}")

                if entry.get("synth_svg"):
                    with st.expander("View gate-level schematic (Yosys synthesis)"):
                        stats = entry.get("synth_stats")
                        if stats and stats.get("cells_total") is not None:
                            st.caption(f"{stats['cells_total']} gate-level cells · {stats.get('wires', '—')} wires")
                        st.components.v1.html(render_svg_html(entry["synth_svg"], max_height=500), height=520, scrolling=True)
                        st.download_button("📥 Download schematic (.svg)", data=entry["synth_svg"],
                                            file_name=f"{entry['module_name']}_schematic.svg", mime="image/svg+xml",
                                            key=f"gallery_svg_dl_{i}")
                        if entry.get("synth_netlist"):
                            st.download_button("📥 Download netlist (.v)", data=entry["synth_netlist"],
                                                file_name=f"{entry['module_name']}_netlist.v", mime="text/plain",
                                                key=f"gallery_netlist_dl_{i}")