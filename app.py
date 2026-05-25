import streamlit as st, time, json, re, unicodedata, io, traceback
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

def parse_pdf(fb, fn):
    """Robust PDF parser handling large files, images, scanned docs, encrypted files."""
    import fitz
    doc=None
    try:
        doc=fitz.open(stream=fb,filetype="pdf")
        if doc.is_encrypted:
            try:doc.authenticate("")
            except:return{"fn":fn,"txt":"","chars":0,"err":"PDF已加密，无法解析"}
        if doc.page_count==0:return{"fn":fn,"txt":"","chars":0,"err":"PDF为空"}

        total_text="";empty_pages=0;has_images=0
        for i,page in enumerate(doc):
            # Extract text with multiple strategies
            text=page.get_text("text")
            if not text.strip():
                # Try blocks extraction for better layout
                blocks=page.get_text("blocks")
                text="\n".join(b[4] for b in blocks if len(b)>4 and b[6]==0)
            if not text.strip():
                # Try dict extraction
                d=page.get_text("dict")
                for block in d.get("blocks",[]):
                    if block.get("type")==0:
                        for line in block.get("lines",[]):
                            for span in line.get("spans",[]):
                                if span.get("text","").strip():
                                    text+=span["text"]+" "
                    elif block.get("type")==1:has_images+=1
            if not text.strip():empty_pages+=1
            total_text+=text+"\n"
        doc.close()

        chars=len(total_text)
        if chars<100 and empty_pages>doc.page_count*0.5:
            return{"fn":fn,"txt":"","chars":0,"err":f"文档主要为图片({has_images}张)，文本仅{chars}字符，建议使用文字版PDF"}
        return{"fn":fn,"txt":total_text,"chars":chars,"pages":doc.page_count}
    except Exception as e:
        try:doc.close()
        except:pass
        return{"fn":fn,"txt":"","chars":0,"err":f"PDF解析失败: {str(e)[:100]}"}

def parse_docx(fb, fn):
    from docx import Document
    try:
        doc=Document(io.BytesIO(fb))
        paras=[p.text for p in doc.paragraphs if p.text.strip()]
        # Also extract tables
        for table in doc.tables:
            for row in table.rows:
                row_text=" | ".join(cell.text for cell in row.cells if cell.text.strip())
                if row_text.strip():paras.append(row_text)
        text="\n\n".join(paras)
        return{"fn":fn,"txt":text,"chars":len(text)}
    except Exception as e:
        return{"fn":fn,"txt":"","chars":0,"err":f"DOCX解析失败: {str(e)[:100]}"}

def parse_html(fb, fn):
    from bs4 import BeautifulSoup
    try:
        soup=BeautifulSoup(fb,"html.parser")
        for tag in soup(["script","style","nav","footer","img","svg"]):tag.decompose()
        text=soup.get_text(separator="\n",strip=True)
        return{"fn":fn,"txt":text,"chars":len(text)}
    except Exception as e:
        return{"fn":fn,"txt":"","chars":0,"err":f"HTML解析失败: {str(e)[:100]}"}

def parse_text(fb, fn):
    for enc in["utf-8","gbk","gb2312","gb18030","latin-1"]:
        try:
            text=fb.decode(enc)
            return{"fn":fn,"txt":text,"chars":len(text)}
        except:continue
    return{"fn":fn,"txt":"","chars":0,"err":"无法识别文件编码"}

def parse_doc(fb, fn):
    ext=Path(fn).suffix.lower()
    if ext==".pdf":return parse_pdf(fb,fn)
    elif ext in(".docx",".doc"):return parse_docx(fb,fn)
    elif ext in(".html",".htm"):return parse_html(fb,fn)
    else:return parse_text(fb,fn)

