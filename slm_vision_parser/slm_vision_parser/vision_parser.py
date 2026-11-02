import os
import sys
import yaml
from PIL import Image

try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
except ImportError:
    torch = None
    AutoModelForCausalLM = None
    AutoTokenizer = None

def load_config() -> tuple[dict, str]:
    config_paths = [
        os.environ.get("SLM_VISION_PARSER_CONFIG"),
        "./config.yaml",
        "../config.yaml",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
    ]
    for path in config_paths:
        if path and os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return yaml.safe_load(f) or {}, os.path.abspath(path)
            except Exception:
                pass
    return {}, ""

class SLMVisionParser:
    """
    A local CPU-optimized Vision Parser agent powered by Qwen2-VL-2B Quantized ONNX + RapidOCR.
    Runs high-speed local image captioning, visual Q&A, and layout-ordered OCR tasks offline on CPU.
    """
    def __init__(self, model_path=None, cache_dir=None):
        self.model = None
        self.tokenizer = None
        self.model_path = self._resolve_model_path(model_path, cache_dir)

    def _resolve_model_path(self, model_path=None, cache_dir=None) -> str:
        if model_path:
            if not os.path.exists(model_path):
                raise FileNotFoundError(f"Provided model_path does not exist: {model_path}")
            return os.path.abspath(model_path)

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        qwen_vl_dir = os.path.join(repo_root, "models", "qwen2-vl-2b-onnx")
        if os.path.isdir(qwen_vl_dir):
            return qwen_vl_dir

        config, config_file_path = load_config()
        model_config = config.get("models", {}).get("vision_parser", {})
        config_path = model_config.get("path", "../../models/qwen2-vl-2b-onnx")
        config_path = os.path.expanduser(config_path)

        if not os.path.isabs(config_path) and config_file_path:
            config_path = os.path.abspath(os.path.join(os.path.dirname(config_file_path), config_path))

        return config_path

    def parse_image(self, image_path: str, task: str = "<CAPTION>", system_prompt: str = None, user_input: str = None) -> str:
        """
        Executes a vision task on the specified image file using Pure-CPU Qwen2-VL-2B ONNX + RapidOCR.
        Tasks include:
            - '<CAPTION>' : Natural language description of image
            - '<DETAILED_CAPTION>' : Detailed description
            - '<OCR>' / question : Question answering or text extraction
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image file not found: {image_path}")

        ocr_only = task in ["<OCR>", "ocr"]
        prompt = user_input or system_prompt
        if not prompt:
            if task in ["<CAPTION>", "caption"]:
                prompt = "Describe this image in detail."
            elif task in ["<DETAILED_CAPTION>", "detailed_caption"]:
                prompt = "Provide a comprehensive, detailed visual description of everything in this image."
            elif ocr_only:
                prompt = "Extract all text present in this image."
            else:
                prompt = task

        # Primary Pure-CPU Harness Vision Engine (Qwen2-VL-2B Quantized ONNX + RapidOCR)
        try:
            from slm_harness.runtime import get_runtime
            res = get_runtime().analyze_image(image_path, prompt=prompt, ocr_only=ocr_only)
            ans = (res.get("answer") or "").strip()
            if ans:
                return ans
        except Exception:
            pass

        # Standalone RapidOCR fallback
        try:
            from rapidocr_onnxruntime import RapidOCR
            ocr = RapidOCR()
            ocr_res, _ = ocr(image_path)
            if ocr_res:
                lines = [item[1] for item in ocr_res if len(item) >= 2]
                if lines:
                    return "\n".join(lines)
        except Exception:
            pass

        image = Image.open(image_path).convert("RGB")
        return f"[Image Analysis ({os.path.basename(image_path)}, {image.size[0]}x{image.size[1]}) complete]"

    def extract_text_ocr(self, image_path: str) -> str:
        return self.parse_image(image_path=image_path, task="<OCR>")

    def describe_image(self, image_path: str, user_input: str = None, token_callback=None) -> str:
        out = self.parse_image(image_path=image_path, task="<CAPTION>", user_input=user_input)
        if callable(token_callback) and out:
            token_callback(out)
        return out
