"""论文 JSON 数据的读写与合并。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def load_data(path: str | Path) -> Dict[str, Any]:
    """读取 JSON 数据文件；文件不存在或为空时返回空字典。"""
    data_path = Path(path)
    if not data_path.exists():
        return {}

    content = data_path.read_text(encoding="utf-8")
    if not content.strip():
        return {}

    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError(f"数据文件顶层必须是 JSON 对象: {data_path}")
    return data


def save_data(path: str | Path, data: Dict[str, Any]) -> None:
    """以 UTF-8、可读格式保存 JSON 数据。"""
    data_path = Path(path)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def merge_papers(
    existing: Dict[str, Any],
    new_papers: List[Dict[str, Any]],
    topic: str,
) -> Dict[str, Any]:
    """按论文 ID 合并新论文，保留历史领域和论文。"""
    result = dict(existing)
    topic_data = dict(result.get(topic, {}))

    for paper in new_papers:
        paper_id = paper.get("id")
        if not paper_id:
            logger.warning("忽略没有 id 的论文记录: %s", paper)
            continue
        topic_data[str(paper_id)] = paper

    result[topic] = topic_data
    logger.info("领域 %s 合并完成，累计 %d 篇", topic, len(topic_data))
    return result
