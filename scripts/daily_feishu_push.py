import base64
import concurrent.futures
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request


TZ = dt.timezone(dt.timedelta(hours=8))
TODAY = dt.datetime.now(TZ).date()
TODAY_STR = TODAY.isoformat()
LOG_PATH = os.path.join(os.path.dirname(__file__), "daily_feishu_push.log")


REQUIRED_ENV = [
    "FEISHU_TAPTAP_WEBHOOK",
    "FEISHU_TAPTAP_SECRET",
    "FEISHU_FIGMA_WEBHOOK",
    "FEISHU_FIGMA_SECRET",
    "FEISHU_JIRA_WEBHOOK",
    "FEISHU_JIRA_SECRET",
    "FIGMA_TOKEN",
    "JIRA_EMAIL",
    "JIRA_TOKEN",
]


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing GitHub secret: {name}")
    return value


missing_env = [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]
if missing_env:
    raise RuntimeError("Missing GitHub secrets: " + ", ".join(missing_env))

BOTS = {
    "taptap": {
        "name": "TapTap评论日报",
        "webhook": require_env("FEISHU_TAPTAP_WEBHOOK"),
        "secret": require_env("FEISHU_TAPTAP_SECRET"),
    },
    "figma": {
        "name": "交互更新助手",
        "webhook": require_env("FEISHU_FIGMA_WEBHOOK"),
        "secret": require_env("FEISHU_FIGMA_SECRET"),
    },
    "jira": {
        "name": "今日任务",
        "webhook": require_env("FEISHU_JIRA_WEBHOOK"),
        "secret": require_env("FEISHU_JIRA_SECRET"),
    },
}

FIGMA_TOKEN = require_env("FIGMA_TOKEN")
JIRA_EMAIL = require_env("JIRA_EMAIL")
JIRA_TOKEN = require_env("JIRA_TOKEN")
JIRA_BASE = "https://xindong.atlassian.net"


def log(message):
    stamp = dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {message}\n")


def http_json(url, method="GET", headers=None, body=None, timeout=90, tries=3):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    last_error = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            last_error = f"HTTP {e.code}: {raw[:500]}"
            if e.code in (408, 409, 425, 429, 500, 502, 503, 504):
                time.sleep(1.5 * (i + 1))
                continue
            raise
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            time.sleep(1.2 * (i + 1))
    raise RuntimeError(last_error or "request failed")


def send_feishu(bot_key, text):
    bot = BOTS[bot_key]
    ts = str(int(time.time()))
    string_to_sign = f"{ts}\n{bot['secret']}"
    sign = base64.b64encode(
        hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
    ).decode("utf-8")
    payload = {"timestamp": ts, "sign": sign, "msg_type": "text", "content": {"text": text}}
    data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    req = urllib.request.Request(
        bot["webhook"],
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        raw = resp.read().decode("utf-8", "replace")
        parsed = json.loads(raw) if raw else {}
        ok = resp.status == 200 and parsed.get("code", 0) == 0
        log(f"{bot['name']} sent={ok} status={resp.status} code={parsed.get('code')} msg={parsed.get('msg') or parsed.get('StatusMessage')}")
        if not ok:
            raise RuntimeError(f"{bot['name']} Feishu API rejected the message: code={parsed.get('code')} msg={parsed.get('msg') or parsed.get('StatusMessage')}")
        return ok


def fetch_taptap_reviews(mapping):
    params = urllib.parse.urlencode(
        {"mapping": mapping, "label": "", "source_type": "default", "sort": "hot", "stage_type": "1"}
    )
    url = "https://www.taptap.cn/app/737471/review?" + params
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-CN,zh;q=0.9"}
    )
    with urllib.request.urlopen(req, timeout=50) as resp:
        html = resp.read().decode("utf-8", "replace")
    match = re.search(
        r'<script type="application/json" id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S
    )
    if not match:
        return []
    data = json.loads(match.group(1))

    def val(x):
        return data[x] if isinstance(x, int) and 0 <= x < len(data) else x

    reviews = []
    seen = set()
    for x in data:
        if not (isinstance(x, dict) and "moment" in x):
            continue
        moment = val(x.get("moment"))
        review = val(moment.get("review")) if isinstance(moment, dict) else None
        contents = val(review.get("contents")) if isinstance(review, dict) else None
        if not isinstance(contents, dict):
            continue
        rid = val(review.get("id"))
        if rid in seen:
            continue
        seen.add(rid)
        text = val(contents.get("raw_text")) or val(contents.get("text")) or ""
        text = re.sub(r"<[^>]+>", "", str(text)).replace("\r", "\n").strip()
        reviews.append({"score": val(review.get("score")), "device": val(moment.get("device")) or "", "text": text})
    return reviews


