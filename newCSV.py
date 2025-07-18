import re
import csv
import requests
import concurrent.futures
import asyncio
import aiohttp
import os
import threading
from queue import Queue
import argparse
from collections import defaultdict

# 频道名称智能识别类
class ChannelNameClassifier:
    def __init__(self):
        # 基础频道名称映射表
        self.base_patterns = {
            r'^CCTV\s*(\d+)\s*[+＋]?': r'CCTV\1+',
            r'^CCTV\s*(\d+)': r'CCTV\1',
            r'^(\w+卫视)': r'\1',
            r'^(\w+新闻)': r'\1',
            r'^(\w+电影)': r'\1',
            r'^(\w+体育)': r'\1',
            r'^(\w+少儿)': r'\1',
            r'^(\w+音乐)': r'\1',
        }
        # 常见干扰词，用于去除频道名称中的冗余信息
        self.interference_words = [
            "高清", "超清", "标清", "HD", "SD", "4K", "测试", "TV", "频道", 
            "卫视", "中央", "电视台", "中国", "网络", "直播", "官网",
            "官方", "CN", "China", "Plus", "＋", "+", "(", ")", "【", "】",
            "-", "_", " ", "/"
        ]
    
    def normalize(self, name):
        # 移除干扰词
        for word in self.interference_words:
            name = name.replace(word, "")
        # 转换为大写
        name = name.upper()
        # 应用基础模式匹配
        for pattern, repl in self.base_patterns.items():
            if re.match(pattern, name):
                return re.sub(pattern, repl, name)
        # 如果没有匹配到任何模式，返回原始名称（已清理干扰词）
        return name

# 归一化频道名称
def channel_name_normalize(name):
    classifier = ChannelNameClassifier()
    return classifier.normalize(name)

# 获取频道名称中的数字
def channel_key(channel_name):
    match = re.search(r'\d+', channel_name)
    if match:
        return int(match.group())
    return float('inf')

# 生成同一C段的所有IP的URL
def generate_ip_range_urls(base_url, ip_address, port, suffix=None):
    ip_parts = ip_address.split('.')
    if len(ip_parts) < 3:
        return []
    c_prefix = '.'.join(ip_parts[:3])
    return [f"{base_url}{c_prefix}.{i}{port}{suffix if suffix else ''}" for i in range(1, 256)]

# 固定并发数，移除对psutil的依赖
def adjust_concurrency():
    return 100  # 使用固定的默认并发数

# 增加超时重试机制
def is_url_accessible(url, retries=3):
    for _ in range(retries):
        try:
            response = requests.get(url, timeout=1)
            return url if response.status_code == 200 else None
        except requests.RequestException:
            continue
    return None

# 并发检测URL可用性
def check_urls_concurrent(urls, timeout=1, print_valid=True):
    max_workers = adjust_concurrency()

    def check_url(url):
        return is_url_accessible(url)

    valid_urls = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(check_url, url) for url in urls]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                valid_urls.append(result)
                if print_valid:
                    print(result)
    return valid_urls

# 替换为CDN地址
def replace_with_cdn(url):
    # 这里只是示例，实际需要根据具体的CDN服务进行替换
    cdn_base = "https://cdn.example.com"
    return url.replace("http://original-server.com", cdn_base)

# jsmpeg模式获取频道
def get_channels_alltv(csv_file):
    urls = set()
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if 'host' not in reader.fieldnames:
            raise ValueError('CSV文件缺少host列')
        for row in reader:
            host = row['host'].strip()
            if host:
                urls.add(host if host.startswith(('http://', 'https://')) else f"http://{host}")

    ip_range_urls = []
    for url in urls:
        ip_start = url.find("//") + 2
        ip_end = url.find(":", ip_start)
        base_url = url[:ip_start]
        ip_address = url[ip_start:ip_end]
        port = url[ip_end:]
        ip_range_urls.extend(generate_ip_range_urls(base_url, ip_address, port))

    valid_urls = check_urls_concurrent(set(ip_range_urls))
    channels = []
    for url in valid_urls:
        json_url = f"{url.rstrip('/')}/streamer/list"
        try:
            json_data = requests.get(json_url, timeout=1).json()
            host = url.rstrip('/')
            for item in json_data:
                name = item.get('name', '').strip()
                key = item.get('key', '').strip()
                if name and key:
                    channel_url = f"{host}/hls/{key}/index.m3u8"
                    channel_url = replace_with_cdn(channel_url)
                    channels.append((channel_name_normalize(name), channel_url))
        except Exception:
            continue
    return channels

