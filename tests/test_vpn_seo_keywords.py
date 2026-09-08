from scripts.collect_vpn_seo_keywords import (
    classify_intent,
    normalize_keyword,
    refine,
)


def test_keyword_normalization():
    assert normalize_keyword(" ＶＰＮ  软件 ") == "vpn软件"
    assert normalize_keyword("Open VPN 配置") == "openvpn配置"
    assert normalize_keyword("Wire Guard 教程") == "wireguard教程"


def test_intent_classification():
    assert classify_intent("企业vpn远程办公") == "企业办公"
    assert classify_intent("vpn怎么配置") == "配置排障"
    assert classify_intent("vpn软件下载") == "下载安装"


def test_refine_deduplicates_sources_and_excludes_noise():
    rows = [
        {
            "source": "google_web",
            "source_name": "Google Web 联想",
            "seed": "vpn",
            "seed_category": "VPN核心",
            "rank": 1,
            "keyword": "VPN 软件",
        },
        {
            "source": "bing_cn",
            "source_name": "Bing 中文联想",
            "seed": "vpn软件",
            "seed_category": "VPN产品",
            "rank": 2,
            "keyword": "vpn软件",
        },
        {
            "source": "bing_cn",
            "source_name": "Bing 中文联想",
            "seed": "vpn",
            "seed_category": "VPN核心",
            "rank": 3,
            "keyword": "vpnとは",
        },
    ]
    refined, excluded = refine(rows)
    assert len(refined) == 1
    assert refined[0]["keyword"] == "vpn软件"
    assert refined[0]["source_count"] == 2
    assert excluded[0]["exclude_reason"] == "日文地区噪声"


def test_all_accelerator_terms_are_retained():
    rows = [
        {
            "source": "baidu",
            "source_name": "百度联想",
            "seed": "游戏加速器",
            "seed_category": "游戏加速",
            "rank": 10,
            "keyword": "游戏加速器下载",
        },
        {
            "source": "google_web",
            "source_name": "Google Web 联想",
            "seed": "网络加速器",
            "seed_category": "网络加速",
            "rank": 8,
            "keyword": "网络加速器破解版",
        },
    ]
    refined, excluded = refine(rows)
    assert {row["keyword"] for row in refined} == {
        "游戏加速器下载",
        "网络加速器破解版",
    }
    assert excluded == []
    assert all(row["recommended_for_kuaishou"] for row in refined)
