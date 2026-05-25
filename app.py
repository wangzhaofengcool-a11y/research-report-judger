import streamlit as st, time, json, re, unicodedata
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError

st.set_page_config(page_title="研究报告中判官", page_icon="📋", layout="wide", initial_sidebar_state="expanded")
st.markdown("""<style>
@media(max-width:768px){button{min-height:44px!important;width:100%!important}}
.report-box{max-height:70vh;overflow-y:auto;padding:1.5rem;border:1px solid #e0e0e0;border-radius:10px;background:#fafbfc;font-size:.92rem;line-height:1.75}
.stButton>button{border-radius:8px}
</style>""", unsafe_allow_html=True)

for k,v in {"running":False,"done":False,"report":"","results":[],"files":[],"search":{}}.items():
    if k not in st.session_state: st.session_state[k] = v

def reset():
    for k in ["running","done","report","results","files","search"]:
        st.session_state[k] = False if k in ("running","done") else ([] if k in ("results","files") else ({} if k=="search" else ""))

def sk(key, default=""):
    try: return st.secrets.get(key, default)
    except:
        import os; return os.environ.get(key, default)

# ── Sidebar ──
with st.sidebar:
    st.header("⚙️ 模型配置")

    ds_key = sk("DEEPSEEK_API_KEY")
    ds_model = sk("DEEPSEEK_MODEL", "deepseek-chat")
    if not ds_key:
        st.error("请配置 DEEPSEEK_API_KEY")
        st.stop()

    st.checkbox(f"🔒 DeepSeek V4 Pro", value=True, disabled=True, key="m_ds")
    models = [("deepseek", ds_key, ds_model)]

    gk = sk("GOOGLE_API_KEY")
    if gk:
        if st.checkbox("Gemini 3.5 Flash", value=False, key="m_gg"):
            models.append(("google", gk, sk("GOOGLE_MODEL", "gemini-2.5-flash")))

    glk = sk("GLM_API_KEY")
    if glk:
        if st.checkbox("GLM 5.1 Pro", value=False, key="m_glm"):
            models.append(("glm", glk, sk("GLM_MODEL", "glm-4.5")))

    st.caption(f"已选 {len(models)} 个模型参与审校")

    st.markdown("---")
    st.subheader("🌐 联网检索")
    gsk = sk("GOOGLE_SEARCH_API_KEY"); gsc = sk("GOOGLE_SEARCH_CSE_ID")
    bsk = sk("BING_SEARCH_API_KEY")
    if gsk and gsc: st.success("Google Search")
    if bsk: st.success("Bing Search")
    if not (gsk and gsc) and not bsk: st.info("DuckDuckGo（免费）")

    st.markdown("---")
    with st.expander("📖 配置说明"):
        st.markdown("编辑 `.streamlit/secrets.toml` 填入各模型 API Key")
    st.caption("v3.2")

