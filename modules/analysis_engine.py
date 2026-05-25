"""
AnalysisEngine — orchestrates the full analysis pipeline:
  1. Claim extraction
  2. Web search for fact-checking
  3. Multi-model parallel analysis
  4. Cross-validation
  5. Final report generation
"""

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from modules.model_adapter import ModelAdapter, ModelResponse
from modules.prompts import (
    EXTRACT_CLAIMS_PROMPT,
    ANALYZE_CLAIM_PROMPT,
    CROSS_VALIDATE_PROMPT,
    FINAL_REPORT_PROMPT,
)
from modules.web_search import WebSearcher, SearchResult


@dataclass
class AnalysisResult:
    """Complete result of an analysis run."""
    documents_text: str
    custom_instructions: str
    claims: dict = field(default_factory=dict)
    model_results: list[ModelResponse] = field(default_factory=list)
    search_results: dict = field(default_factory=dict)
    cross_validation: str = ""
    final_report: str = ""
    errors: list[str] = field(default_factory=list)
    quota_alerts: list[str] = field(default_factory=list)


class AnalysisEngine:
    """Orchestrates the full analysis pipeline."""

    def __init__(self, adapter: ModelAdapter, searcher: WebSearcher):
        self.adapter = adapter
        self.searcher = searcher

    def _extract_json(self, text: str) -> dict:
        """Extract JSON from model response that might be wrapped in markdown."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        brace_match = re.search(r'\{[\s\S]*\}', text)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        return {}

    def _generate_search_queries(self, claims: dict, custom_instructions: str) -> list[str]:
        """Generate search queries from claims and custom instructions."""
        queries = []

        for cc in claims.get("core_conclusions", []):
            stmt = cc.get("statement", "")
            if len(stmt) > 30:
                queries.append(f"fact check: {stmt[:150]}")

        for ev in claims.get("evidence", []):
            if ev.get("type") in ("数据", "引用"):
                stmt = ev.get("statement", "")
                if len(stmt) > 20:
                    queries.append(stmt[:200])

        if custom_instructions and len(custom_instructions) > 5:
            queries.append(f"{custom_instructions[:200]}")

        return queries[:5]

    def extract_claims(self, document_text: str) -> dict:
        """Extract conclusions, arguments, and evidence from document text."""
        # Sanitize: remove non-printable chars that may break JSON serialization
        document_text = "".join(c for c in document_text if c.isprintable() or c in "\n\r\t")
        document_text = unicodedata.normalize("NFKC", document_text)

        max_chars = 10000
        if len(document_text) > max_chars:
            document_text = document_text[:max_chars] + "\n\n[文档过长，已截取前段分析]"

        user_msg = f"以下是需要分析的文档内容：\n\n{document_text}"

        deepseek_configs = [c for c in self.adapter.configs.values() if c.provider == "deepseek"]
        if not deepseek_configs:
            responses = self.adapter.call_sync(EXTRACT_CLAIMS_PROMPT, user_msg)
        else:
            single_adapter = ModelAdapter(deepseek_configs)
            responses = single_adapter.call_sync(EXTRACT_CLAIMS_PROMPT, user_msg)

        if responses and not responses[0].error:
            return self._extract_json(responses[0].content)

        return {}

    def analyze_claims(self, claims: dict, document_text: str,
                       custom_instructions: str,
                       progress_callback=None) -> AnalysisResult:
        """Run the full multi-model analysis pipeline."""
        result = AnalysisResult(
            documents_text=document_text,
            custom_instructions=custom_instructions,
            claims=claims,
        )

        if progress_callback:
            progress_callback("联网检索中...")

        queries = self._generate_search_queries(claims, custom_instructions)
        if queries:
            search_results = self.searcher.search(queries)
            result.search_results = search_results
        else:
            result.search_results = {}

        search_text = self.searcher.format_results(result.search_results)

        context_summary = document_text[:3000]

        claims_text = json.dumps(claims, ensure_ascii=False, indent=2)
        claims_for_prompt = claims_text[:10000]

        custom_instr = custom_instructions if custom_instructions else "（无额外要求，按默认审校标准执行）"

        analyze_prompt = ANALYZE_CLAIM_PROMPT.format(
            context_summary=context_summary,
            claim_text=claims_for_prompt,
            search_results=search_text if search_text else "（未执行联网检索）",
            custom_instructions=custom_instr,
        )

        if progress_callback:
            progress_callback("多模型并行分析中...")

        system_prompt = "你是一位严谨的学术审校专家。请严格按照JSON格式输出分析结果。"
        responses = self.adapter.call_sync(system_prompt, analyze_prompt)
        result.model_results = responses

        for resp in responses:
            if resp.error:
                result.errors.append(f"{resp.provider}/{resp.model_name}: {resp.error}")
                result.quota_alerts.append(f"{resp.provider}/{resp.model_name} 调用失败: {resp.error}")

        successful = [r for r in responses if not r.error and r.content]
        if len(successful) >= 2:
            if progress_callback:
                progress_callback("交叉验证中...")

            model_summaries = []
            for r in successful:
                model_summaries.append(f"## {r.provider} / {r.model_name}\n\n{r.content[:8000]}")

            cv_prompt = CROSS_VALIDATE_PROMPT.format(
                model_results="\n\n---\n\n".join(model_summaries)
            )
            cv_system = "你是一位公正的学术仲裁专家。请严格按照JSON格式输出交叉验证结果。"

            cv_adapter = self.adapter
            deepseek = [c for c in self.adapter.configs.values() if c.provider == "deepseek"]
            if deepseek:
                cv_adapter = ModelAdapter(deepseek)

            cv_responses = cv_adapter.call_sync(cv_system, cv_prompt)
            if cv_responses and cv_responses[0].content:
                result.cross_validation = cv_responses[0].content
        elif len(successful) == 1:
            result.cross_validation = json.dumps({
                "note": "仅有一个模型成功返回结果，无法进行交叉验证",
                "model": successful[0].provider,
            }, ensure_ascii=False)

        if progress_callback:
            progress_callback("生成矫正报告...")

        analysis_text = "\n\n".join([
            f"## {r.provider}/{r.model_name}\n{r.content[:6000]}"
            for r in successful
        ])

        cv_text = result.cross_validation if result.cross_validation else "（未执行交叉验证）"

        report_prompt = FINAL_REPORT_PROMPT.format(
            analysis_results=analysis_text,
            cross_validation=cv_text,
        )

        report_responses = self.adapter.call_sync(
            "你是一位专业的学术报告撰写专家。请直接输出Markdown格式报告。",
            report_prompt
        )

        if report_responses and report_responses[0].content and not report_responses[0].error:
            report = report_responses[0].content
            report = re.sub(r'^```markdown\s*', '', report)
            report = re.sub(r'^```\s*', '', report)
            report = re.sub(r'\s*```$', '', report)
            result.final_report = report
        else:
            result.final_report = self._build_fallback_report(result)

        return result

    def _build_fallback_report(self, result: AnalysisResult) -> str:
        """Build a basic report when the AI report generation fails."""
        lines = ["# 研究报告审校报告\n"]
        lines.append("## 一、文档概况\n")
        lines.append(f"文档总字数：{len(result.documents_text)} 字符\n")

        lines.append("## 二、各模型分析结果\n")
        for resp in result.model_results:
            status = "OK" if not resp.error else f"失败: {resp.error}"
            lines.append(f"### {resp.provider} / {resp.model_name} ({status})\n")
            if resp.content:
                lines.append(resp.content[:5000])
            lines.append("")

  