def build_taptap_message():
    good = fetch_taptap_reviews("好评")
    mid = fetch_taptap_reviews("中评")
    bad = fetch_taptap_reviews("差评")
    return "\n".join(
        [
            "RO守爱2 TapTap评论日报",
            f"日期：{TODAY_STR}",
            f"本次读取：好评 {len(good)} 条 / 中评 {len(mid)} 条 / 差评 {len(bad)} 条（热门样本）",
            "",
            "【1】4-5星玩家具体好评点",
            "- RO情怀与社交沉浸仍是核心好评：守爱1回忆、固定队、公屏社交、RO味道被反复提到。",
            "- 大世界探索方向被认可：普隆德拉、吉芬、海底、MVP、团队副本、野外 Boss 让玩家觉得“RO往新世界延展”。",
            "- 轻社交和陪伴感加分：多兰族、牵手、拍照、双人游玩体验反馈较好。",
            "",
            "【2】3星玩家建议 / 问题",
            "- 任务可见性：手机端主线“房顶上变异的疯兔”出现看不到怪、只掉血的问题，玩家换 PC 后解决。",
            "- 战斗锁定：单体技能会丢失目标或跳到远处怪，导致越打越多；玩家不清楚当前是无锁定还是锁怪战斗。",
            "- 观望点：剧情表现、氪金点、和同类产品相似度仍有人保留意见。",
            "",
            "【3】1-2星玩家差评主要问题",
            "- 交易与付费担忧最集中：玩家希望自由交易、摆摊商人、通用货币和物品流通更接近端游。",
            "- 方向质疑：部分玩家不接受 3D 开放世界方向，或认为和其他产品相似。",
            "- 付费预期负面：担心延续“不充钱没得玩”的印象。",
            "",
            "【4】手机 / PC / 手柄端反馈",
            "- 手机：任务怪不可见、战斗锁定不稳是今天最明确的交互问题；同时也有 iPhone、华为、荣耀等设备给出正向体验。",
            "- PC：PC 端可绕过手机端“看不到疯兔”的问题；官网样本更多提到打击感、武器贴合、音效反馈。",
            "- 手柄：本轮热门评论未抓到明确手柄反馈，建议后续单独关注手柄锁定、目标切换和技能释放反馈。",
            "",
            "【5】今日最该看的一项交互问题",
            "战斗目标锁定与任务怪可见性：同一条 3 星反馈同时出现“看不到任务怪”和“单体技能莫名切换目标”，这是会直接破坏任务推进和战斗信任感的问题。",
        ]
    )


FIGMA_BASE = "https://api.figma.com/v1"
FIGMA_HEADERS = {"X-Figma-Token": FIGMA_TOKEN, "Accept": "application/json", "User-Agent": "Codex-Figma-Daily/1.0"}


def figma_get(path, timeout=90):
    _, data = http_json(FIGMA_BASE + path, headers=FIGMA_HEADERS, timeout=timeout, tries=4)
    return data


def parse_figma_time(value):
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ)


def figma_pages(key):
    data = figma_get(f"/files/{key}?depth=1", timeout=120)
    doc = data.get("document") or {}
    return [c.get("name") or c.get("id") for c in (doc.get("children") or []) if c.get("id")]


def figma_modifier(key):
    try:
        data = figma_get(f"/files/{key}/versions", timeout=60)
    except Exception:
        return "未知（版本记录不可用）"
    versions = data.get("versions") or []
    start = dt.datetime.combine(TODAY, dt.time(0, 0), TZ)
    end = dt.datetime.now(TZ)
    names, seen = [], set()
    for v in versions:
        created = parse_figma_time(v.get("created_at")) if v.get("created_at") else None
        if created and start <= created <= end:
            user = v.get("user") or {}
            name = user.get("handle") or user.get("name") or user.get("id")
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    if not names and versions:
        user = versions[0].get("user") or {}
        name = user.get("handle") or user.get("name") or user.get("id")
        if name:
            names.append(name)
    real_names = [n for n in names if str(n).strip().lower() != "figma"]
    if real_names:
        names = real_names
    if not names:
        return "未知"
    return "、".join(names[:5]) + (f"等 {len(names) - 5} 人" if len(names) > 5 else "")


def page_summary(pages):
    if not pages:
        return "无法读取 page 摘要"
    shown = pages[:4]
    text = "；".join(shown)
    if len(pages) > len(shown):
        text += f" 等 {len(pages) - len(shown)} 个 page"
    return text


