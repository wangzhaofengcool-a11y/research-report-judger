import streamlit as st, time, json, re, unicodedata, io
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from pathlib import Path

st.set_page_config(page_title="研究报告中判官", page_icon="📋", layout="wide")
st.markdown("""<style>
@media(max-width:768px){button{min-height:44px!important;width:100%!important}}
.report-box{max-height:70vh;overflow-y:auto;padding:1.5rem;border:1px solid #e0e0e0;border-radius:10px;background:#fafbfc;font-size:.92rem;line-height:1.75}
.stButton>button{border-radius:8px}
</style>""", unsafe_allow_html=True)

for k,v in {"running":False,"done":False,"report":"","results":[],"files":[],"search":{}}.items():
    if k not in st.session_state: st.session_state[k]=v
def reset():
    for k in ["running","done","report","results","files","search"]:
        st.session_state[k]=False if k in("running","done")else([]if k in("results","files")else({}if k=="search"else""))
def sk(key,default=""):
    try:return st.secrets.get(key,default)
    except:
        import os;return os.environ.get(key,default)

def parse_doc(fb,fn):
    ext=Path(fn).suffix.lower()
    try:
        if ext==".pdf":
            import fitz;doc=fitz.open(stream=fb,filetype="pdf")
            t="\n\n".join(p.get_text("text")for p in doc);doc.close()
            return{"fn":fn,"txt":t,"chars":len(t),"pages":doc.page_count}
        elif ext in(".docx",".doc"):
            from docx import Document;doc=Document(io.BytesIO(fb))
            t="\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
            return{"fn":fn,"txt":t,"chars":len(t)}
        elif ext in(".html",".htm"):
            from bs4 import BeautifulSoup;soup=BeautifulSoup(fb,"html.parser")
            for tag in soup(["script","style","nav","footer"]):tag.decompose()
            t=soup.get_text(separator="\n",strip=True)
            return{"fn":fn,"txt":t,"chars":len(t)}
        else:
            try:t=fb.decode("utf-8")
            except:
                try:t=fb.decode("gbk")
                except:t=fb.decode("latin-1")
            return{"fn":fn,"txt":t,"chars":len(t)}
    except Exception as e:return{"fn":fn,"txt":"","chars":0,"err":str(e)}

def call_llm(pid,pk,pm,sys_msg,usr_msg,max_tok=4096):
    if pid in("deepseek","glm"):
        base="https://api.deepseek.com" if pid=="deepseek" else "https://open.bigmodel.cn/api/paas/v4"
        body={"model":pm,"messages":[{"role":"system","content":sys_msg},{"role":"user","content":usr_msg}],"temperature":0.3,"max_tokens":max_tok}
        h={"Authorization":f"Bearer {pk}","Content-Type":"application/json"}
        url=f"{base}/chat/completions"
    else:
        url=f"https://generativelanguage.googleapis.com/v1beta/models/{pm}:generateContent?key={pk}"
        body={"system_instruction":{"parts":[{"text":sys_msg}]},"contents":[{"role":"user","parts":[{"text":usr_msg}]}],"generationConfig":{"temperature":0.3,"maxOutputTokens":max_tok}}
        r=urlopen(Request(url,data=json.dumps(body).encode(),headers={"Content-Type":"application/json"},method="POST"),timeout=300)
        return json.loads(r.read().decode())["candidates"][0]["content"]["parts"][0]["text"]
    r=urlopen(Request(url,data=json.dumps(body).encode(),headers=h,method="POST"),timeout=300)
    return json.loads(r.read().decode())["choices"][0]["message"]["content"]

