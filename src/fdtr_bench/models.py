"""加载模型与容器配置：

- configs/models.yaml    模型 profile / default（含 api_key，已 gitignore）
- configs/container.yaml 容器镜像 tag（image_tags，纳入版本控制）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "models.yaml"
CONTAINER_PATH = ROOT / "configs" / "container.yaml"


@dataclass
class PricingConfig:
    cache_read: float = 0.0   # $/1M tokens
    input: float = 0.0        # $/1M tokens（非缓存输入）
    output: float = 0.0       # $/1M tokens（含 reasoning）


@dataclass
class ModelProfile:
    name: str
    provider: str
    api_key: str
    api_key_env: str = ""          # api_key 注入到的容器环境变量名（如 DEEPSEEK_API_KEY）
    endpoint: str = ""
    variant: str = "max"          # opencode run 的 --variant 参数
    opencode_env: dict[str, str] = field(default_factory=dict)
    pricing: PricingConfig | None = None


@dataclass
class ModelConfig:
    profiles: dict[str, ModelProfile] = field(default_factory=dict)
    image_tags: dict[str, str] = field(default_factory=dict)
    default: str = ""

    def get_profile(self, name: str | None = None) -> ModelProfile:
        """取运行模型 profile：name 缺省时用 default。"""
        key = name or self.default
        if not key:
            raise ValueError("未指定 profile 且 models.yaml 未设置 default")
        if key not in self.profiles:
            raise KeyError(f"profile '{key}' 不存在于 models.yaml，可用: {list(self.profiles)}")
        return self.profiles[key]

    def get_image(self, tier: str) -> str:
        if tier not in self.image_tags:
            raise KeyError(f"image_tags 未配置 tier '{tier}'，可用: {list(self.image_tags)}")
        return self.image_tags[tier]


def _load_image_tags(path: Path | str | None = None) -> dict[str, str]:
    """从 configs/container.yaml 加载容器镜像 tag。"""
    cp = Path(path) if path else CONTAINER_PATH
    if not cp.exists():
        raise FileNotFoundError(
            f"容器配置文件不存在: {cp}\n请在 configs/container.yaml 配置 image_tags。"
        )
    raw = yaml.safe_load(cp.read_text(encoding="utf-8")) or {}
    return dict(raw.get("image_tags") or {})


def load_model_config(
    path: Path | str | None = None,
    container_path: Path | str | None = None,
) -> ModelConfig:
    """加载模型 profile（models.yaml）与镜像 tag（container.yaml）。"""
    p = Path(path) if path else CONFIG_PATH
    if not p.exists():
        raise FileNotFoundError(
            f"配置文件不存在: {p}\n请复制 configs/models.example.yaml 为 models.yaml 并填入真实配置。"
        )
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    profiles: dict[str, ModelProfile] = {}
    for name, spec in (raw.get("profiles") or {}).items():
        pricing_raw = spec.get("pricing")
        pricing = None
        if isinstance(pricing_raw, dict):
            pricing = PricingConfig(
                cache_read=float(pricing_raw.get("cache_read", 0)),
                input=float(pricing_raw.get("input", 0)),
                output=float(pricing_raw.get("output", 0)),
            )
        profiles[name] = ModelProfile(
            name=name,
            provider=spec.get("provider", ""),
            api_key=spec.get("api_key", ""),
            api_key_env=spec.get("api_key_env", ""),
            endpoint=spec.get("endpoint", ""),
            variant=spec.get("variant", "max"),
            opencode_env=dict(spec.get("opencode_env") or {}),
            pricing=pricing,
        )

    return ModelConfig(
        profiles=profiles,
        image_tags=_load_image_tags(container_path),
        default=raw.get("default", ""),
    )


def render_opencode_json(profile: ModelProfile) -> str:
    """根据 profile 生成运行时 opencode.json 内容。

    对于 openai-compatible provider，注入 provider 段；其余 provider 依赖环境变量。
    """
    model_env = profile.opencode_env.get("OPENCODE_MODEL", "")
    provider_id = model_env.split("/", 1)[0] if "/" in model_env else profile.provider

    config: dict = {
        "$schema": "https://opencode.ai/config.json",
        "model": "{env:OPENCODE_MODEL}",
        "shell": "bash",
        "permission": {"edit": "allow", "bash": "allow", "write": "allow"},
    }

    if profile.provider == "openai-compatible" and "/" in model_env:
        model_name = model_env.split("/", 1)[1]
        config["provider"] = {
            provider_id: {
                "id": provider_id,
                "name": provider_id,
                "api": "openai-compatible",
                "options": {
                    "baseURL": profile.endpoint,
                    "apiKey": profile.api_key or "na",
                },
                "models": {
                    model_name: {"id": model_name, "name": model_name},
                },
            }
        }

    return json.dumps(config, indent=2, ensure_ascii=False)
