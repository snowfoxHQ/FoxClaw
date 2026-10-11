from dotenv import load_dotenv
import os

p = r"C:\Users\snow\projects\hestiaOS\.env"
try:
    load_dotenv(p)
    print("DOTENV_OK")
except Exception as e:
    print(f"DOTENV_FAIL: {type(e).__name__}: {e}")
    raise SystemExit(1)

for k in ("HKC_EMBEDDING_MODEL", "HF_HUB_OFFLINE", "HESTIA_API_KEY",
          "HESTIA_PROVIDER", "HESTIA_DATA_DIR", "HESTIA_MCP_SERVER",
          "HESTIA_MODEL", "HKC_LLM_PROVIDER", "HKC_LLM_MODEL"):
    v = os.environ.get(k, "<UNSET>")
    if k.endswith("API_KEY") and v not in ("<UNSET>", ""):
        v = v[:8] + "..." + v[-4:] if len(v) > 12 else "<short>"
    print(f"  {k} = {v}")

n = sum(1 for k in os.environ if k.startswith((
    "HKC_", "HESTIA_", "HF_", "ANTHROPIC_", "OPENAI_", "GOOGLE_",
    "DASHSCOPE_", "LOCAL_", "AMAP_", "BAIDU_", "MINIMAX_", "DEEPSEEK_")))
print(f"TOTAL_PROJECT_KEYS={n}")
