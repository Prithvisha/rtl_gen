<<<<<<< HEAD
import streamlit as st
from google import genai
import subprocess
import os
import tempfile
import re
import time
import shutil

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
</style>
<div class="header">
    <h1>⚡ RTL Gen &nbsp; <span style="font-size:14px;color:#90CAF9;font-weight:400">AI Verilog Generator + Auto-Verification</span></h1>
    <p>Describe a chip in English → get compiled, simulated Verilog RTL &nbsp;·&nbsp; Sahastra Mudra Semiconductors</p>
</div>
""", unsafe_allow_html=True)

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

# ── MAIN INPUT ──────────────────────────────────────────────────
if "prompt_text" not in st.session_state:
    st.session_state["prompt_text"] = ""

description = st.text_area(
    "Describe the chip / module you want in plain English",
    value=st.session_state["prompt_text"],
    height=100,
    placeholder="e.g. A 4-bit binary counter with synchronous reset and enable input"
)

module_name = st.text_input("Module name (must match Verilog module name)", value="my_module")

col_gen, col_clear = st.columns([1, 5])
generate_clicked = col_gen.button("⚡ Generate & Verify", type="primary", use_container_width=True)

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

Output ONLY the corrected Verilog code (module + testbench) inside a single ```verilog code fence. No explanation text outside the code block.
"""

def compile_and_simulate(verilog_code, module_name, work_dir):
    """Try to compile with iverilog and run with vvp. Returns (success, output_or_error, functional_pass)."""
    v_file = os.path.join(work_dir, f"{module_name}.v")
    vvp_file = os.path.join(work_dir, f"{module_name}.vvp")

    with open(v_file, "w") as f:
        f.write(verilog_code)

    compile_result = subprocess.run(
        [IVERILOG_PATH, "-o", vvp_file, v_file],
        capture_output=True, text=True, timeout=30
    )

    if compile_result.returncode != 0:
        return False, "COMPILATION ERROR:\n" + compile_result.stderr, False

    sim_result = subprocess.run(
        [VVP_PATH, vvp_file],
        capture_output=True, text=True, timeout=30
    )

    if sim_result.returncode != 0:
        return False, "SIMULATION ERROR:\n" + sim_result.stderr, False

    # Compiled and ran cleanly — but did the self-checks actually pass?
    output_upper = sim_result.stdout.upper()
    has_fail = "FAIL" in output_upper
    has_pass = "PASS" in output_upper

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
        return False, functional_report, False

    return True, sim_result.stdout, True

# ── MAIN GENERATION FLOW ────────────────────────────────────────
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
    history = []

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

        history.append({"attempt": 0, "code": current_code, "result": "Generated, testing now..."})

        while attempt < max_retries and not success:
            st.write(f"🔧 Attempt {attempt + 1}: compiling with Icarus Verilog...")
            success, output, functional_pass = compile_and_simulate(current_code, module_name, work_dir)

            if success:
                sim_output = output
                st.write("✅ Compiled, simulated, AND all self-checks passed!")
                history[-1]["result"] = "✅ Success — fully verified"
                break
            else:
                if "FUNCTIONAL VERIFICATION FAILURE" in output:
                    st.write(f"⚠️ Attempt {attempt + 1}: compiled fine, but self-checks FAILED — asking Gemini to debug...")
                    history[-1]["result"] = f"⚠️ Functional bug: {output[:200]}"
                else:
                    st.write(f"❌ Attempt {attempt + 1} failed to compile — asking Gemini to fix it...")
                    history[-1]["result"] = f"❌ Compile error: {output[:200]}"
                fix_prompt = build_fix_prompt(current_code, output, description, module_name)
                try:
                    raw_response = call_gemini(fix_prompt, api_key)
                    current_code = extract_verilog_code(raw_response)
                    history.append({"attempt": attempt + 1, "code": current_code, "result": "Fixing..."})
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
    else:
        st.error(f"⚠️ Could not produce fully passing code after {max_retries} attempts.")
        st.caption("This can happen with more complex designs — try increasing 'Max auto-fix attempts' in the sidebar, or simplify the description.")
        st.markdown("#### Last attempted code (still has issues)")
        st.code(current_code, language="verilog")

    with st.expander("🔍 View full attempt history"):
        for h in history:
            st.markdown(f"**Attempt {h['attempt']}** — {h['result']}")
            st.code(h["code"], language="verilog")
            st.divider()