# ── API Helpers ──
def call_llm(pid, pk, pm, sys_msg, usr_msg, max_tok=4096):
    if pid in ("deepseek", "glm"):
        base = "https://api.deepseek.com" if pid == "deepseek" else "https://open.bigmodel.cn/api/paas/v4"
        body = {"model": pm, "messages": [{"role": "system", "content": sys_msg}, {"role": "user", "content": usr_msg}], "temperature": 0.3, "max_tokens": max_tok}
        h = {"Authorization": f"Bearer {pk}", "Content-Type": "application/json"}
    else:  # google
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{pm}:generateContent?key={pk}"
        body = {"system_instruction": {"parts": [{"text": sys_msg}]}, "contents": [{"role": "user", "parts": [{"text": usr_msg}]}], "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tok}}
        r = urlopen(Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST"), timeout=300)
        return json.loads(r.read().decode())["candidates"][0]["content"]["parts"][0]["text"]
    url = f"{base}/chat/completions"
    r = urlopen(Request(url, data=json.dumps(body).encode(), headers=h, method="POST"), timeout=300)
    return json.loads(r.read().decode())["choices"][0]["message"]["content"]

def search_web(query, n=3):
    try:
        r = urlopen(Request(f"https://api.duckduckgo.com/?q={query}&format=json&no_html=1&skip_disambig=1"), timeout=10)
        d = json.loads(r.read().decode())
        out = []
        for t in d.get("RelatedTopics", [])[:n]:
            if isinstance(t, dict) and "Text" in t:
                out.append({"t": t.get("FirstURL", ""), "s": t.get("Text", "")[:200]})
        if d.get("AbstractText"):
            out.append({"t": d.get("AbstractSource", ""), "s": d.get("AbstractText", "")[:200]})
        return out
    except:
        return []

def extract_json(txt):
    try: return json.loads(txt)
    except: pass
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', txt)
    if m:
        try: return json.loads(m.group(1))
        except: pass
    m = re.search(r'\{[\s\S]*\}', txt)
    if m:
        try: return json.loads(m.group(0))
        except: pass
    return {}

# ── Main Page ──
st.title("📋 研究报告中判官")
st.caption("上传研究文档 → 多模型交叉验证结论 → 下载审校报告")

st.markdown("---")
st.subheader("📤 上传文档")
up = st.file_uploader("PDF / DOCX / MD / TXT / HTML", type=["pdf", "docx", "md", "txt", "html", "htm"], accept_multiple_files=True)
if up:
    ok = True
    tmb = sum(f.size for f in up) / 1024 / 1024
    for f in up:
        if f.size > 50 * 1024 * 1024:
            st.error(f"❌ {f.name} 超过 50MB"); ok = False
    if tmb > 200: st.error(f"❌ 总计 {tmb:.1f}MB 超限"); ok = False
    if ok: st.success(f"已选择 {len(up)} 个文件（{tmb:.1f} MB）")

st.subheader("✏️ 自定义审校要求（可选）")
ci = st.text_area("输入额外审校方向，AI 将综合原始目的和你的要求完成分析", placeholder="例如：关注第三章方法论\n对比 2024 年后行业数据", height=80, key="ci")

st.markdown("---")
c1, c2 = st.columns([3, 1])
with c1:
    go = st.button("🔍 开始审校分析", type="primary", disabled=not up or st.session_state.running, use_container_width=True)
with c2:
    if st.button("🔄 重置", use_container_width=True): reset(); st.rerun()

if go:
    st.session_state.running = True; st.session_state.done = False
    st.session_state.files = [(f.name, f.read()) for f in up]
    st.session_state.results = []; st.session_state.report = ""; st.session_state.search = {}
    st.rerun()

# ── Analysis Pipeline ──
if st.session_state.running and not st.session_state.done:
    st.markdown("---"); st.subheader("🔍 分析进度")
    pbar = st.progress(0); stat = st.empty()

    try:
        from modules.document_parser import parse_document

        # Parse
        stat.info("📄 解析文档中…"); pbar.progress(5)
        parsed = []
        for fn, fb in st.session_state.files:
            d = parse_document(fb, fn)
            if not d.error: parsed.append(d)
        if not parsed: st.error("文档解析失败"); st.session_state.running = False; st.stop()
        pbar.progress(10)

        combined = parsed[0].text
        combined = "".join(c for c in combined if c.isprintable() or c in "\n\r\t")
        combined = unicodedata.normalize("NFKC", combined)
        st.caption(f"文档共 {len(combined):,} 字符")

        # Step 1: DeepSeek extracts claims from full document (1M context)
        stat.info("🧠 DeepSeek 提取结论与论据（完整文档）…"); pbar.progress(15)
        SYS1 = "你是学术审校专家。通读完整文档，提取所有核心结论、分论点和支撑论据。仅输出JSON: {\"conclusions\":[{\"text\":\"结论原文\"}],\"arguments\":[{\"text\":\"分论点\",\"parent\":\"所属结论\"}],\"evidence\":[{\"text\":\"论据\",\"type\":\"数据/引用/推理\",\"supports\":\"支撑的论点\"}]}"
        USR1 = f"完整文档内容：\n\n{combined}"
        if ci: USR1 += f"\n\n用户额外要求：{ci}"

        claims = {}
        try:
            claims_raw = call_llm("deepseek", ds_key, ds_model, SYS1, USR1, max_tok=8192)
            claims = extract_json(claims_raw)
        except Exception as e:
            st.warning(f"完整文档分析失败，降级为前段分析")
            try:
                claims_raw = call_llm("deepseek", ds_key, ds_model, SYS1, f"文档前段：\n\n{combined[:5000]}", max_tok=4096)
                claims = extract_json(claims_raw)
            except: pass

        nc = len(claims.get("conclusions", []))
        na = len(claims.get("arguments", []))
        ne = len(claims.get("evidence", []))
        st.caption(f"✓ 提取到 {nc} 条核心结论、{na} 条分论点、{ne} 条论据")
        pbar.progress(35)

        # Step 2: Web search
        stat.info("🌐 联网检索中…"); pbar.progress(45)
        queries = []
        for c in claims.get("conclusions", [])[:5]:
            t = c.get("text", "")
            if len(t) > 20: queries.append(t[:100])
        if ci: queries.append(ci[:100])
        queries = queries[:3]

        sr = {}
        for q in queries:
            r = search_web(q)
            if r: sr[q] = r
        st.session_state.search = sr

        search_txt = ""
        for q, items in sr.items():
            search_txt += f"\n搜索「{q}」:\n"
            for it in items: search_txt += f"- {it['s'][:150]}\n"
        pbar.progress(55)

        # Step 3: Multi-model cross-validation
        stat.info("🔍 多模型交叉验证中…"); pbar.progress(60)
        claims_json = json.dumps(claims, ensure_ascii=False)[:3000]
        SYS2 = """你是学术交叉验证专家。逐条审校每个核心结论：

格式要求：
## 结论N：[结论简述]
- 原文：引用原文相关段落
- 交叉验证分析：(结合联网检索结果，给出你的判断)
- 综合判断：✅可信 / ⚠️部分存疑 / ❌建议修正
- 修改建议：(如有)

全部结论验证完毕后，用「## 附录：问题清单」列出所有发现的问题（逻辑漏洞、事实错误、论据不足、来源可疑），每条标注严重程度。"""
        USR2 = f"文档结论：\n{claims_json}\n\n联网检索结果：{search_txt}\n\n用户要求：{ci if ci else '无'}\n\n文档摘要：{combined[:2000]}"

        all_results = []
        for idx, (pid, pk, pm) in enumerate(models):
            stat.info(f"调用 {pid.upper()}（{idx+1}/{len(models)}）…")
            try:
                content = call_llm(pid, pk, pm, SYS2, USR2)
                all_results.append({"p": pid, "m": pm, "c": content, "e": None})
            except Exception as e:
                all_results.append({"p": pid, "m": pm, "c": "", "e": str(e)})
        st.session_state.results = all_results
        pbar.progress(85)

        # Step 4: Synthesize final report
        stat.info("📝 生成最终审校报告…"); pbar.progress(90)
        model_text = ""
        for mr in all_results:
            model_text += f"\n## {mr['p'].upper()} 验证结果\n{mr['c'][:4000]}\n"

        SYS3 = """你是学术报告撰写专家。整合多模型交叉验证结果，生成最终Markdown审校报告。

结构：
# 审校报告
## 一、文档概况（简要）
## 二、核心结论交叉验证（逐条展示：原文→各模型验证→最终判断→修正建议）
## 三、综合评估（整体可信度、主要发现、需关注重点）
## 附录：问题清单（逐条列出：位置、描述、严重程度、参考来源）"""
        USR3 = f"交叉验证结果：\n{model_text}\n\n联网检索：{search_txt}\n\n用户要求：{ci if ci else '无'}"
        try:
            report = call_llm("deepseek", ds_key, ds_model, SYS3, USR3, max_tok=8192)
        except:
            report = f"# 审校报告\n\n{model_text}"

        st.session_state.report = report
        pbar.progress(100); stat.success("✅ 分析完成")
        st.session_state.done = True; time.sleep(0.3); st.rerun()

    except Exception as e:
        st.error(f"流程异常: {type(e).__name__}: {e}")
        st.session_state.running = False

# ── Results ──
if st.session_state.done and st.session_state.report:
    st.markdown("---"); st.header("📊 审校结果")
    results = st.session_state.results
    m1, m2, m3 = st.columns(3)
    m1.metric("参与模型", len(results))
    m2.metric("成功返回", sum(1 for r in results if not r.get("e")))
    m3.metric("检索结果", f"{sum(len(v) for v in st.session_state.search.values())} 条")

    t1, t2 = st.tabs(["📋 审校报告", "🔍 模型详情"])
    with t1:
        if st.session_state.report:
            st.markdown('<div class="report-box">', unsafe_allow_html=True)
            st.markdown(st.session_state.report)
            st.markdown('</div>', unsafe_allow_html=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            st.download_button("📥 下载 Markdown 报告", data=st.session_state.report.encode(), file_name=f"审校报告_{ts}.md", mime="text/markdown", use_container_width=True)
        else:
            st.info("报告生成失败")
    with t2:
        for r in results:
            ic = "✅" if not r.get("e") else "❌"
            with st.expander(f"{ic} {r['p'].upper()} / {r['m']}"):
                if r.get("e"): st.error(r["e"])
                else: st.text(r["c"][:4000])
        if st.session_state.search:
            with st.expander("🌐 检索结果"):
                for q, items in st.session_state.search.items():
                    st.caption(f"搜索: {q}")
                    for it in items: st.caption(it["s"][:120])

    st.markdown("---")
    if st.button("📤 分析新文档", use_container_width=True): reset(); st.rerun()