# txiptv模式获取频道（异步）
async def get_channels_newnew(csv_file):
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        urls = list(set(row.get('link', '').strip() for row in reader if row.get('link')))

    async def modify_urls(url):
        ip_start = url.find("//") + 2
        ip_end = url.find(":", ip_start)
        base_url = url[:ip_start]
        ip_address = url[ip_start:ip_end]
        ip_parts = ip_address.split('.')
        if len(ip_parts) < 3:
            return []
        c_prefix = '.'.join(ip_parts[:3])
        port = url[ip_end:]
        ip_end = "/iptv/live/1000.json?key=txiptv"
        return [f"{base_url}{c_prefix}.{i}{port}{ip_end}" for i in range(1, 256)]

    async def is_url_accessible(session, url, semaphore):
        async with semaphore:
            try:
                async with session.get(url, timeout=1) as response:
                    return url if response.status == 200 else None
            except (aiohttp.ClientError, asyncio.TimeoutError):
                return None

    async def check_urls(session, urls, semaphore):
        tasks = []
        for url in urls:
            modified_urls = await modify_urls(url)
            tasks.extend(asyncio.create_task(is_url_accessible(session, modified_url, semaphore)) for modified_url in modified_urls)
        results = await asyncio.gather(*tasks)
        valid_urls = [result for result in results if result]
        for url in valid_urls:
            print(url)
        return valid_urls

    async def fetch_json(session, url, semaphore):
        async with semaphore:
            try:
                ip_start = url.find("//") + 2
                ip_index = url.find("/", url.find(".") + 1)
                base_url = url[:ip_start]
                ip_address = url[ip_start:ip_index]
                url_x = f"{base_url}{ip_address}"
                json_data = await session.get(url, timeout=1).json()
                channels = []
                for item in json_data.get('data', []):
                    if isinstance(item, dict):
                        name = item.get('name')
                        urlx = item.get('url')
                        if ',' in urlx:
                            urlx = "aaaaaaaa"
                        urld = urlx if 'http' in urlx else f"{url_x}{urlx}"
                        urld = replace_with_cdn(urld)
                        if name and urlx:
                            channels.append((channel_name_normalize(name), urld))
                return channels
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                return []

    x_urls = []
    for url in urls:
        ip_start = url.find("//") + 2
        ip_end = url.find(":", ip_start)
        ip_dot = url.find(".") + 1
        ip_address = url[ip_start:url.find(".", ip_dot, url.find(".", ip_dot + 1)) + 1]
        port = url[ip_end:]
        x_urls.append(f"{url[:ip_start]}{ip_address}1{port}")

    unique_urls = set(x_urls)
    semaphore = asyncio.Semaphore(500)
    async with aiohttp.ClientSession() as session:
        valid_urls = await check_urls(session, unique_urls, semaphore)
        tasks = [asyncio.create_task(fetch_json(session, url, semaphore)) for url in valid_urls]
        results = await asyncio.gather(*tasks)
        return [channel for sublist in results for channel in sublist]