# Startup
try:
    ds_key=sk("DEEPSEEK_API_KEY");ds_model=sk("DEEPSEEK_MODEL","deepseek-chat")
    gk=sk("GOOGLE_API_KEY");glk=sk("GLM_API_KEY")
    if not ds_key and not gk and not glk:
        st.title("研究报告中判官")
        st.error("未配置 API Key。请在 Settings > Secrets 中添加。")
        st.stop()

    with st.sidebar:
        st.header("模型")
        models=[]
        if ds_key:
            st.checkbox("DeepSeek V4 Pro",value=True,disabled=True,key="mds");models.append(("deepseek",ds_key,ds_model))
        if gk:
            if st.checkbox("Gemini 3.5 Flash",value=False,key="mgg"):models.append(("google",gk,sk("GOOGLE_MODEL","gemini-2.5-flash")))
        if glk:
            if st.checkbox("GLM 5.1 Pro",value=False,key="mglm"):models.append(("glm",glk,sk("GLM_MODEL","glm-4.5")))
        st.caption(f"已选 {len(models)} 个");st.markdown("---")
        st.subheader("联网检索");st.info("DuckDuckGo");st.caption("v3.2")

    st.title("研究报告中判官")
    st.caption("上传文档 → 多模型交叉验证 → 修正结论报告")
    st.markdown("---")
    st.subheader("上传文档")
    up=st.file_uploader("PDF/DOCX/MD/TXT/HTML | 单文件≤50MB | 总计≤200MB",type=["pdf","docx","md","txt","html","htm"],accept_multiple_files=True)
    if up:
        ok=True;tmb=sum(f.size for f in up)/1024/1024
        for f in up:
            if f.size>50*1024*1024:st.error(f"{f.name} 超50MB");ok=False
        if tmb>200:st.error(f"总{tmb:.1f}MB 超限");ok=False
        if ok:st.success(f"{len(up)} 个文件 ({tmb:.1f} MB)")

    mode=st.radio("处理模式",["逐一分析（每篇独立报告）","合并分析（综合结论）"],horizontal=True,key="mode")
    pm="separate" if "逐一" in mode else "merged"
    ci=st.text_area("自定义审校要求（可选）",placeholder="关注第三章方法论\n对比2024年后数据",height=80,key="ci")
    c1,c2=st.columns([3,1])
    with c1:go=st.button("开始审校分析",type="primary",disabled=not up or st.session_state.running,use_container_width=True)
    with c2:
        if st.button("重置",use_container_width=True):reset();st.rerun()
except Exception as e:
    st.error(f"启动失败: {traceback.format_exc()}");st.stop()

if go:
    st.session_state.running=True;st.session_state.done=False
    st.session_state.files=[(f.name,f.read())for f in up]
    st.session_state.results=[];st.session_state.report="";st.session_state.search={}
    st.rerun()