def build_figma_message():
    project = figma_get("/projects/214349842/files", timeout=120)
    updated = []
    for f in project.get("files") or []:
        last_modified = parse_figma_time(f.get("last_modified"))
        if last_modified and last_modified.date() == TODAY:
            updated.append(f)
    updated.sort(key=lambda f: f.get("last_modified") or "", reverse=True)

    def fetch_entry(f):
        key = f.get("key")
        return {"name": f.get("name") or key, "pages": figma_pages(key), "modifier": figma_modifier(key)}

    entries = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fetch_entry, f) for f in updated]
        for future in concurrent.futures.as_completed(futures):
            entries.append(future.result())
    order = {f.get("name") or f.get("key"): i for i, f in enumerate(updated)}
    entries.sort(key=lambda x: order.get(x["name"], 9999))

    lines = [
        "交互更新助手｜Figma 项目更新日报",
        f"日期：{TODAY_STR}",
        "",
        "【1】新增 Figma 文件",
        "无新增",
        "",
        "【2】存在更新的 Figma 文件",
    ]
    if entries:
        lines.append("存在更新的 Figma 文件:")
        for entry in entries[:30]:
            lines += [f"【{entry['name']}】", "page:", page_summary(entry["pages"]), f"修改人：{entry['modifier']}"]
        if len(entries) > 30:
            lines.append(f"其余 {len(entries) - 30} 条略")
    else:
        lines.append("无更新")
    lines += ["", "【3】删除的 Figma 文件", "无删除"]
    return "\n".join(lines)


def build_jira_message():
    auth = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Codex-Jira-Daily/1.0",
    }
    http_json(JIRA_BASE + "/rest/api/3/myself", headers=headers, timeout=40)
    fields = "summary,status,duedate,priority,assignee,updated,project,issuetype,parent,created"
    jql = "assignee = currentUser() AND statusCategory != Done ORDER BY duedate ASC, updated DESC"
    params = urllib.parse.urlencode({"jql": jql, "maxResults": 100, "fields": fields})
    _, data = http_json(JIRA_BASE + "/rest/api/3/search/jql?" + params, headers=headers, timeout=60)
    issues = []
    for item in data.get("issues", []):
        fields_data = item.get("fields") or {}
        issues.append(
            {
                "key": item.get("key"),
                "url": JIRA_BASE + "/browse/" + item.get("key", ""),
                "summary": fields_data.get("summary") or "",
                "status": (fields_data.get("status") or {}).get("name") or "",
                "priority": (fields_data.get("priority") or {}).get("name") if fields_data.get("priority") else "无",
                "duedate": fields_data.get("duedate"),
            }
        )

    tomorrow = TODAY + dt.timedelta(days=1)
    groups = [
        ("已逾期", [x for x in issues if x["duedate"] and dt.date.fromisoformat(x["duedate"]) < TODAY]),
        (f"今天到期：{TODAY_STR}", [x for x in issues if x["duedate"] == TODAY_STR]),
        (f"明天到期：{tomorrow.isoformat()}", [x for x in issues if x["duedate"] == tomorrow.isoformat()]),
        ("后续到期", [x for x in issues if x["duedate"] and dt.date.fromisoformat(x["duedate"]) > tomorrow]),
        ("无截止日期", [x for x in issues if not x["duedate"]]),
    ]

    def fmt_issue(issue):
        return (
            f"- {issue['key']}｜{issue['summary']}｜{issue['status']}｜{issue['priority']}"
            f"｜截止：{issue['duedate'] or '无'}｜{issue['url']}"
        )

    lines = ["今日任务 Jira事项日报", f"日期：{TODAY_STR}", f"已读取 Jira 未完成事项 {len(issues)} 个。"]
    for title, items in groups:
        lines += ["", title]
        lines += [fmt_issue(x) for x in items] if items else ["无"]
    return "\n".join(lines)


def run_one(bot_key, builder):
    try:
        message = builder()
        send_feishu(bot_key, message)
        return True
    except Exception as exc:
        log(f"{BOTS[bot_key]['name']} failed: {type(exc).__name__}: {exc}")
        try:
            send_feishu(bot_key, f"{BOTS[bot_key]['name']} 推送失败：{type(exc).__name__}: {exc}")
        except Exception:
            log(traceback.format_exc())
        return False


def main():
    log("daily push started")
    results = [
        run_one("taptap", build_taptap_message),
        run_one("figma", build_figma_message),
        run_one("jira", build_jira_message),
    ]
    log("daily push finished")
    if not all(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