# zhgxtv模式获取频道
def get_channels_hgxtv(csv_file):
    urls = set()
    with open(csv_file, 'r', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            host = row['host'].strip()
            if host:
                url = host if host.startswith(('http://', 'https://')) else f"http://{host}{':80' if ':' not in host else ''}"
                urls.add(url)

    ip_range_urls = []
    for url in urls:
        ip_start = url.find("//") + 2
        ip_end = url.find(":", ip_start)
        base_url = url[:ip_start]
        ip_address = url[ip_start:ip_end]
        port = url[ip_end:]
        ip_range_urls.extend(generate_ip_range_urls(base_url, ip_address, port, "/ZHGXTV/Public/json/live_interface.txt"))

    valid_urls = check_urls_concurrent(set(ip_range_urls))
    channels = []
    for url in valid_urls:
        try:
            json_data = requests.get(url, timeout=1).content.decode('utf-8')
            for line in json_data.split('\n'):
                line = line.strip()
                if line:
                    name, channel_url = line.split(',')
                    urls_parts = channel_url.split('/', 3)
                    url_data_parts = url.split('/', 3)
                    urld = f"{urls_parts[0]}//{url_data_parts[2]}/{urls_parts[3]}" if len(urls_parts) >= 4 else f"{urls_parts[0]}//{url_data_parts[2]}"
                    urld = replace_with_cdn(urld)
                    channels.append((channel_name_normalize(name), urld))
        except:
            continue
    return channels

# 测试频道速度并输出结果
def test_speed_and_output(channels, output_prefix="itvlist"):
    task_queue = Queue()
    speed_results = []
    error_channels = []

    def worker():
        while True:
            channel_name, channel_url = task_queue.get()
            total_speed = 0
            valid_tests = 0
            for _ in range(3):  # 进行3次测试
                try:
                    channel_url_t = channel_url.rstrip(channel_url.split('/')[-1])
                    lines = requests.get(channel_url, timeout=1).text.strip().split('\n')
                    ts_lists = [line for line in lines if not line.startswith('#')]
                    if not ts_lists:
                        raise Exception("No valid TS files found.")
                    ts_url = channel_url_t + ts_lists[0].split('/')[-1]
                    start_time = os.times()[0]
                    content = requests.get(ts_url, timeout=5).content
                    end_time = os.times()[0]
                    response_time = end_time - start_time
                    if content:
                        file_size = len(content)
                        download_speed = file_size / response_time / 1024
                        total_speed += download_speed
                        valid_tests += 1
                except:
                    continue
            if valid_tests > 0:
                average_speed = total_speed / valid_tests
                normalized_speed = min(max(average_speed / 1024, 0.001), 100)
                speed_results.append((channel_name, channel_url, f"{normalized_speed:.3f} MB/s"))
            else:
                error_channels.append((channel_name, channel_url))
            finally:
                progress = (len(speed_results) + len(error_channels)) / len(channels) * 100
                print(f"可用频道：{len(speed_results)} 个 , 不可用频道：{len(error_channels)} 个 , 总频道：{len(channels)} 个 ,总进度：{progress:.2f} %。")
                task_queue.task_done()

    num_threads = 50
    for _ in range(num_threads):
        threading.Thread(target=worker, daemon=True).start()

    for channel in channels:
        task_queue.put(channel)
    task_queue.join()

    # 按速度排序并筛选每个频道最多10个源
    from collections import defaultdict
    channel_sources = defaultdict(list)
    for channel_name, channel_url, speed in speed_results:
        channel_sources[channel_name].append((channel_url, speed))

    optimized_sources = []
    for channel_name, sources in channel_sources.items():
        sorted_sources = sorted(sources, key=lambda x: float(x[1].split()[0]), reverse=True)[:10]  # 保留最多10个源
        for url, speed in sorted_sources:
            optimized_sources.append((channel_name, url, speed))

    # 去重
    unique_channels = []
    seen = set()
    for channel_name, channel_url, speed in optimized_sources:
        key = (channel_name, channel_url)
        if key not in seen:
            unique_channels.append((channel_name, channel_url, speed))
            seen.add(key)

    # 对频道进行排序
    def custom_sort_key(item):
        name = item[0]
        if name.startswith('CCTV'):
            num = re.search(r'\d+', name)
            if num:
                return (0, int(num.group()))
            return (0, float('inf'))
        return (1, name)

    unique_channels.sort(key=custom_sort_key)

    def write_to_file(file, results, genre):
        channel_counters = {}
        for result in results:
            channel_name, channel_url, _ = result
            # 改进的分类规则
            if genre == '央视频道' and 'CCTV' in channel_name:
                pass
            elif genre == '卫视频道' and any(keyword in channel_name for keyword in ['卫视', 'TV']):
                pass
            elif genre == '国际频道' and any(keyword in channel_name for keyword in ['BBC', 'CNN', 'NHK', 'FOX', 'DW', 'RT']):
                pass
            elif genre == '体育频道' and any(keyword in channel_name for keyword in ['体育', '足球', '篮球', 'F1', 'NBA', 'NHL', 'MLB']):
                pass
            elif genre == '电影频道' and any(keyword in channel_name for keyword in ['电影', 'MOVIE']):
                pass
            elif genre == '少儿频道' and any(keyword in channel_name for keyword in ['少儿', '动画', 'KIDS']):
                pass
            elif genre == '音乐频道' and any(keyword in channel_name for keyword in ['音乐', 'MTV']):
                pass
            elif genre == '其他频道':
                if any(genre_key in channel_name for genre_key in ['央视频道', '卫视频道', '国际频道', '体育频道', '电影频道', '少儿频道', '音乐频道']):
                    continue
            else:
                continue
                
            if channel_name in channel_counters:
                if channel_counters[channel_name] < 10:  # 每个频道最多10个源
                    file.write(f"{channel_name},{channel_url}\n")
                    channel_counters[channel_name] += 1
            else:
                file.write(f"{channel_name},{channel_url}\n")
                channel_counters[channel_name] = 1

    with open(f"{output_prefix}.txt", 'w', encoding='utf-8') as txt_file:
        txt_file.write('央视频道,#genre#\n')
        write_to_file(txt_file, unique_channels, '央视频道')
        txt_file.write('卫视频道,#genre#\n')
        write_to_file(txt_file, unique_channels, '卫视频道')
        txt_file.write('国际频道,#genre#\n')
        write_to_file(txt_file, unique_channels, '国际频道')
        txt_file.write('体育频道,#genre#\n')
        write_to_file(txt_file, unique_channels, '体育频道')
        txt_file.write('电影频道,#genre#\n')
        write_to_file(txt_file, unique_channels, '电影频道')
        txt_file.write('少儿频道,#genre#\n')
        write_to_file(txt_file, unique_channels, '少儿频道')
        txt_file.write('音乐频道,#genre#\n')
        write_to_file(txt_file, unique_channels, '音乐频道')
        txt_file.write('其他频道,#genre#\n')
        write_to_file(txt_file, unique_channels, '其他频道')

    with open(f"{output_prefix}.m3u", 'w', encoding='utf-8') as m3u_file:
        m3u_file.write('#EXTM3U\n')
        def write_to_m3u(file, results, genre):
            channel_counters = {}
            for result in results:
                channel_name, channel_url, _ = result
                # 改进的分类规则
                if genre == '央视频道' and 'CCTV' in channel_name:
                    pass
                elif genre == '卫视频道' and any(keyword in channel_name for keyword in ['卫视', 'TV']):
                    pass
                elif genre == '国际频道' and any(keyword in channel_name for keyword in ['BBC', 'CNN', 'NHK', 'FOX', 'DW', 'RT']):
                    pass
                elif genre == '体育频道' and any(keyword in channel_name for keyword in ['体育', '足球', '篮球', 'F1', 'NBA', 'NHL', 'MLB']):
                    pass
                elif genre == '电影频道' and any(keyword in channel_name for keyword in ['电影', 'MOVIE']):
                    pass
                elif genre == '少儿频道' and any(keyword in channel_name for keyword in ['少儿', '动画', 'KIDS']):
                    pass
                elif genre == '音乐频道' and any(keyword in channel_name for keyword in ['音乐', 'MTV']):
                    pass
                elif genre == '其他频道':
                    if any(genre_key in channel_name for genre_key in ['央视频道', '卫视频道', '国际频道', '体育频道', '电影频道', '少儿频道', '音乐频道']):
                        continue
                else:
                    continue
                    
                if channel_name in channel_counters:
                    if channel_counters[channel_name] < 10:  # 每个频道最多10个源
                        file.write(f"#EXTINF:-1 tvg-name=\"{channel_name}\" group-title=\"{genre}\" tvg-logo=\"\",{channel_name}\n")
                        file.write(f"{channel_url}\n")
                        channel_counters[channel_name] += 1
                else:
                    file.write(f"#EXTINF:-1 tvg-name=\"{channel_name}\" group-title=\"{genre}\" tvg-logo=\"\",{channel_name}\n")
                    file.write(f"{channel_url}\n")
                    channel_counters[channel_name] = 1
        write_to_m3u(m3u_file, unique_channels, '央视频道')
        write_to_m3u(m3u_file, unique_channels, '卫视频道')
        write_to_m3u(m3u_file, unique_channels, '国际频道')
        write_to_m3u(m3u_file, unique_channels, '体育频道')
        write_to_m3u(m3u_file, unique_channels, '电影频道')
        write_to_m3u(m3u_file, unique_channels, '少儿频道')
        write_to_m3u(m3u_file, unique_channels, '音乐频道')
        write_to_m3u(m3u_file, unique_channels, '其他频道')

    with open("speed.txt", 'w', encoding='utf-8') as speed_file:
        for result in unique_channels:
            speed_file.write(f"{','.join(result)}\n")

# 主入口函数
def main():
    parser = argparse.ArgumentParser(description='多模式IPTV频道批量探测与测速')
    parser.add_argument('--jsmpeg', help='jsmpeg-streamer模式csv文件')
    parser.add_argument('--txiptv', help='txiptv模式csv文件')
    parser.add_argument('--zhgxtv', help='zhgxtv模式csv文件')
    parser.add_argument('--output', default='itvlist', help='输出文件前缀')
    args = parser.parse_args()

    channels = []
    if args.jsmpeg:
        channels.extend(get_channels_alltv(args.jsmpeg))
    if args.zhgxtv:
        channels.extend(get_channels_hgxtv(args.zhgxtv))
    if args.txiptv:
        channels.extend(asyncio.run(get_channels_newnew(args.txiptv)))

    if not channels:
        print('请至少指定一个csv文件')
        return

    test_speed_and_output(channels, args.output)

if __name__ == "__main__":
    main()
