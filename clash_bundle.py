#!/usr/bin/env python3
"""Download a Clash subscription and rebuild it into a slim provider-based set.

The generated top-level config mirrors the shape of the example file:
it keeps base settings in the root YAML and moves proxies/rules into provider
files that can be hosted alongside the main subscription.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import yaml


DEFAULT_SOURCE_URL = "https://fc-sub.yuritele.com/link/6984e0aacb26accf?clash=1"
DEFAULT_OUTPUT_DIR = Path("dist")
DEFAULT_CUSTOM_RULES = Path("custom-rules.txt")
DEFAULT_MANAGED_NAME = "managed.yaml"
DEFAULT_PROXY_PROVIDER_NAME = "my-subscription"
DEFAULT_DIRECT_RULE_PROVIDER_NAME = "source-direct"
DEFAULT_PROXY_RULE_PROVIDER_NAME = "source-proxy"
DEFAULT_CUSTOM_DIRECT_RULE_PROVIDER_NAME = "custom-direct"
DEFAULT_CUSTOM_PROXY_RULE_PROVIDER_NAME = "custom-proxy"
DEFAULT_ENV_FILE = Path(".env")


@dataclass(frozen=True)
class BuildInputs:
    source_url: str
    public_base_url: str
    output_dir: Path
    custom_rules_path: Path
    managed_name: str
    env_file: Path


def fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 clash-bundle/2.0"})
    with urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def load_yaml(text: str) -> dict[str, Any]:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("The source subscription does not contain a YAML mapping at the top level.")
    return data


def dump_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        values[key] = value
    return values


def resolve_config(argv: list[str] | None = None) -> BuildInputs:
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Path to a .env-style file.")
    bootstrap_args, remaining_argv = bootstrap.parse_known_args(argv)

    env_values = dict(os.environ)
    env_values.update(parse_env_file(Path(bootstrap_args.env_file)))

    parser = argparse.ArgumentParser(
        description="Download a Clash subscription and rebuild it into provider-based files.",
        parents=[bootstrap],
    )
    parser.add_argument("--source-url", default=env_values.get("SOURCE_URL", DEFAULT_SOURCE_URL), help="Subscription URL to download.")
    parser.add_argument(
        "--public-base-url",
        default=env_values.get("PUBLIC_BASE_URL", ""),
        help="Public base URL where the generated proxy-provider and ruleset files will be hosted.",
    )
    parser.add_argument("--output-dir", default=env_values.get("OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)), help="Directory for generated files.")
    parser.add_argument(
        "--custom-rules",
        default=env_values.get("CUSTOM_RULES", str(DEFAULT_CUSTOM_RULES)),
        help="Path to a custom Clash rules fragment file.",
    )
    parser.add_argument(
        "--managed-name",
        default=env_values.get("MANAGED_NAME", DEFAULT_MANAGED_NAME),
        help="Filename for the final top-level Clash config.",
    )
    parsed = parser.parse_args(remaining_argv)
    if not parsed.public_base_url:
        parser.error("--public-base-url or PUBLIC_BASE_URL is required")

    return BuildInputs(
        source_url=parsed.source_url,
        public_base_url=parsed.public_base_url.rstrip("/"),
        output_dir=Path(parsed.output_dir),
        custom_rules_path=Path(parsed.custom_rules),
        managed_name=parsed.managed_name,
        env_file=Path(parsed.env_file),
    )


def read_custom_rules(path: Path) -> list[str]:
    if not path.exists():
        path.write_text(
            "# One Clash rule per line. Blank lines and comments are ignored.\n"
            "# Example:\n"
            "# DOMAIN-SUFFIX,example.com,DIRECT\n"
            "# DOMAIN-SUFFIX,example.org,PROXY\n",
            encoding="utf-8",
        )
        return []

    rules: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        rules.append(line)
    return rules


def normalize_rule_target(rule: str) -> str | None:
    parts = [part.strip() for part in rule.split(",")]
    if len(parts) < 2:
        return None
    if parts[-1] == "no-resolve" and len(parts) >= 3:
        return parts[-2]
    return parts[-1]


def rule_condition(rule: str) -> str | None:
    parts = [part.strip() for part in rule.split(",")]
    if len(parts) < 2:
        return None
    target = normalize_rule_target(rule)
    if target is None:
        return None
    if target == "no-resolve":
        return None
    return ",".join(parts[:-1]) if parts[-1] != "no-resolve" else ",".join(parts[:-2])


def split_rules_by_target(rules: list[Any]) -> tuple[list[str], list[str], list[str]]:
    direct_rules: list[str] = []
    proxy_rules: list[str] = []
    passthrough_rules: list[str] = []

    for rule in rules:
        if not isinstance(rule, str):
            continue
        normalized = rule.strip()
        if not normalized:
            continue
        if normalized.startswith("MATCH,"):
            continue

        target = normalize_rule_target(normalized)
        condition = rule_condition(normalized)
        if condition is None:
            passthrough_rules.append(normalized)
            continue

        if target == "DIRECT":
            direct_rules.append(condition)
        elif target in {"REJECT", "REJECT-DROP", "REJECT-TINYGIF"}:
            passthrough_rules.append(normalized)
        else:
            proxy_rules.append(condition)

    return direct_rules, proxy_rules, passthrough_rules


def build_provider_file(payload_rules: list[str]) -> dict[str, list[str]]:
    return {"payload": payload_rules}


def build_managed_config(source_config: dict[str, Any], public_base_url: str, timestamp: str) -> dict[str, Any]:
    base_config = {
        key: value
        for key, value in source_config.items()
        if key not in {"proxies", "proxy-groups", "rules"}
    }

    proxy_provider_name = DEFAULT_PROXY_PROVIDER_NAME
    direct_rule_provider_name = DEFAULT_DIRECT_RULE_PROVIDER_NAME
    proxy_rule_provider_name = DEFAULT_PROXY_RULE_PROVIDER_NAME
    custom_direct_rule_provider_name = DEFAULT_CUSTOM_DIRECT_RULE_PROVIDER_NAME
    custom_proxy_rule_provider_name = DEFAULT_CUSTOM_PROXY_RULE_PROVIDER_NAME

    managed_config = dict(base_config)
    managed_config["proxies"] = []
    managed_config["proxy-providers"] = {
        proxy_provider_name: {
            "type": "http",
            "url": f"{public_base_url}/proxy-providers/{proxy_provider_name}.yaml",
            "interval": 60,
            "path": f"./proxy-providers/{proxy_provider_name}.yaml",
            "health-check": {
                "enable": True,
                "interval": 15,
                "url": "http://www.gstatic.com/generate_204",
                "timeout": 5,
            },
        }
    }
    managed_config["proxy-groups"] = [
        {
            "name": "PROXY",
            "type": "select",
            "use": [proxy_provider_name],
            "proxies": ["DIRECT"],
        },
        {
            "name": "Auto",
            "type": "url-test",
            "use": [proxy_provider_name],
            "url": "http://www.gstatic.com/generate_204",
            "interval": 300,
        },
        {
            "name": "Fallback",
            "type": "fallback",
            "use": [proxy_provider_name],
            "url": "http://www.gstatic.com/generate_204",
            "interval": 300,
        },
    ]
    managed_config["rule-providers"] = {
        direct_rule_provider_name: {
            "type": "http",
            "behavior": "classical",
            "url": f"{public_base_url}/ruleset/{direct_rule_provider_name}.yaml",
            "path": f"./ruleset/{direct_rule_provider_name}.yaml",
            "interval": 300,
        },
        proxy_rule_provider_name: {
            "type": "http",
            "behavior": "classical",
            "url": f"{public_base_url}/ruleset/{proxy_rule_provider_name}.yaml",
            "path": f"./ruleset/{proxy_rule_provider_name}.yaml",
            "interval": 300,
        },
        custom_direct_rule_provider_name: {
            "type": "http",
            "behavior": "classical",
            "url": f"{public_base_url}/ruleset/{custom_direct_rule_provider_name}.yaml",
            "path": f"./ruleset/{custom_direct_rule_provider_name}.yaml",
            "interval": 300,
        },
        custom_proxy_rule_provider_name: {
            "type": "http",
            "behavior": "classical",
            "url": f"{public_base_url}/ruleset/{custom_proxy_rule_provider_name}.yaml",
            "path": f"./ruleset/{custom_proxy_rule_provider_name}.yaml",
            "interval": 300,
        },
    }
    managed_config["rules"] = [
        "IP-CIDR,127.0.0.0/8,DIRECT",
        "IP-CIDR,172.16.0.0/12,DIRECT",
        "IP-CIDR,192.168.0.0/16,DIRECT",
        "IP-CIDR,10.0.0.0/8,DIRECT",
    ]
    return managed_config


def build_files(inputs: BuildInputs) -> Path:
    source_text = fetch_text(inputs.source_url)
    source_config = load_yaml(source_text)
    proxies = source_config.get("proxies", [])
    rules = source_config.get("rules", [])
    custom_rules = read_custom_rules(inputs.custom_rules_path)
    source_direct_rules, source_proxy_rules, passthrough_rules = split_rules_by_target(rules)

    custom_direct_rules: list[str] = []
    custom_proxy_rules: list[str] = []
    for rule in custom_rules:
        target = normalize_rule_target(rule)
        condition = rule_condition(rule)
        if condition is None:
            passthrough_rules.append(rule)
            continue
        if target == "DIRECT":
            custom_direct_rules.append(condition)
        else:
            custom_proxy_rules.append(condition)

    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    output_dir = inputs.output_dir
    proxy_provider_dir = output_dir / "proxy-providers"
    ruleset_dir = output_dir / "ruleset"
    output_dir.mkdir(parents=True, exist_ok=True)

    managed_config = build_managed_config(source_config, inputs.public_base_url.rstrip("/"), timestamp)

    managed_rules = managed_config["rules"]
    managed_rules.extend(passthrough_rules)
    managed_rules.extend(
        [
            f"RULE-SET,{DEFAULT_DIRECT_RULE_PROVIDER_NAME},DIRECT",
            f"RULE-SET,{DEFAULT_CUSTOM_DIRECT_RULE_PROVIDER_NAME},DIRECT",
            f"RULE-SET,{DEFAULT_PROXY_RULE_PROVIDER_NAME},PROXY",
            f"RULE-SET,{DEFAULT_CUSTOM_PROXY_RULE_PROVIDER_NAME},PROXY",
            "GEOIP,CN,DIRECT",
            "MATCH,DIRECT",
        ]
    )

    write_text(output_dir / inputs.managed_name, "#!MANAGED-CONFIG\n" + f"# Generated from: {inputs.source_url}\n" + f"# Generated at: {timestamp}\n" + dump_yaml(managed_config))
    write_text(proxy_provider_dir / f"{DEFAULT_PROXY_PROVIDER_NAME}.yaml", dump_yaml(build_provider_file(proxies if isinstance(proxies, list) else [])))
    write_text(ruleset_dir / f"{DEFAULT_DIRECT_RULE_PROVIDER_NAME}.yaml", dump_yaml(build_provider_file(source_direct_rules)))
    write_text(ruleset_dir / f"{DEFAULT_PROXY_RULE_PROVIDER_NAME}.yaml", dump_yaml(build_provider_file(source_proxy_rules)))
    write_text(ruleset_dir / f"{DEFAULT_CUSTOM_DIRECT_RULE_PROVIDER_NAME}.yaml", dump_yaml(build_provider_file(custom_direct_rules)))
    write_text(ruleset_dir / f"{DEFAULT_CUSTOM_PROXY_RULE_PROVIDER_NAME}.yaml", dump_yaml(build_provider_file(custom_proxy_rules)))

    custom_rules_output = output_dir / "custom-rules.txt"
    custom_rules_output.write_text(inputs.custom_rules_path.read_text(encoding="utf-8") if inputs.custom_rules_path.exists() else "", encoding="utf-8")

    write_text(output_dir / "base.yaml", dump_yaml({key: value for key, value in source_config.items() if key not in {"proxies", "proxy-groups", "rules"}}))
    write_text(output_dir / "nodes.yaml", dump_yaml({"proxies": proxies if isinstance(proxies, list) else []}))
    write_text(output_dir / "source-rules-direct.yaml", dump_yaml(build_provider_file(source_direct_rules)))
    write_text(output_dir / "source-rules-proxy.yaml", dump_yaml(build_provider_file(source_proxy_rules)))

    summary = {
        "source_url": inputs.source_url,
        "public_base_url": inputs.public_base_url,
        "env_file": str(inputs.env_file),
        "generated_at": timestamp,
        "managed_file": str(output_dir / inputs.managed_name),
        "proxy_count": len(proxies) if isinstance(proxies, list) else 0,
        "source_direct_rule_count": len(source_direct_rules),
        "source_proxy_rule_count": len(source_proxy_rules),
        "custom_direct_rule_count": len(custom_direct_rules),
        "custom_proxy_rule_count": len(custom_proxy_rules),
        "passthrough_rule_count": len(passthrough_rules),
    }
    write_text(output_dir / "build-info.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    return output_dir / inputs.managed_name


def main() -> None:
    inputs = resolve_config()
    managed_path = build_files(inputs)
    print(f"Generated Clash bundle at {managed_path}")


if __name__ == "__main__":
    main()