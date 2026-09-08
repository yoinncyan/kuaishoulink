#!/usr/bin/env python3
"""Collect and refine VPN-related search suggestions from public SEO signals."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx


SEEDS: list[tuple[str, str]] = [
    # VPN core
    ("vpn", "VPN核心"),
    ("vpn是什么", "VPN核心"),
    ("vpn怎么用", "VPN核心"),
    ("vpn安全吗", "VPN核心"),
    ("vpn哪个好", "VPN核心"),
    ("vpn软件", "VPN产品"),
    ("vpn加速器", "VPN产品"),
    ("vpn下载", "VPN产品"),
    ("vpn免费", "VPN产品"),
    ("vpn推荐", "VPN产品"),
    ("vpn连接", "VPN技术"),
    ("vpn配置", "VPN技术"),
    ("vpn设置", "VPN技术"),
    ("vpn服务器", "VPN技术"),
    ("vpn客户端", "VPN产品"),
    ("vpn节点", "VPN技术"),
    ("vpn协议", "VPN技术"),
    ("vpn路由器", "VPN技术"),
    ("vpn电脑", "VPN产品"),
    ("vpn手机", "VPN产品"),
    ("vpn安卓", "VPN产品"),
    ("vpn苹果", "VPN产品"),
    ("企业vpn", "企业办公"),
    ("远程办公vpn", "企业办公"),
    ("虚拟专用网络", "VPN核心"),
    ("虚拟私人网络", "VPN核心"),
    ("ssl vpn", "VPN协议"),
    ("ipsec vpn", "VPN协议"),
    ("openvpn", "VPN协议"),
    ("wireguard", "VPN协议"),
    # Cross-border business adjacency
    ("跨境网络", "跨境外贸"),
    ("跨境电商网络", "跨境外贸"),
    ("外贸网络", "跨境外贸"),
    ("海外网络", "跨境外贸"),
    ("海外专线", "跨境外贸"),
    ("国际专线", "跨境外贸"),
    ("跨境专线", "跨境外贸"),
    ("海外加速器", "跨境外贸"),
    ("国际网络加速", "跨境外贸"),
    ("tiktok网络", "跨境平台"),
    ("亚马逊网络", "跨境平台"),
    ("海外直播网络", "跨境直播"),
    ("跨境直播网络", "跨境直播"),
    ("回国加速器", "跨境网络"),
    # Related network terms
    ("网络加速", "网络加速"),
    ("网络加速器", "网络加速"),
    ("游戏加速器", "游戏加速"),
    ("代理服务器", "代理IP"),
    ("静态ip", "代理IP"),
    ("独立ip", "代理IP"),
    ("海外ip", "代理IP"),
    ("住宅ip", "代理IP"),
    ("异地组网", "企业办公"),
    ("内网穿透", "企业办公"),
    ("防关联", "跨境店群"),
    ("多店铺防关联", "跨境店群"),
    ("sd-wan", "企业办公"),
    ("sase", "企业办公"),
]

# Curated from the 65 real search-result rows in the user-provided
# `快手视频链接.xlsx`. Promotional hashtags and creator names are excluded;
# terms below describe recurring network problems, products, games or intent.
VIDEO_DERIVED_SEEDS: list[tuple[str, str]] = [
    ("苹果手机vpn设置", "快手样本-VPN设置"),
    ("手机vpn设置", "快手样本-VPN设置"),
    ("vpn速度慢", "快手样本-VPN排障"),
    ("vpn节点超时", "快手样本-VPN排障"),
    ("vpn原理", "快手样本-VPN科普"),
    ("vpn修改", "快手样本-VPN设置"),
    ("vpn违法吗", "快手样本-VPN法律"),
    ("用vpn会被抓吗", "快手样本-VPN法律"),
    ("翻墙违法吗", "快手样本-VPN法律"),
    ("翻墙会被抓吗", "快手样本-VPN法律"),
    ("vpn和翻墙的区别", "快手样本-VPN科普"),
    ("vpn法律风险", "快手样本-VPN法律"),
    ("vpn行政处罚", "快手样本-VPN法律"),
    ("翻墙", "快手样本-关联表达"),
    ("外网", "快手样本-关联表达"),
    ("访问外网", "快手样本-关联表达"),
    ("梯子", "快手样本-关联表达"),
    ("网络用语梯子", "快手样本-关联表达"),
    ("科学上网", "快手样本-关联表达"),
    ("魔法上网", "快手样本-关联表达"),
    ("境外网站", "快手样本-海外访问"),
    ("国外软件不能用", "快手样本-海外访问"),
    ("海外软件下载", "快手样本-海外访问"),
    ("海外应用商店", "快手样本-海外访问"),
    ("海外app下载", "快手样本-海外访问"),
    ("锁国区", "快手样本-海外访问"),
    ("国区限制", "快手样本-海外访问"),
    ("tiktok网络环境异常", "快手样本-跨境平台"),
    ("tiktok无网络连接", "快手样本-跨境平台"),
    ("telegram一直连接中", "快手样本-跨境平台"),
    ("telegram连接不上", "快手样本-跨境平台"),
    ("shadowrocket连接不上", "快手样本-跨境平台"),
    ("跨境电商网络环境", "快手样本-跨境电商"),
    ("跨境电商网络环境搭建", "快手样本-跨境电商"),
    ("虚拟局域网", "快手样本-网络技术"),
    ("apn设置", "快手样本-移动网络"),
    ("全球节点", "快手样本-节点"),
    ("网络延迟卡顿", "快手样本-网络排障"),
    ("游戏网络延迟", "快手样本-游戏网络"),
    ("海外网络卡顿", "快手样本-海外访问"),
    ("海外留学生网络卡顿", "快手样本-海外访问"),
    ("校园网卡顿", "快手样本-校园网"),
    ("校园网vpn", "快手样本-校园网"),
    ("高ping", "快手样本-网络排障"),
    ("随身wifi", "快手样本-移动网络"),
    ("随身无线宽带", "快手样本-移动网络"),
    ("上网卡", "快手样本-移动网络"),
    ("手机卡上外网", "快手样本-移动网络"),
    ("免费加速器", "快手样本-加速器"),
    ("永久免费加速器", "快手样本-加速器"),
    ("加速器测评", "快手样本-加速器"),
    ("ios加速器测评", "快手样本-加速器"),
    ("加速器排行榜", "快手样本-加速器"),
    ("游戏加速器推荐", "快手样本-加速器"),
    ("游戏加速器下载", "快手样本-加速器"),
    ("加速器延迟高", "快手样本-加速器"),
    ("加速器卡顿", "快手样本-加速器"),
    ("加速器免费时长", "快手样本-加速器"),
    ("加速器直装", "快手样本-加速器"),
    ("国际服加速器", "快手样本-游戏加速"),
    ("国际服下载加速", "快手样本-游戏加速"),
    ("steam游戏加速器", "快手样本-游戏加速"),
    ("pubg加速器", "快手样本-游戏加速"),
    ("和平精英加速器", "快手样本-游戏加速"),
    ("地铁逃生加速器", "快手样本-游戏加速"),
    ("apex英雄加速器", "快手样本-游戏加速"),
    ("对峙2加速器", "快手样本-游戏加速"),
    ("荒野乱斗国际服加速器", "快手样本-游戏加速"),
    ("氧化物生存岛加速器", "快手样本-游戏加速"),
    ("三角洲日服加速器", "快手样本-游戏加速"),
    ("球球大作战加速器", "快手样本-游戏加速"),
    ("迅游加速器", "快手样本-加速器品牌"),
    ("雷神加速器", "快手样本-加速器品牌"),
    ("奇游手游加速器", "快手样本-加速器品牌"),
    ("tt加速器", "快手样本-加速器品牌"),
    ("火箭加速器", "快手样本-加速器品牌"),
    ("主机游戏加速器", "快手样本-游戏加速"),
    ("联机游戏加速器", "快手样本-游戏加速"),
    ("手游加速器", "快手样本-游戏加速"),
    ("手机游戏加速器", "快手样本-游戏加速"),
    ("电脑游戏加速器", "快手样本-游戏加速"),
]

SEEDS.extend(VIDEO_DERIVED_SEEDS)
VIDEO_DERIVED_WORDS = {word for word, _ in VIDEO_DERIVED_SEEDS}

SOURCE_INFO = {
    "google_web": {
        "name": "Google Web 联想",
        "base_url": "https://suggestqueries.google.com/complete/search",
    },
    "google_youtube": {
        "name": "Google/YouTube 联想",
        "base_url": "https://suggestqueries.google.com/complete/search",
    },
    "bing_cn": {
        "name": "Bing 中文联想",
        "base_url": "https://api.bing.com/osjson.aspx",
    },
    "baidu": {
        "name": "百度联想",
        "base_url": "https://suggestion.baidu.com/su",
    },
    "5118_index": {
        "name": "5118公开长尾词索引",
        "base_url": "https://www.5118.com/seo/newwords/",
    },
    "kuaishou_sample": {
        "name": "快手视频样本词根",
        "base_url": "快手视频链接.xlsx",
    },
}

INDEXED_ROWS = [
    {
        "phase": "seo_platform_index",
        "source": "5118_index",
        "source_name": "5118公开长尾词索引",
        "source_url": "https://www.5118.com/seo/newwords/e6l07l67l15f7de65128666b57295cae47135/",
        "seed": "vpn",
        "seed_category": "VPN核心",
        "rank": 1,
        "keyword": "北京大学校园网vpn",
    }
]

QUESTION_OR_INTENT = (
    "是什么",
    "什么意思",
    "怎么",
    "如何",
    "为什么",
    "哪个好",
    "推荐",
    "区别",
    "安全吗",
    "价格",
    "收费",
    "免费",
    "下载",
    "安装",
    "配置",
    "设置",
    "连接",
    "连不上",
    "教程",
    "服务商",
)

DIRECT_TERMS = (
    "vpn",
    "虚拟专用网络",
    "虚拟私人网络",
    "openvpn",
    "wireguard",
    "ipsec",
    "ssl vpn",
    "翻墙",
    "科学上网",
    "魔法上网",
)
CROSS_BORDER_TERMS = (
    "跨境",
    "外贸",
    "海外",
    "国际专线",
    "出海",
    "亚马逊",
    "tiktok",
    "独立站",
    "shopify",
    "回国加速",
)
RELATED_TERMS = (
    "网络加速",
    "加速器",
    "代理服务器",
    "静态ip",
    "独立ip",
    "海外ip",
    "住宅ip",
    "异地组网",
    "内网穿透",
    "防关联",
    "sd-wan",
    "sase",
    "外网",
    "telegram",
    "shadowrocket",
    "境外网站",
    "国外软件",
    "海外应用商店",
    "国区限制",
    "锁国区",
    "虚拟局域网",
    "apn设置",
    "全球节点",
    "网络延迟",
    "网络卡顿",
    "校园网",
    "高ping",
    "随身wifi",
    "随身无线宽带",
    "上网卡",
)
SENSITIVE_TERMS = (
    "翻墙",
    "科学上网",
    "机场",
    "梯子",
    "节点",
    "破解",
    "账号共享",
)
NOISE_TERMS = (
    "破解版",
    "账号密码大全",
    "永久激活码",
    "色情",
    "赌博",
    "博彩",
)

TRADITIONAL_REPLACEMENTS = {
    "免費": "免费",
    "下載": "下载",
    "軟體": "软件",
    "網路": "网络",
    "網絡": "网络",
    "連線": "连接",
    "連接": "连接",
    "推薦": "推荐",
    "電腦": "电脑",
    "蘋果": "苹果",
    "設置": "设置",
    "服務器": "服务器",
    "節點": "节点",
    "遊戲": "游戏",
    "專線": "专线",
    "國際": "国际",
    "購買": "购买",
    "試用": "试用",
    "區別": "区别",
    "與": "与",
}


@dataclass(frozen=True)
class RequestSpec:
    source: str
    seed: str
    seed_category: str
    url: str


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _parse_suggestions(source: str, body: bytes) -> list[str]:
    text = _decode(body).strip()
    if not text:
        return []
    if source in {"google_web", "google_youtube", "bing_cn"}:
        payload = json.loads(text)
        values = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
        return [str(value).strip() for value in values if str(value).strip()]
    if source == "baidu":
        match = re.search(r"\bs\s*:\s*(\[.*\])\s*}\s*\)\s*;?\s*$", text)
        if not match:
            return []
        values = json.loads(match.group(1))
        return [str(value).strip() for value in values if str(value).strip()]
    return []


def _specs(seeds: list[tuple[str, str]]) -> list[RequestSpec]:
    specs: list[RequestSpec] = []
    for seed, category in seeds:
        encoded = quote(seed)
        specs.extend(
            [
                RequestSpec(
                    "google_web",
                    seed,
                    category,
                    f"https://suggestqueries.google.com/complete/search?client=firefox&hl=zh-CN&gl=cn&q={encoded}",
                ),
                RequestSpec(
                    "google_youtube",
                    seed,
                    category,
                    f"https://suggestqueries.google.com/complete/search?client=firefox&hl=zh-CN&gl=cn&ds=yt&q={encoded}",
                ),
                RequestSpec(
                    "bing_cn",
                    seed,
                    category,
                    f"https://api.bing.com/osjson.aspx?query={encoded}&mkt=zh-CN",
                ),
                RequestSpec(
                    "baidu",
                    seed,
                    category,
                    f"https://suggestion.baidu.com/su?wd={encoded}&cb=cb",
                ),
            ]
        )
    return specs


async def _fetch_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    spec: RequestSpec,
) -> tuple[RequestSpec, list[str], str | None]:
    async with semaphore:
        for attempt in range(2):
            try:
                response = await client.get(spec.url)
                response.raise_for_status()
                return spec, _parse_suggestions(spec.source, response.content), None
            except Exception as exc:
                if attempt == 1:
                    return spec, [], f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(0.4)
        return spec, [], "unknown error"


def normalize_keyword(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).strip().lower()
    for traditional, simplified in TRADITIONAL_REPLACEMENTS.items():
        text = text.replace(traditional, simplified)
    text = text.replace("–", "-").replace("—", "-").replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"open\s+vpn", "openvpn", text, flags=re.I)
    text = re.sub(r"wire\s+guard", "wireguard", text, flags=re.I)
    text = re.sub(r"ip\s*sec", "ipsec", text, flags=re.I)
    text = re.sub(r"(?i)vpn\s+(?=[\u4e00-\u9fff])", "vpn", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=vpn\b)", "", text, flags=re.I)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(
        r"(?i)\b(openvpn|wireguard|ipsec|sd-wan|sase)\s+(?=[\u4e00-\u9fff])",
        r"\1",
        text,
    )
    text = re.sub(r"(?<=[a-z0-9])\s+(?=[\u4e00-\u9fff])", "", text, flags=re.I)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[a-z0-9])", "", text, flags=re.I)
    return text.strip(" ,，。.!！?？:：;；-_")


def contains_topic_term(keyword: str, term: str) -> bool:
    """Match ambiguous Latin/IP terms without accepting longer noise words."""
    if term in {"sase", "sd-wan", "静态ip", "独立ip", "海外ip", "住宅ip"}:
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
                keyword,
                flags=re.I,
            )
        )
    return term in keyword


def contains_any_topic_term(keyword: str, terms: tuple[str, ...]) -> bool:
    return any(contains_topic_term(keyword, term) for term in terms)


def classify_intent(keyword: str) -> str:
    if any(term in keyword for term in ("企业", "公司", "办公", "远程", "组网", "内网", "sd-wan", "sase")):
        return "企业办公"
    if any(term in keyword for term in CROSS_BORDER_TERMS):
        return "跨境外贸"
    if any(term in keyword for term in ("怎么", "如何", "设置", "配置", "连接", "搭建", "教程", "连不上", "失败", "开启", "关闭")):
        return "配置排障"
    if any(term in keyword for term in ("推荐", "哪个好", "好用", "价格", "收费", "购买", "服务商")):
        return "选型购买"
    if any(term in keyword for term in ("下载", "安装", "软件", "客户端", "安卓", "苹果", "电脑", "手机", "app")):
        return "下载安装"
    if any(term in keyword for term in ("是什么", "什么意思", "原理", "区别", "协议", "安全吗")):
        return "科普原理"
    if any(term in keyword for term in ("游戏", "手游", "网游")):
        return "游戏娱乐"
    if any(term in keyword for term in ("ip", "代理", "节点", "防关联", "住宅")):
        return "代理与IP"
    return "泛需求"


def classify_topic(keyword: str) -> str:
    if contains_any_topic_term(
        keyword, ("openvpn", "wireguard", "ipsec", "ssl vpn", "协议")
    ):
        return "VPN协议与技术"
    if any(term in keyword for term in CROSS_BORDER_TERMS):
        return "跨境与海外网络"
    if any(term in keyword for term in ("企业", "办公", "远程", "异地组网", "sd-wan", "sase", "内网")):
        return "企业网络"
    if contains_any_topic_term(
        keyword, ("静态ip", "独立ip", "海外ip", "住宅ip", "代理", "防关联")
    ):
        return "代理与IP"
    if any(term in keyword for term in ("游戏", "手游", "网游")):
        return "游戏加速"
    if contains_any_topic_term(keyword, DIRECT_TERMS):
        return "VPN核心"
    if "加速" in keyword:
        return "网络加速"
    return "关联网络需求"


def is_long_tail(keyword: str) -> bool:
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", keyword))
    word_count = len(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", keyword))
    return (
        chinese_chars >= 5
        or word_count >= 3
        or any(term in keyword for term in QUESTION_OR_INTENT)
    )


def refine(raw_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        normalized = normalize_keyword(row["keyword"])
        row["normalized"] = normalized
        if not normalized:
            row["retained"] = False
            row["exclude_reason"] = "空词"
            continue
        item = grouped.setdefault(
            normalized,
            {
                "keyword": normalized,
                "variants": set(),
                "sources": set(),
                "source_names": set(),
                "seeds": set(),
                "seed_categories": set(),
                "best_rank": 999,
                "raw_occurrences": 0,
            },
        )
        item["variants"].add(row["keyword"])
        item["sources"].add(row["source"])
        item["source_names"].add(row["source_name"])
        item["seeds"].add(row["seed"])
        item["seed_categories"].add(row["seed_category"])
        item["best_rank"] = min(item["best_rank"], int(row["rank"]))
        item["raw_occurrences"] += 1

    refined: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for keyword, item in grouped.items():
        japanese = bool(re.search(r"[\u3040-\u30ff]", keyword))
        direct = contains_any_topic_term(keyword, DIRECT_TERMS)
        cross_border = contains_any_topic_term(keyword, CROSS_BORDER_TERMS)
        related = contains_any_topic_term(keyword, RELATED_TERMS)
        accelerator = "加速器" in keyword
        noise = next((term for term in NOISE_TERMS if term in keyword), None)
        if japanese:
            reason = "日文地区噪声"
        elif noise and not accelerator:
            reason = f"低质量或非目标词：{noise}"
        elif not (direct or cross_border or related):
            reason = "与VPN/跨境网络关联不足"
        elif len(keyword) > 80 or keyword.startswith(("http://", "https://")):
            reason = "异常长度或URL"
        else:
            reason = ""

        source_count = len(item["sources"])
        long_tail = is_long_tail(keyword)
        score = 0
        score += 55 if direct else 0
        score += 42 if cross_border else 0
        score += 34 if related else 0
        score += min(20, source_count * 5)
        score += max(0, 8 - min(item["best_rank"], 8))
        score += 5 if long_tail else 0
        if any(term in keyword for term in ("游戏", "手游", "网游")):
            score -= 10
        # Product requirement: all accelerator queries remain actionable input,
        # even when they are broad, gaming-oriented or appear in one source.
        if accelerator and not japanese:
            score = max(score, 55)
        score = max(0, min(100, score))

        result = {
            "keyword": keyword,
            "topic": classify_topic(keyword),
            "intent": classify_intent(keyword),
            "priority": "A" if score >= 70 else "B" if score >= 55 else "C",
            "rule_relevance": score,
            "is_long_tail": long_tail,
            "platform_sensitive": any(term in keyword for term in SENSITIVE_TERMS),
            "source_count": source_count,
            "sources": "、".join(sorted(item["source_names"])),
            "seed_terms": "、".join(sorted(item["seeds"])),
            "seed_categories": "、".join(sorted(item["seed_categories"])),
            "best_source_rank": item["best_rank"],
            "raw_occurrences": item["raw_occurrences"],
            "variants": "、".join(sorted(item["variants"])),
            "recommended_for_kuaishou": (score >= 55 or accelerator) and not reason,
            "exclude_reason": reason,
        }
        if reason or score < 40:
            excluded.append(result)
        else:
            refined.append(result)

    refined.sort(
        key=lambda row: (
            {"A": 0, "B": 1, "C": 2}[row["priority"]],
            -row["source_count"],
            row["best_source_rank"],
            row["keyword"],
        )
    )
    excluded.sort(key=lambda row: (row["exclude_reason"], row["keyword"]))
    return refined, excluded


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=columns,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_kuaishou_tsv(path: Path, refined: list[dict[str, Any]]) -> int:
    """Write every refined keyword with its canonical Kuaishou search URL."""
    selected = list(refined)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "keyword_index",
        "keyword",
        "encoded_keyword",
        "search_url",
        "topic",
        "intent",
        "priority",
        "platform_sensitive",
        "recommended_for_kuaishou",
        "source_count",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=columns,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for index, row in enumerate(selected, 1):
            keyword = row["keyword"]
            encoded = quote(keyword, safe="")
            writer.writerow(
                {
                    "keyword_index": index,
                    "keyword": keyword,
                    "encoded_keyword": encoded,
                    "search_url": f"https://www.kuaishou.com/search/{encoded}",
                    "topic": row["topic"],
                    "intent": row["intent"],
                    "priority": row["priority"],
                    "platform_sensitive": str(bool(row["platform_sensitive"])).lower(),
                    "recommended_for_kuaishou": str(
                        bool(row["recommended_for_kuaishou"])
                    ).lower(),
                    "source_count": row["source_count"],
                }
            )
    return len(selected)


def refine_existing(output_dir: Path) -> dict[str, Any]:
    """Re-run cleaning rules against the previously downloaded raw CSV."""
    raw_path = output_dir / "vpn_keywords_raw.csv"
    with raw_path.open("r", encoding="utf-8-sig", newline="") as handle:
        raw_rows = list(csv.DictReader(handle))
    for row in raw_rows:
        row["rank"] = int(row.get("rank") or 0)
    dataset_path = output_dir / "vpn_keywords_dataset.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    _append_indexed_rows(raw_rows, dataset.get("generated_at", ""))
    refined, excluded = refine(raw_rows)
    columns = [
        "keyword",
        "topic",
        "intent",
        "priority",
        "rule_relevance",
        "is_long_tail",
        "platform_sensitive",
        "recommended_for_kuaishou",
        "source_count",
        "sources",
        "seed_terms",
        "seed_categories",
        "best_source_rank",
        "raw_occurrences",
        "variants",
        "exclude_reason",
    ]
    _write_csv(output_dir / "vpn_keywords_refined.csv", refined, columns)
    _write_csv(output_dir / "vpn_keywords_excluded.csv", excluded, columns)
    raw_columns = [
        "phase",
        "source",
        "source_name",
        "source_url",
        "seed",
        "seed_category",
        "rank",
        "keyword",
        "normalized",
        "collected_at",
    ]
    _write_csv(output_dir / "vpn_keywords_raw.csv", raw_rows, raw_columns)
    dataset["refined_count"] = len(refined)
    dataset["excluded_count"] = len(excluded)
    dataset["raw_row_count"] = len(raw_rows)
    dataset["source_count"] = len(SOURCE_INFO)
    dataset["sources"] = SOURCE_INFO
    dataset["refined"] = refined
    dataset["excluded"] = excluded
    dataset_path.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dataset


def _append_indexed_rows(raw_rows: list[dict[str, Any]], collected_at: str) -> None:
    existing = {(row.get("source"), row.get("keyword")) for row in raw_rows}
    for indexed in INDEXED_ROWS:
        key = (indexed["source"], indexed["keyword"])
        if key in existing:
            continue
        raw_rows.append({**indexed, "collected_at": collected_at})
        existing.add(key)


async def collect(output_dir: Path, expand_limit: int) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    }
    raw_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15) as client:
        semaphore = asyncio.Semaphore(6)

        async def run_specs(specs: list[RequestSpec], phase: str) -> None:
            results = await asyncio.gather(
                *[_fetch_one(client, semaphore, spec) for spec in specs]
            )
            for spec, keywords, error in results:
                if error:
                    errors.append(
                        {
                            "phase": phase,
                            "source": spec.source,
                            "seed": spec.seed,
                            "url": spec.url,
                            "error": error,
                        }
                    )
                for rank, keyword in enumerate(keywords, 1):
                    raw_rows.append(
                        {
                            "phase": phase,
                            "source": spec.source,
                            "source_name": SOURCE_INFO[spec.source]["name"],
                            "source_url": spec.url,
                            "seed": spec.seed,
                            "seed_category": spec.seed_category,
                            "rank": rank,
                            "keyword": keyword,
                            "collected_at": collected_at,
                        }
                    )

        await run_specs(_specs(SEEDS), "seed")

        # Query a bounded set of the best first-pass long tails again. This is
        # the equivalent of SEO deep mining while keeping request volume stable.
        candidate_sources: dict[str, set[str]] = defaultdict(set)
        candidate_ranks: dict[str, int] = {}
        for row in raw_rows:
            normalized = normalize_keyword(row["keyword"])
            if not normalized or normalized == normalize_keyword(row["seed"]):
                continue
            if not contains_any_topic_term(
                normalized, DIRECT_TERMS + CROSS_BORDER_TERMS + RELATED_TERMS
            ):
                continue
            candidate_sources[normalized].add(row["source"])
            candidate_ranks[normalized] = min(
                candidate_ranks.get(normalized, 999), int(row["rank"])
            )
        candidates = sorted(
            candidate_sources,
            key=lambda word: (
                -len(candidate_sources[word]),
                candidate_ranks[word],
                -len(word),
                word,
            ),
        )[:expand_limit]
        expanded_seeds = [(word, "二级扩展") for word in candidates]
        await run_specs(_specs(expanded_seeds), "expanded")

    # The seed list itself is an auditable project input rather than a measured
    # suggestion. Keeping it in Raw prevents useful business-adjacent terms from
    # disappearing when an engine returns no autocomplete rows.
    for seed, category in SEEDS:
        from_video_sample = seed in VIDEO_DERIVED_WORDS
        raw_rows.append(
            {
                "phase": "input",
                "source": "kuaishou_sample" if from_video_sample else "project_seed",
                "source_name": "快手视频样本词根" if from_video_sample else "项目种子词",
                "source_url": "快手视频链接.xlsx" if from_video_sample else "",
                "seed": seed,
                "seed_category": category,
                "rank": 0,
                "keyword": seed,
                "collected_at": collected_at,
            }
        )

    _append_indexed_rows(raw_rows, collected_at)

    refined, excluded = refine(raw_rows)
    raw_columns = [
        "phase",
        "source",
        "source_name",
        "source_url",
        "seed",
        "seed_category",
        "rank",
        "keyword",
        "normalized",
        "collected_at",
    ]
    refined_columns = [
        "keyword",
        "topic",
        "intent",
        "priority",
        "rule_relevance",
        "is_long_tail",
        "platform_sensitive",
        "recommended_for_kuaishou",
        "source_count",
        "sources",
        "seed_terms",
        "seed_categories",
        "best_source_rank",
        "raw_occurrences",
        "variants",
        "exclude_reason",
    ]
    _write_csv(output_dir / "vpn_keywords_raw.csv", raw_rows, raw_columns)
    _write_csv(output_dir / "vpn_keywords_refined.csv", refined, refined_columns)
    _write_csv(output_dir / "vpn_keywords_excluded.csv", excluded, refined_columns)
    (output_dir / "errors.json").write_text(
        json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    dataset = {
        "generated_at": collected_at,
        "seed_count": len(SEEDS),
        "raw_row_count": len(raw_rows),
        "refined_count": len(refined),
        "excluded_count": len(excluded),
        "source_count": len(SOURCE_INFO),
        "sources": SOURCE_INFO,
        "seeds": [{"keyword": word, "category": category} for word, category in SEEDS],
        "refined": refined,
        "excluded": excluded,
        "errors": errors,
    }
    (output_dir / "vpn_keywords_dataset.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dataset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("research/seo/vpn-20260908"),
    )
    parser.add_argument("--expand-limit", type=int, default=60)
    parser.add_argument("--refine-only", action="store_true")
    parser.add_argument(
        "--kuaishou-tsv",
        type=Path,
        default=Path("resources/keywords/vpn_kuaishou_search_keywords.tsv"),
    )
    args = parser.parse_args()
    dataset = (
        refine_existing(args.output_dir)
        if args.refine_only
        else asyncio.run(collect(args.output_dir, max(0, args.expand_limit)))
    )
    tsv_count = write_kuaishou_tsv(args.kuaishou_tsv, dataset["refined"])
    print(
        json.dumps(
            {
                key: dataset[key]
                for key in (
                    "generated_at",
                    "seed_count",
                    "raw_row_count",
                    "refined_count",
                    "excluded_count",
                    "source_count",
                )
            }
            | {"kuaishou_tsv": str(args.kuaishou_tsv), "tsv_keyword_count": tsv_count},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
