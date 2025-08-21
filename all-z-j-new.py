import re
import csv
import requests
import concurrent.futures
import asyncio
import aiohttp
import argparse
from queue import Queue
import threading
import os

# 校验URL是否为标准IPTV(m3u8)源
def is_valid_iptv_url(url):
    if not re.match(r'^https?://.+\.m3u8(\?.*)?$', url):
        return False
    try:
        resp = requests.get(url, timeout=2)
        if resp.status_code != 200:
            return False
        # 检查m3u8基本格式
        lines = resp.text.split('\n')
        if not lines or not lines[0].startswith('#EXTM3U'):
            return False
        ts_count = sum(1 for line in lines if line and not line.startswith('#') and line.endswith('.ts'))
        # 至少有一个ts片段
        if ts_count < 1:
            return False
        return True
    except Exception:
        return False

# 下载ts片段并校验为MPEG-TS
def validate_ts_segment(ts_url):
    try:
        r = requests.get(ts_url, timeout=4)
        if r.status_code == 200 and r.content and r.content[0] == 0x47:
            return True
    except Exception:
        pass
    return False

# 频道名称归一化
def channel_name_normalize(name):
    for rep in ["高清", "超高", "HD", "标清", "频道", "-", " ", "PLUS", "＋", "(", ")"]:
        name = name.replace(rep, "" if rep not in ["PLUS", "＋"] else "+")
    name = re.sub(r"CCTV(\d+)台", r"CCTV\1", name)
    name_map = {
        "CCTV1综合": "CCTV1", "CCTV2财经": "CCTV2", "CCTV3综艺": "CCTV3",
        "CCTV4国际": "CCTV4", "CCTV4中文国际": "CCTV4", "CCTV4欧洲": "CCTV4",
        "CCTV5体育": "CCTV5", "CCTV6电影": "CCTV6", "CCTV7军事": "CCTV7",
        "CCTV7军农": "CCTV7", "CCTV7农业": "CCTV7", "CCTV7国防军事": "CCTV7",
        "CCTV8电视剧": "CCTV8", "CCTV9记录": "CCTV9", "CCTV9纪录": "CCTV9",
        "CCTV10科教": "CCTV10", "CCTV11戏曲": "CCTV11", "CCTV12社会与法": "CCTV12",
        "CCTV13新闻": "CCTV13", "CCTV新闻": "CCTV13", "CCTV14少儿": "CCTV14",
        "CCTV15音乐": "CCTV15", "CCTV16奥林匹克": "CCTV16",
        "CCTV17农业农村": "CCTV17", "CCTV17农业": "CCTV17",
        "CCTV5+体育赛视": "CCTV5+", "CCTV5+体育赛事": "CCTV5+", "CCTV5+体育": "CCTV5+"
    }
    name = name_map.get(name, name)
    return name

# 采集阶段：从csv读入、采集URL（仅支持csv格式，每行有name/url字段）
def collect_channels_from_csv(csv_file):
    channels = []
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get('name') or row.get('channel') or row.get('title') or ""
            url = row.get('url') or row.get('link') or row.get('host') or ""
            if name and url and url.startswith('http'):
                channels.append((channel_name_normalize(name.strip()), url.strip()))
    return channels

# 校验+初步测速阶段（多线程）
def validate_and_speedtest(channels):
    results = []
    lock = threading.Lock()
    def worker(channel):
        name, url = channel
        if not is_valid_iptv_url(url):
            return
        # 获取第一个ts片段
        try:
            m3u8 = requests.get(url, timeout=2).text
            ts_lines = [line for line in m3u8.split('\n') if line and not line.startswith('#') and line.endswith('.ts')]
            if not ts_lines:
                return
            ts_url = ts_lines[0] if ts_lines[0].startswith('http') else url.rsplit('/', 1)[0] + '/' + ts_lines[0]
            # 测速
            start = os.times()[4]
            if not validate_ts_segment(ts_url):
                return
            elapsed = max(os.times()[4] - start, 0.01)
            size = len(requests.get(ts_url, timeout=3).content)
            speed = size / elapsed / 1024
            with lock:
                results.append((name, url, "{:.3f} KB/s".format(speed)))
        except Exception:
            return
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        list(executor.map(worker, channels))
    return results

# 再次输出前过滤（去重、去低速、同名只留前n个）
def filter_and_sort_results(results, max_per_channel=5, min_speed_kb=30.0):
    from collections import defaultdict
    channel_map = defaultdict(list)
    for name, url, speed_str in results:
        speed = float(speed_str.split()[0])
        if speed >= min_speed_kb:
            channel_map[name].append((url, speed))
    final = []
    for name, items in channel_map.items():
        items = sorted(items, key=lambda x: x[1], reverse=True)[:max_per_channel]
        for url, speed in items:
            final.append((name, url, "{:.3f} KB/s".format(speed)))
    # CCTV前，卫视次，其他后
    def sort_key(x):
        name = x[0]
        if name.startswith('CCTV'):
            n = re.search(r'\d+', name)
            return (0, int(n.group()) if n else 0)
        if '卫视' in name:
            return (1, name)
        return (2, name)
    final.sort(key=sort_key)
    return final

# 输出
def write_output(results, prefix="iptvlist"):
    # TXT
    with open(f"{prefix}.txt", "w", encoding="utf-8") as f:
        for name, url, speed in results:
            f.write(f"{name},{url},{speed}\n")
    # M3U
    with open(f"{prefix}.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for name, url, speed in results:
            f.write(f'#EXTINF:-1 group-title="IPTV" tvg-name="{name}" tvg-logo="",{name}\n')
            f.write(f"{url}\n")
    # 速度统计
    with open(f"{prefix}_speed.txt", "w", encoding="utf-8") as f:
        for name, url, speed in results:
            f.write(f"{name},{url},{speed}\n")

# 主流程
def main():
    parser = argparse.ArgumentParser(description="IPTV源采集+测速+校验一体化工具")
    parser.add_argument("--csv", required=True, help="输入频道csv文件，需包含name/url字段")
    parser.add_argument("--output", default="iptvlist", help="输出文件前缀")
    args = parser.parse_args()
    print(">>> 正在采集频道...")
    channels = collect_channels_from_csv(args.csv)
    print(">>> 频道总数:", len(channels))
    print(">>> 正在校验与测速...")
    valid_results = validate_and_speedtest(channels)
    print(">>> 初步有效源数:", len(valid_results))
    print(">>> 输出前过滤与去重...")
    final_results = filter_and_sort_results(valid_results)
    print(">>> 最终可用频道数:", len(final_results))
    write_output(final_results, args.output)
    print(">>> 完成！输出文件：", args.output + ".txt", args.output + ".m3u")

if __name__ == "__main__":
    main()