else:
=======
import streamlit as st
from google import genai
import subprocess
import os
import tempfile
import re
import time
import shutil

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
</style>
<div class="header">
    <h1>⚡ RTL Gen &nbsp; <span style="font-size:14px;color:#90CAF9;font-weight:400">AI Verilog Generator + Auto-Verification</span></h1>
    <p>Describe a chip in English → get compiled, simulated Verilog RTL &nbsp;·&nbsp; Sahastra Mudra Semiconductors</p>
</div>
""", unsafe_allow_html=True)

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

# ── MAIN INPUT ──────────────────────────────────────────────────
if "prompt_text" not in st.session_state:
    st.session_state["prompt_text"] = ""

description = st.text_area(
    "Describe the chip / module you want in plain English",
    value=st.session_state["prompt_text"],
    height=100,
    placeholder="e.g. A 4-bit binary counter with synchronous reset and enable input"
)

module_name = st.text_input("Module name (must match Verilog module name)", value="my_module")

col_gen, col_clear = st.columns([1, 5])
generate_clicked = col_gen.button("⚡ Generate & Verify", type="primary", use_container_width=True)

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

Output ONLY the corrected Verilog code (module + testbench) inside a single ```verilog code fence. No explanation text outside the code block.
"""

def compile_and_simulate(verilog_code, module_name, work_dir):
    """Try to compile with iverilog and run with vvp. Returns (success, output_or_error, functional_pass)."""
    v_file = os.path.join(work_dir, f"{module_name}.v")
    vvp_file = os.path.join(work_dir, f"{module_name}.vvp")

    with open(v_file, "w") as f:
        f.write(verilog_code)

    compile_result = subprocess.run(
        [IVERILOG_PATH, "-o", vvp_file, v_file],
        capture_output=True, text=True, timeout=30
    )

    if compile_result.returncode != 0:
        return False, "COMPILATION ERROR:\n" + compile_result.stderr, False

    sim_result = subprocess.run(
        [VVP_PATH, vvp_file],
        capture_output=True, text=True, timeout=30
    )

    if sim_result.returncode != 0:
        return False, "SIMULATION ERROR:\n" + sim_result.stderr, False

    # Compiled and ran cleanly — but did the self-checks actually pass?
    output_upper = sim_result.stdout.upper()
    has_fail = "FAIL" in output_upper
    has_pass = "PASS" in output_upper

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
        return False, functional_report, False

    return True, sim_result.stdout, True

# ── MAIN GENERATION FLOW ────────────────────────────────────────
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
    history = []

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

        history.append({"attempt": 0, "code": current_code, "result": "Generated, testing now..."})

        while attempt < max_retries and not success:
            st.write(f"🔧 Attempt {attempt + 1}: compiling with Icarus Verilog...")
            success, output, functional_pass = compile_and_simulate(current_code, module_name, work_dir)

            if success:
                sim_output = output
                st.write("✅ Compiled, simulated, AND all self-checks passed!")
                history[-1]["result"] = "✅ Success — fully verified"
                break
            else:
                if "FUNCTIONAL VERIFICATION FAILURE" in output:
                    st.write(f"⚠️ Attempt {attempt + 1}: compiled fine, but self-checks FAILED — asking Gemini to debug...")
                    history[-1]["result"] = f"⚠️ Functional bug: {output[:200]}"
                else:
                    st.write(f"❌ Attempt {attempt + 1} failed to compile — asking Gemini to fix it...")
                    history[-1]["result"] = f"❌ Compile error: {output[:200]}"
                fix_prompt = build_fix_prompt(current_code, output, description, module_name)
                try:
                    raw_response = call_gemini(fix_prompt, api_key)
                    current_code = extract_verilog_code(raw_response)
                    history.append({"attempt": attempt + 1, "code": current_code, "result": "Fixing..."})
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
    else:
        st.error(f"⚠️ Could not produce fully passing code after {max_retries} attempts.")
        st.caption("This can happen with more complex designs — try increasing 'Max auto-fix attempts' in the sidebar, or simplify the description.")
        st.markdown("#### Last attempted code (still has issues)")
        st.code(current_code, language="verilog")

    with st.expander("🔍 View full attempt history"):
        for h in history:
            st.markdown(f"**Attempt {h['attempt']}** — {h['result']}")
            st.code(h["code"], language="verilog")
            st.divider()

else:
>>>>>>> 7b8491613ad9e184b53b59829101f113b32bee0b
    st.info("👈 Enter a description, paste your Gemini API key in the sidebar, and click **Generate & Verify** to get started. Try one of the example prompts in the sidebar first.")