def search_web(q,n=3):
    try:
        r=urlopen(Request(f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"),timeout=10)
        d=json.loads(r.read().decode());out=[]
        for t in d.get("RelatedTopics",[])[:n]:
            if isinstance(t,dict)and"Text"in t:out.append({"t":t.get("FirstURL",""),"s":t.get("Text","")[:200]})
        if d.get("AbstractText"):out.append({"t":d.get("AbstractSource",""),"s":d.get("AbstractText","")[:200]})
        return out
    except:return[]

def extract_json(txt):
    try:return json.loads(txt)
    except:pass
    m=re.search(r'```(?:json)?\s*([\s\S]*?)```',txt)
    if m:
        try:return json.loads(m.group(1))
        except:pass
    m=re.search(r'\{[\s\S]*\}',txt)
    if m:
        try:return json.loads(m.group(0))
        except:pass
    return{}

# Load models
ds_key=sk("DEEPSEEK_API_KEY");ds_model=sk("DEEPSEEK_MODEL","deepseek-chat")
gk=sk("GOOGLE_API_KEY");glk=sk("GLM_API_KEY")
if not ds_key and not gk and not glk:
    st.title("研究报告中判官")
    st.error("未配置 API Key。请在 Settings → Secrets 中添加。")
    st.stop()

# Sidebar
with st.sidebar:
    st.header("模型")
    models=[]
    if ds_key:
        st.checkbox("DeepSeek V4 Pro",value=True,disabled=True,key="mds");models.append(("deepseek",ds_key,ds_model))
    if gk:
        if st.checkbox("Gemini 3.5 Flash",value=False,key="mgg"):models.append(("google",gk,sk("GOOGLE_MODEL","gemini-2.5-flash")))
    if glk:
        if st.checkbox("GLM 5.1 Pro",value=False,key="mglm"):models.append(("glm",glk,sk("GLM_MODEL","glm-4.5")))
    st.caption(f"已选 {len(models)} 个")
    st.markdown("---")
    st.subheader("联网检索")
    st.info("DuckDuckGo 实时搜索")
    st.caption("v3.2")

# Main
st.title("研究报告中判官")
st.caption("上传文档 → 多模型交叉验证 → 下载修正结论报告")
st.markdown("---")
st.subheader("上传文档")
up=st.file_uploader("PDF/DOCX/MD/TXT/HTML | ≤50MB | 最多20个",type=["pdf","docx","md","txt","html","htm"],accept_multiple_files=True)
if up:
    ok=True;tmb=sum(f.size for f in up)/1024/1024
    for f in up:
        if f.size>50*1024*1024:st.error(f"{f.name} 超过50MB");ok=False
    if tmb>200:st.error(f"总计 {tmb:.1f}MB 超限");ok=False
    if ok:st.success(f"已选择 {len(up)} 个文件 ({tmb:.1f} MB)")
st.subheader("处理模式")
mode=st.radio("多文档处理方式",["逐一分析（每篇独立报告）","合并分析（综合结论）"],horizontal=True,key="mode")
pm="separate" if "逐一" in mode else "merged"
st.subheader("自定义审校要求（可选）")
ci=st.text_area("额外审校方向",placeholder="关注第三章方法论\n对比2024年后数据",height=80,key="ci")
c1,c2=st.columns([3,1])
with c1:go=st.button("开始审校分析",type="primary",disabled=not up or st.session_state.running,use_container_width=True)
with c2:
    if st.button("重置",use_container_width=True):reset();st.rerun()

if go:
    st.session_state.running=True;st.session_state.done=False
    st.session_state.files=[(f.name,f.read())for f in up]
    st.session_state.results=[];st.session_state.report="";st.session_state.search={}
    st.rerun()

if st.session_state.running and not st.session_state.done:
    st.markdown("---");st.subheader("分析进度")
    pbar=st.progress(0);stat=st.empty()
    try:
        stat.info("解析文档...");pbar.progress(5)
        parsed=[]
        for fn,fb in st.session_state.files:
            d=parse_doc(fb,fn)
            if"err"not in d:parsed.append(d)
        if not parsed:st.error("解析失败");st.session_state.running=False;st.stop()
        pbar.progress(10)
        all_docs_text=""
        for i,d in enumerate(parsed):
            txt="".join(c for c in d["txt"] if c.isprintable()or c in"\n\r\t")
            txt=unicodedata.normalize("NFKC",txt)
            if pm=="merged":all_docs_text+=f"\n\n===== 文档{i+1}: {d['fn']} =====\n\n{txt}"
            else:all_docs_text+=f"\n\n===== 文档{i+1}: {d['fn']} =====\n\n{txt}"
        combined=all_docs_text
        st.caption(f"共 {len(parsed)} 篇文档, {len(combined):,} 字符")

        # Step 1: Extract claims
        stat.info("DeepSeek 提取结论与论据...");pbar.progress(15)
        SYS1="你是学术审校专家。阅读全部文档，提取所有核心结论、分论点、支撑论据。仅输出JSON: {\"conclusions\":[{\"text\":\"...\",\"source\":\"文档名\"}],\"arguments\":[{\"text\":\"...\",\"parent\":\"...\"}],\"evidence\":[{\"text\":\"...\",\"type\":\"数据/引用/推理\",\"supports\":\"...\"}]}"
        USR1=f"全部文档：\n\n{combined}"
        if ci:USR1+=f"\n\n用户要求：{ci}"
        claims={}
        try:
            raw=call_llm("deepseek",ds_key,ds_model,SYS1,USR1,max_tok=8192)
            claims=extract_json(raw)
        except:
            try:claims=extract_json(call_llm("deepseek",ds_key,ds_model,SYS1,f"文档前段：\n\n{combined[:5000]}"))
            except:pass
        nc=len(claims.get("conclusions",[]));na=len(claims.get("arguments",[]));ne=len(claims.get("evidence",[]))
        st.caption(f"提取: {nc} 结论, {na} 论点, {ne} 论据")
        pbar.progress(35)

        # Step 2: Web search
        stat.info("联网检索中...");pbar.progress(45)
        queries=[]
        for c in claims.get("conclusions",[])[:5]:
            t=c.get("text","")
            if len(t)>20:queries.append(t[:100])
        for e in claims.get("evidence",[])[:3]:
            if e.get("type")in("数据","引用"):
                t=e.get("text","")
                if len(t)>20:queries.append(t[:100])
        if ci:queries.append(ci[:100])
        queries=queries[:5]
        sr={};stxt=""
        for q in queries:
            r=search_web(q)
            if r:sr[q]=r
            for it in r:stxt+=f"\n[检索「{q}」] {it['s'][:150]}"
        st.session_state.search=sr
        sc=sum(len(v)for v in sr.values())
        st.caption(f"检索完成: {len(queries)} 次查询, {sc} 条结果")
        pbar.progress(55)

        # Step 3: Cross-validate
        stat.info("多模型交叉验证...");pbar.progress(60)
        SYS2="""你是学术交叉验证专家。逐条审校每个结论，结合联网检索结果进行验证。

格式：
## 结论N：[简述]
- 原文：[引用]
- 交叉验证分析：[结合检索结果验证]
- 验证结论：[经修正后的最终结论表述]

全部审校完成后，以「## 附录：问题清单」列出全部发现问题及严重程度。"""
        USR2=f"待验证结论：\n{json.dumps(claims,ensure_ascii=False)[:3000]}\n\n联网检索结果：{stxt}\n\n用户要求：{ci if ci else'无'}\n\n文档摘要：{combined[:2000]}"
        all_results=[]
        for pid,pk,pm in models:
            stat.info(f"调用 {pid.upper()}...")
            try:
                content=call_llm(pid,pk,pm,SYS2,USR2)
                all_results.append({"p":pid,"m":pm,"c":content,"e":None})
            except Exception as e:
                all_results.append({"p":pid,"m":pm,"c":"","e":str(e)})
        st.session_state.results=all_results
        pbar.progress(85)

        # Step 4: Final report - summarize corrected conclusions
        stat.info("生成最终报告...");pbar.progress(90)
        mt="\n".join(f"## {r['p'].upper()} 验证结果\n{r['c'][:4000]}"for r in all_results)
        SYS3="""整合多模型交叉验证结果，生成最终审校报告。

结构要求：
# 审校报告
## 一、文档概况
## 二、交叉验证后的修正结论
（逐条列出经过验证和修正后的最终结论，而非修改建议。每条结论标注验证状态）
## 三、综合评估
## 附录：问题清单
（逐条列出所有发现的问题、严重程度、参考来源）"""
        try:report=call_llm("deepseek",ds_key,ds_model,SYS3,f"{mt}\n\n检索：{stxt}",max_tok=8192)
        except:report=f"# 审校报告\n\n{mt}"
        st.session_state.report=report
        pbar.progress(100);stat.success("完成")
        st.session_state.done=True;time.sleep(0.3);st.rerun()
    except Exception as e:
        st.error(f"异常: {type(e).__name__}: {e}")
        st.session_state.running=False

if st.session_state.done and st.session_state.report:
    st.markdown("---");st.header("审校结果")
    results=st.session_state.results
    m1,m2,m3=st.columns(3)
    m1.metric("模型",len(results));m2.metric("成功",sum(1 for r in results if not r.get("e")))
    sc=sum(len(v)for v in st.session_state.search.values())
    m3.metric("检索结果",f"{sc} 条")
    t1,t2=st.tabs(["报告","详情"])
    with t1:
        if st.session_state.report:
            st.markdown('<div class="report-box">',unsafe_allow_html=True)
            st.markdown(st.session_state.report)
            st.markdown('</div>',unsafe_allow_html=True)
            ts=datetime.now().strftime("%Y%m%d_%H%M%S")
            st.download_button("下载报告",data=st.session_state.report.encode(),file_name=f"审校报告_{ts}.md",mime="text/markdown",use_container_width=True)
    with t2:
        for r in results:
            ic="OK"if not r.get("e")else"ERR"
            with st.expander(f"{ic} {r['p'].upper()}"):
                if r.get("e"):st.error(r["e"])
                else:st.text(r["c"][:4000])
        if st.session_state.search:
            with st.expander("检索详情"):
                for q,items in st.session_state.search.items():
                    st.caption(f"搜索: {q}")
                    for it in items:st.caption(it["s"][:150])
    if st.button("分析新文档",use_container_width=True):reset();st.rerun()
