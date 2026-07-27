"""Generate zh-Hant overlay from zh-Hans with Taiwan terminology tweaks."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
OVERLAY_DIR = ROOT / "scripts" / "translation_overlays"
TRANSLATIONS_DIR = ROOT / "custom_components" / "ip_ban_manager" / "translations"

CHAR_MAP = str.maketrans(
    {
        "设": "設",
        "选": "選",
        "择": "擇",
        "录": "錄",
        "网": "網",
        "络": "絡",
        "软": "軟",
        "盘": "盤",
        "载": "載",
        "传": "傳",
        "数": "數",
        "据": "據",
        "库": "庫",
        "应": "應",
        "侧": "側",
        "边": "邊",
        "栏": "欄",
        "复": "複",
        "审": "審",
        "认": "認",
        "确": "確",
        "启": "啟",
        "运": "運",
        "过": "過",
        "检": "檢",
        "测": "測",
        "条": "條",
        "来": "來",
        "败": "敗",
        "尝": "嘗",
        "阈": "閾",
        "许": "許",
        "当": "當",
        "输": "輸",
        "护": "護",
        "监": "監",
        "响": "響",
        "静": "靜",
        "备": "備",
        "写": "寫",
        "读": "讀",
        "仪": "儀",
        "旧": "舊",
        "导": "導",
        "请": "請",
        "删": "刪",
        "预": "預",
        "块": "塊",
        "单": "單",
        "务": "務",
        "时": "時",
        "间": "間",
        "显": "顯",
        "页": "頁",
        "质": "質",
        "广": "廣",
        "围": "圍",
        "执": "執",
        "谨": "謹",
        "仅": "僅",
        "连": "連",
        "关": "關",
        "统": "統",
        "项": "項",
        "该": "該",
        "这": "這",
        "将": "將",
        "从": "從",
        "为": "為",
        "与": "與",
        "无": "無",
        "状": "狀",
        "态": "態",
        "还": "還",
        "没": "沒",
        "现": "現",
        "实": "實",
        "际": "際",
        "种": "種",
        "类": "類",
        "别": "別",
        "发": "發",
        "问": "問",
        "题": "題",
        "开": "開",
        "权": "權",
        "访": "訪",
        "锁": "鎖",
        "踪": "蹤",
        "动": "動",
        "紧": "緊",
        "急": "急",
        "钩": "勾",
        "质": "質",
        "气": "氣",
        "台": "臺",
    }
)

PHRASE_MAP = [
    ("应用", "套用"),
    ("加载", "載入"),
    ("配置", "設定"),
    ("登录", "登入"),
    ("保存", "儲存"),
    ("恢复", "還原"),
    ("磁盘", "磁碟"),
    ("下载", "下載"),
    ("上传", "上傳"),
    ("数据库", "資料庫"),
    ("文件", "檔案"),
    ("添加", "新增"),
    ("阻止", "封鎖"),
    ("已阻止", "已封鎖"),
    ("阻止的", "封鎖的"),
    ("自动封禁", "自動封鎖"),
    ("封禁", "封鎖"),
    ("白名单", "白名單"),
    ("导入", "匯入"),
    ("导出", "匯出"),
    ("默认值", "預設值"),
    ("默认", "預設"),
    ("勾选", "勾選"),
    ("复选框", "核取方塊"),
    ("审查", "檢視"),
    ("检测", "偵測"),
    ("软件", "軟體"),
    ("重启", "重新啟動"),
    ("运行时", "執行階段"),
    ("紧急", "緊急"),
    ("开关", "開關"),
    ("健康检查", "健康檢查"),
    ("跟踪前", "追蹤前"),
    ("Transfer", "傳輸"),
    ("Panel", "面板"),
]


def convert_text(value: str) -> str:
    """Convert one UI string to Traditional Chinese (Taiwan)."""
    text = value
    for source, target in PHRASE_MAP:
        text = text.replace(source, target)
    text = text.translate(CHAR_MAP)
    text = text.replace("應用", "套用").replace("加載", "載入")
    return text


def convert_tree(value: object) -> object:
    """Convert all strings inside a JSON-compatible translation tree."""
    if isinstance(value, dict):
        return {key: convert_tree(item) for key, item in value.items()}
    if isinstance(value, str):
        return convert_text(value)
    return value


def extract_overlay(en: dict[str, Any], locale: dict[str, Any]) -> dict[str, Any]:
    """Return only locale strings that differ from English."""
    overlay: dict[str, Any] = {}
    for key, value in locale.items():
        if key not in en:
            overlay[key] = value
            continue
        english_value = en[key]
        if isinstance(value, dict) and isinstance(english_value, dict):
            nested = extract_overlay(english_value, value)
            if nested:
                overlay[key] = nested
        elif value != english_value:
            overlay[key] = value
    return overlay


def main() -> None:
    """Generate the Traditional Chinese overlay file."""
    english = json.loads((TRANSLATIONS_DIR / "en.json").read_text(encoding="utf-8"))
    simplified = json.loads((OVERLAY_DIR / "zh-Hans.json").read_text(encoding="utf-8"))

    # Start from converted simplified overlay, then fill any de-only gaps from English.
    converted = cast(dict[str, Any], convert_tree(simplified))
    merged = copy.deepcopy(converted)

    def fill_from_english(en_node: dict[str, Any], out_node: dict[str, Any]) -> None:
        for key, en_value in en_node.items():
            if isinstance(en_value, dict):
                child = out_node.setdefault(key, {})
                if isinstance(child, dict):
                    fill_from_english(en_value, child)
                continue
            if key not in out_node:
                out_node[key] = convert_text(en_value)

    fill_from_english(english, merged)

    overlay = extract_overlay(english, merged)
    (OVERLAY_DIR / "zh-Hant.json").write_text(
        json.dumps(overlay, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("wrote zh-Hant.json")


if __name__ == "__main__":
    main()