if st.session_state.running and not st.session_state.done:
    st.markdown("---");st.subheader("分析进度")
    pbar=st.progress(0);stat=st.empty();det=st.expander("解析详情",expanded=True)
    try:
        stat.info("解析文档...");pbar.progress(3)
        parsed=[]
        with det:
            for i,(fn,fb) in enumerate(st.session_state.files):
                d=parse_doc(fb,fn);pbar.progress(3+int(7*(i+1)/len(st.session_state.files)))
                if"err"in d:st.warning(f"{fn}: {d['err']}")
                else:parsed.append(d);st.caption(f"{fn} → {d['chars']:,}字符"+(f", {d.get('pages',0)}页" if d.get('pages') else""))
        if not parsed:st.error("所有文档解析失败");st.session_state.running=False;st.stop()
        pbar.progress(12)

        # Combine
        all_text=""
        for i,d in enumerate(parsed):
            txt="".join(c for c in d["txt"] if c.isprintable()or c in"\n\r\t")
            txt=unicodedata.normalize("NFKC",txt)
            all_text+=f"\n\n===== 文档{i+1}: {d['fn']} =====\n\n{txt}"
        combined=all_text
        st.caption(f"共 {len(parsed)} 篇, {len(combined):,} 字符")
        pbar.progress(15)

        # LLM helper
        def call_llm(pid,pk,pm,sys_msg,usr_msg,max_tok=4096):
            if pid in("deepseek","glm"):
                base="https://api.deepseek.com" if pid=="deepseek" else "https://open.bigmodel.cn/api/paas/v4"
                url=f"{base}/chat/completions"
                body={"model":pm,"messages":[{"role":"system","content":sys_msg},{"role":"user","content":usr_msg}],"temperature":0.3,"max_tokens":max_tok}
                h={"Authorization":f"Bearer {pk}","Content-Type":"application/json"}
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

        # Step 1: Extract claims
        stat.info("DeepSeek 提取结论与论据...");pbar.progress(18)
        S1="你是学术审校专家。通读全部文档，提取所有核心结论、分论点、论据。仅输出JSON: {\"conclusions\":[{\"text\":\"...\"}],\"arguments\":[{\"text\":\"...\",\"parent\":\"...\"}],\"evidence\":[{\"text\":\"...\",\"type\":\"数据/引用/推理\",\"supports\":\"...\"}]}"
        U1=f"全部文档：\n\n{combined}"+(f"\n\n用户要求：{ci}" if ci else"")
        claims={}
        try:claims=extract_json(call_llm("deepseek",ds_key,ds_model,S1,U1,max_tok=8192))
        except:
            try:claims=extract_json(call_llm("deepseek",ds_key,ds_model,S1,f"文档前段：\n\n{combined[:5000]}"))
            except:pass
        nc=len(claims.get("conclusions",[]));na=len(claims.get("arguments",[]));ne=len(claims.get("evidence",[]))
        st.caption(f"提取: {nc} 结论, {na} 论点, {ne} 论据")
        pbar.progress(35)

        # Step 2: Search
        stat.info("联网检索...");pbar.progress(45)
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
        st.caption(f"{len(queries)} 次查询, {sum(len(v)for v in sr.values())} 条")
        pbar.progress(55)

        # Step 3: Cross-validate
        stat.info("多模型交叉验证...");pbar.progress(60)
        S2="你是学术交叉验证专家。逐条验证每个结论并给出修正后的最终结论。格式：\n## 结论N\n- 原文\n- 验证分析\n- 修正结论: [经修正后的最终表述]\n全部完成后，附录列出问题清单及严重程度。"
        U2=f"结论：{json.dumps(claims,ensure_ascii=False)[:3000]}\n检索：{stxt}\n要求：{ci if ci else'无'}\n摘要：{combined[:2000]}"
        all_results=[]
        for pid,pk,pm in models:
            stat.info(f"调用 {pid.upper()}...")
            try:
                content=call_llm(pid,pk,pm,S2,U2)
                all_results.append({"p":pid,"m":pm,"c":content,"e":None})
            except Exception as e:all_results.append({"p":pid,"m":pm,"c":"","e":str(e)})
        st.session_state.results=all_results
        pbar.progress(85)

        # Step 4: Report
        stat.info("生成报告...");pbar.progress(90)
        mt="\n".join(f"## {r['p'].upper()}\n{r['c'][:4000]}"for r in all_results)
        S3="整合验证结果生成报告。结构：文档概况→修正后的最终结论→综合评估→附录问题清单。"
        try:report=call_llm("deepseek",ds_key,ds_model,S3,f"{mt}\n检索：{stxt}",max_tok=8192)
        except:report=f"# 审校报告\n\n{mt}"
        st.session_state.report=report
        pbar.progress(100);stat.success("完成")
        st.session_state.done=True;time.sleep(0.3);st.rerun()
    except Exception as e:
        st.error(f"异常:\n{traceback.format_exc()}")
        st.session_state.running=False

if st.session_state.done and st.session_state.report:
    st.markdown("---");st.header("审校结果")
    results=st.session_state.results
    c1,c2,c3=st.columns(3)
    c1.metric("模型",len(results));c2.metric("成功",sum(1 for r in results if not r.get("e")))
    c3.metric("检索",f"{sum(len(v)for v in st.session_state.search.values())} 条")
    t1,t2=st.tabs(["报告","详情"])
    with t1:
        st.markdown('<div class="report-box">',unsafe_allow_html=True)
        st.markdown(st.session_state.report)
        st.markdown('</div>',unsafe_allow_html=True)
        ts=datetime.now().strftime("%Y%m%d_%H%M%S")
        st.download_button("下载报告",data=st.session_state.report.encode(),file_name=f"审校报告_{ts}.md",mime="text/markdown",use_container_width=True)
    with t2:
        for r in results:
            with st.expander(f"{'OK' if not r.get('e') else 'ERR'} {r['p'].upper()}"):
                if r.get("e"):st.error(r["e"])
                else:st.text(r["c"][:4000])
    if st.button("分析新文档",use_container_width=True):reset();st.rerun()
