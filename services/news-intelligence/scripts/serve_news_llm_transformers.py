from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from news_intelligence.config import IntelligenceConfig
from news_intelligence.model_registry import ModelRegistry


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float = 0.0
    max_tokens: int = Field(default=512, ge=1, le=4096)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve a local Transformers LLM through a tiny OpenAI-compatible API.")
    parser.add_argument("--model-key", required=True)
    parser.add_argument("--model-root", default=r"D:\models_artifacts\opensource")
    parser.add_argument("--manifest", default=PACKAGE_ROOT / "models" / "opensource_models.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--served-model-name", default="")
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--dtype", default="auto", choices=["auto", "float32", "float16", "bfloat16"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    import torch
    import uvicorn
    from transformers import AutoModelForCausalLM, AutoTokenizer

    config = IntelligenceConfig.from_env()
    config = replace(config, model_root=Path(args.model_root), manifest_path=Path(args.manifest))
    registry = ModelRegistry(config)
    model_info = registry.model_info(args.model_key)
    model_path = registry.path_for(args.model_key)
    device = resolve_device(torch, args.device)
    dtype = resolve_dtype(torch, args.dtype, device)
    served_model_name = args.served_model_name or model_info.get("serving", {}).get("served_model_name") or model_info.get("repo_id") or args.model_key
    if not model_path.exists():
        raise FileNotFoundError(f"Model path does not exist: {model_path}")

    print(
        json.dumps(
            {
                "event": "loading_model",
                "model_key": args.model_key,
                "model_path": str(model_path),
                "served_model_name": served_model_name,
                "device": device,
                "dtype": str(dtype).replace("torch.", ""),
            }
        ),
        flush=True,
    )
    tokenizer = AutoTokenizer.from_pretrained(str(model_path), trust_remote_code=True)
    load_kwargs: dict[str, Any] = {
        "trust_remote_code": True,
        "dtype": dtype,
        "low_cpu_mem_usage": True,
    }
    if device == "cuda":
        load_kwargs["device_map"] = "auto"
    model = AutoModelForCausalLM.from_pretrained(str(model_path), **load_kwargs)
    if device == "cpu":
        model.to(device)
    model.eval()
    lock = threading.Lock()
    app = FastAPI()

    @app.get("/v1/models")
    def models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": served_model_name, "object": "model"}]}

    @app.post("/v1/chat/completions")
    def chat_completions(request: ChatRequest) -> dict[str, Any]:
        if request.model != served_model_name:
            raise HTTPException(status_code=404, detail=f"Unknown model {request.model}; served model is {served_model_name}")
        prompt = build_prompt(tokenizer, request.messages)
        started = time.perf_counter()
        try:
            with lock:
                encoded = tokenizer(prompt, return_tensors="pt", return_token_type_ids=False)
                if device == "cuda":
                    encoded = {key: value.to("cuda") for key, value in encoded.items()}
                with torch.no_grad():
                    generated = model.generate(
                        **encoded,
                        max_new_tokens=min(request.max_tokens, args.max_new_tokens),
                        do_sample=request.temperature > 0,
                        temperature=max(request.temperature, 0.01),
                        pad_token_id=tokenizer.eos_token_id,
                    )
                output_tokens = generated[0][encoded["input_ids"].shape[-1] :]
                content = tokenizer.decode(output_tokens, skip_special_tokens=True).strip()
        except Exception as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        elapsed = time.perf_counter() - started
        return {
            "id": f"local-transformers-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": served_model_name,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": int(encoded["input_ids"].shape[-1]), "completion_tokens": int(output_tokens.shape[-1]), "total_tokens": int(generated.shape[-1])},
            "local_profile": {"elapsed_seconds": elapsed},
        }

    print(json.dumps({"event": "server_ready", "endpoint": f"http://{args.host}:{args.port}/v1", "served_model_name": served_model_name}), flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=False)
    return 0


def build_prompt(tokenizer: Any, messages: list[ChatMessage]) -> str:
    rows = [{"role": message.role, "content": message.content} for message in messages]
    if getattr(tokenizer, "chat_template", None):
        return tokenizer.apply_chat_template(rows, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    return "\n\n".join(f"{message.role.upper()}:\n{message.content}" for message in messages) + "\n\nASSISTANT:\n"


def resolve_device(torch_module: Any, requested: str) -> str:
    if requested != "auto":
        return requested
    return "cuda" if torch_module.cuda.is_available() else "cpu"


def resolve_dtype(torch_module: Any, requested: str, device: str) -> Any:
    if requested == "float32":
        return torch_module.float32
    if requested == "float16":
        return torch_module.float16
    if requested == "bfloat16":
        return torch_module.bfloat16
    return torch_module.bfloat16 if device == "cuda" else torch_module.float32


if __name__ == "__main__":
    raise SystemExit(main())
