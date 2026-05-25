import concurrent.futures, time, json
from dataclasses import dataclass
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError

@dataclass
class ModelConfig:
    provider: str; model_name: str; api_key: str
    api_base: str = ""; is_fixed: bool = False

@dataclass
class ModelResponse:
    provider: str; model_name: str; content: str
    duration_seconds: float; tokens_used: int = 0; error: Optional[str] = None

class ModelAdapter:
    def __init__(self, configs):
        self.configs = {c.provider: c for c in configs}

    def _post(self, url, body, headers):
        b = json.dumps(body).encode("utf-8")
        return urlopen(Request(url, data=b, headers=headers, method="POST"), timeout=120)

    def _err(self, cfg, start, code, bt):
        return ModelResponse(provider=cfg.provider, model_name=cfg.model_name, content="", duration_seconds=round(time.time()-start,1), error=f"HTTP {code}: {bt[:200]}")

    def _call_ds_oai(self, cfg, s, u):
        start = time.time()
        base = cfg.api_base or "https://api.deepseek.com"
        url = f"{base}/chat/completions"
        body = {"model": cfg.model_name, "messages": [{"role": "system", "content": s}, {"role": "user", "content": u}], "temperature": 0.3, "max_tokens": 4096}
        h = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
        try:
            r = self._post(url, body, h)
            d = json.loads(r.read().decode("utf-8"))
            c = d["choices"][0]["message"]["content"]
            t = d.get("usage", {}).get("total_tokens", 0)
            return ModelResponse(provider=cfg.provider, model_name=cfg.model_name, content=c, duration_seconds=round(time.time()-start,1), tokens_used=t)
        except HTTPError as e:
            be = ""
            try: be = e.read().decode("utf-8")[:200]
            except: pass
            return self._err(cfg, start, e.code, be)
        except Exception as e:
            return ModelResponse(provider=cfg.provider, model_name=cfg.model_name, content="", duration_seconds=round(time.time()-start,1), error=f"{type(e).__name__}: {e}")

    def _call_google(self, cfg, s, u):
        start = time.time()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{cfg.model_name}:generateContent?key={cfg.api_key}"
        body = {"system_instruction": {"parts": [{"text": s}]}, "contents": [{"role": "user", "parts": [{"text": u}]}], "generationConfig": {"temperature": 0.3, "maxOutputTokens": 4096}}
        try:
            r = self._post(url, body, {"Content-Type": "application/json"})
            d = json.loads(r.read().decode("utf-8"))
            c = d["candidates"][0]["content"]["parts"][0]["text"]
            return ModelResponse(provider=cfg.provider, model_name=cfg.model_name, content=c, duration_seconds=round(time.time()-start,1))
        except HTTPError as e:
            be = ""
            try: be = e.read().decode("utf-8")[:200]
            except: pass
            return self._err(cfg, start, e.code, be)
        except Exception as e:
            return ModelResponse(provider=cfg.provider, model_name=cfg.model_name, content="", duration_seconds=round(time.time()-start,1), error=f"{type(e).__name__}: {e}")

    def _call_one(self, cfg, s, u):
        if cfg.provider in ("deepseek", "openai"):
            return self._call_ds_oai(cfg, s, u)
        if cfg.provider == "google":
            return self._call_google(cfg, s, u)
        return ModelResponse(provider=cfg.provider, model_name=cfg.model_name, content="", duration_seconds=0, error=f"Unsupported: {cfg.provider}")

    def call_sync(self, s, u):
        cfgs = list(self.configs.values())
        if len(cfgs) == 1:
            return [self._call_one(cfgs[0], s, u)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(cfgs)) as ex:
            futs = {ex.submit(self._call_one, c, s, u): c for c in cfgs}
            return [f.result() for f in concurrent.futures.as_completed(futs)]
