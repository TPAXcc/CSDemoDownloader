import tkinter as tk
from tkinter import filedialog
import ctypes
from bs4 import BeautifulSoup
import os
import icoextract
from urllib.parse import urlparse
import asyncio
import os
import aiohttp
from tqdm.asyncio import tqdm_asyncio
import math
from pathlib import Path
from typing import List, Tuple
import aiofiles
import aioconsole
import bz2

script_dir = os.path.dirname(os.path.abspath(__file__))




def select(_title="请选择文件", select_type="file", _multiple=False):
    """打开文件选择窗口，允许用户多选文件并返回列表"""
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    # 提取 explorer.exe 的图标并保存
    # if not os.path.exists('explorer_icon.ico'):
    #     explorer_path = r'C:\Windows\explorer.exe'
    #     ico = icoextract.IconExtractor(explorer_path)
    #     ico.export_icon('explorer_icon.ico')
        
    root = tk.Tk()
    # root.iconbitmap(os.path.join(script_dir, 'assets', 'explorer_icon.ico'))
    root.withdraw()  # 隐藏主窗口
    
    if select_type == "file":
        # 设置文件对话框参数
        paths = filedialog.askopenfilenames(
            title=_title,
            filetypes=[ ("CS2比赛记录", "*.html"), ("所有文件", "*.*")],  # 支持自定义过滤器
            multiple=_multiple
        )
    elif select_type == "folder":
        # 文件夹选择模式
        paths = filedialog.askdirectory(
            title=_title,
        ) 
    else:
        raise ValueError("无效的select_type参数，仅支持'file'或'directory'")
    
    root.destroy()  # 销毁窗口
    # os.remove('explorer_icon.ico')  # 删除临时图标文件
    
    if _multiple == False:
        return paths  # 返回单个路径
    elif _multiple == True:
        return list(paths)  # 返回路径列表
    
def extract_demo_links(html_file):
    """
    从HTML文件中提取符合特定结构的回放下载链接
    参数:
        html_file (str): HTML文件路径
    返回:
        list: 包含所有匹配的.dem.bz2链接的列表
    """
    with open(html_file, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')

    links = []
    # 匹配包含特定class组合的td标签
    for td in soup.find_all('td', class_='csgo_scoreboard_cell_noborder'):
        # 查找包含下载按钮的div标签
        download_div = td.find('div', class_=lambda x: x and {
                               'btnv6_blue_hoverfade', 'btn_medium', 'csgo_scoreboard_btn_gotv'}.issubset(x.split()))
        if download_div:
            # 获取父级a标签的href属性
            a_tag = download_div.find_parent('a')
            if a_tag and a_tag.has_attr('href'):
                links.append(a_tag['href'])
    
    return links

class AsyncDownloader:
    def __init__(
        self,
        urls: List[str],
        download_dir: str = Path(),
        max_workers: int = 4,
        retries: int = 5,
        chunk_size: int = 1024 * 1024,  # 1MB
        max_chunks_per_task: int = 4    # 分片限制
    ):
        self.urls = urls
        self.max_workers = max_workers
        self.retries = retries
        self.chunk_size = chunk_size
        self.max_chunks = max_chunks_per_task
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.progress_bars = {}
        self.downloaded_count = 0  # 下载计数器
        self.files_count = 0  # 文件计数器
        self.filenames = []  # 所有文件名

    async def _get_file_info(self, session: aiohttp.ClientSession, url: str) -> Tuple[int, bool]:
        """获取文件大小和分片支持情况"""
        async with session.head(url) as response:
            if response.status != 200:
                raise ValueError(f"HEAD请求失败: {response.status}")
            
            supports_range = 'bytes' in response.headers.get('Accept-Ranges', '')
            total_size = int(response.headers.get('Content-Length', 0))
            return total_size, supports_range

    def _calculate_chunks(self, total_size: int) -> List[Tuple[int, int]]:
        """计算分片范围"""
        chunk_count = min(math.ceil(total_size / (self.chunk_size * 8)), 8)  # 文件大小除以8自动分片
        chunk_size = total_size // chunk_count
        chunks = []
        for i in range(chunk_count):
            start = i * chunk_size
            end = (i+1)*chunk_size - 1 if i < chunk_count-1 else total_size-1
            chunks.append((start, end))
        return chunks

    async def _download_chunk(
        self,
        session: aiohttp.ClientSession,
        url: str,
        filepath: Path,
        chunk_range: Tuple[int, int]
    ):
        """分片下载核心逻辑"""
        start, end = chunk_range
        headers = {"Range": f"bytes={start}-{end}"}
        progress_key = f"{url}_{start}-{end}"

        for attempt in range(self.retries):
            try:
                async with session.get(url, headers=headers) as response:
                    response.raise_for_status()
                    
                    async with aiofiles.open(filepath, "r+b") as f:
                        await f.seek(start)
                        async for chunk in response.content.iter_chunked(self.chunk_size):
                            await f.write(chunk)
                            self.progress_bars[url].update(len(chunk))
                    return
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == self.retries - 1:
                    print(f"\n分片下载失败: {filepath.name} [{start}-{end}] ({str(e)})")
                await asyncio.sleep(2 ** attempt)

    async def _download_file(self, session: aiohttp.ClientSession, url: str):
        """文件下载主逻辑"""
        filename = url.split("/")[-1] or f"file_{hash(url)}"
        filepath = self.download_dir / filename
        
        
        # 获取服务器端文件信息
        total_size, supports_range = await self._get_file_info(session, url)
        if total_size == 0:
            raise ValueError("无法获取文件大小")

        # 检查本地文件是否存在并比较大小
        if filepath.exists():
            local_size = filepath.stat().st_size
            if local_size == total_size:
                print(f"文件 {filename} 已存在且大小相同，跳过下载。")
                self.files_count += 1
                self.filenames.append(filename)
                return  # 直接跳过下载
            else:
                # 用户选择是否覆盖（异步输入）
                choice = await aioconsole.ainput(
                    f"文件 {filename} 已存在但大小不同（本地: {local_size}，服务器: {total_size}） 是否覆盖？(y/n): "
                ).strip().lower()
                if choice != 'y':
                    print(f"跳过下载 {filename}")
                    self.filenames.append(filename)
                    return
                filepath.unlink(missing_ok=True)  # 删除旧文件
                
        try:
            total_size, supports_range = await self._get_file_info(session, url)
            if total_size == 0:
                raise ValueError("无法获取文件大小")

            # 初始化进度条
            self.progress_bars[url] = tqdm_asyncio(
                total=total_size,
                desc=f"下载 {filename}",
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                leave=False
            )

            # 预创建空文件
            if not filepath.exists():
                async with aiofiles.open(filepath, "wb") as f:
                    await f.truncate(total_size)

            # 分片下载逻辑
            if supports_range:
                chunks = self._calculate_chunks(total_size)
                semaphore = asyncio.Semaphore(self.max_chunks)  # 分片专用信号量
                
                tasks = []
                for chunk in chunks:
                    task = asyncio.create_task(self._download_chunk(session, url, filepath, chunk))
                    task.add_done_callback(lambda _: semaphore.release())
                    tasks.append(task)
                
                await asyncio.gather(*tasks)
            else:
                # 回退到整体下载
                async with session.get(url) as response:
                    async with aiofiles.open(filepath, "wb") as f:
                        async for chunk in response.content.iter_chunked(self.chunk_size):
                            await f.write(chunk)
                            self.progress_bars[url].update(len(chunk))
            self.downloaded_count += 1
            self.files_count += 1
            self.filenames.append(filename)
    
        finally:
            if url in self.progress_bars:
                self.progress_bars[url].close()
                del self.progress_bars[url]

    def files_name(self) -> List[str]:
        """返回所有服务器文件名列表"""
        return self.filenames
    
    def downloaded_num(self) -> int:
        """返回已成功下载的文件总数"""
        return self.downloaded_count
    
    def files_num(self) -> int:
        """返回文件总数"""
        return self.files_count

    async def run(self):
        """任务运行入口"""
        semaphore = asyncio.Semaphore(self.max_workers)
        
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=False),
            timeout=aiohttp.ClientTimeout(total=3600)
        ) as session:
            tasks = []
            for url in self.urls:
                await semaphore.acquire()
                task = asyncio.create_task(self._download_file(session, url))
                task.add_done_callback(lambda _: semaphore.release())
                tasks.append(task)
            
            await tqdm_asyncio.gather(*tasks, desc=f"总进度",)

class AsyncBZ2Decompressor:
    def __init__(
        self,
        bz2_files_list: List[str],
        extract_dir: str,
        max_workers: int = 4,
        chunk_size: int = 1024*1024  # 1MB
    ):
        self.bz2_files_list = bz2_files_list
        self.extract_dir = Path(extract_dir)
        self.max_workers = max_workers
        self.chunk_size = chunk_size
        self._extracted_files = []
        self._lock = None
        self.extract_dir.mkdir(parents=True, exist_ok=True)

    async def _stream_extract(self, src_path: Path):
        """流式解压单个BZ2文件"""
        if self._lock is None:
            self._lock = asyncio.Lock()
        
        dest_path = self.extract_dir / src_path.stem
        total_size = src_path.stat().st_size
        
        try:
            with (
                bz2.BZ2File(src_path, 'rb') as f_in,
                open(dest_path, 'wb') as f_out,
                tqdm_asyncio(
                    total=total_size,
                    desc=f"解压 {src_path.name}",
                    unit='B',
                    unit_scale=True,
                    unit_divisor=1024,
                    ascii=True,
                    dynamic_ncols=True
                ) as pbar
            ):
                while chunk := f_in.read(self.chunk_size):
                    f_out.write(chunk)
                    pbar.update(len(chunk))
                    await asyncio.sleep(0)  # 让出事件循环控制权

            async with self._lock:
                self._extracted_files.append(str(dest_path))
                
            return True
        except Exception as e:
            print(f"解压失败 {src_path.name}: {str(e)}")
            return False

    def files_num(self) -> int:
        """已解压文件数量"""
        return len(self._extracted_files)

    def files_name(self) -> List[str]:
        """已解压文件路径列表"""
        return self._extracted_files
    
    async def run(self):
        """异步入口函数"""
        sem = asyncio.Semaphore(self.max_workers)
        
        async def worker(src_path):
            async with sem:
                return await self._stream_extract(src_path)

        tasks = [worker(Path(f)) for f in self.bz2_files_list]
        results = await tqdm_asyncio.gather(
            *tasks,
            desc="总进度",
            unit="文件",
            position=0
        )
        
        return all(results)

def main():
   #解析链接
    print("请选择 CS2 比赛记录文件")
    selected_files = select("请选择 CS2 比赛记录文件", "file", True)
    if not selected_files:
        print("未选择CS2 比赛记录文件，程序退出。")
        
    print(f"已选则 {len(selected_files)} 个 CS2 比赛记录文件：")
    for file in selected_files:
        print("- ", file)
    
    print("开始解析 html 文件...")   
    if selected_files:
        demo_links = extract_demo_links(selected_files[0])  # 处理第一个文件
        # 或者处理所有文件
        demo_links = []
        file_num =0
        for file in selected_files:
            file_num += 1
            demo_links.extend(extract_demo_links(file))
    print(f"已解析到 {len(demo_links)} 个 Demo 链接 ：")
    for link in demo_links:
        print("- ", link)
    
    
    
    # 处理demo链接
    print("请选择 Demo 下载文件夹") 
    traget_folder = select("请选择 Demo 下载文件夹", "folder")     
    print(f"已选择的 Demo 文件夹 {traget_folder} ")
    
    print("开始下载 Demo 文件...")
    tmp_folder = traget_folder + "/tmp"
    downloader = AsyncDownloader(
        urls=demo_links,  # 下载链接列表
        download_dir=tmp_folder,  # 下载目录
        max_workers=4,  # 最大并发下载任务数
        max_chunks_per_task=4  # 单任务最大分片数
    )
    asyncio.run(downloader.run())
    print(f"\n已下载 {downloader.downloaded_num()} 个 Demo 文件, 当前存在 {downloader.files_num()} 个 Demo 文件:")
    for file in downloader.files_name():
        print("- ", file)

    print("开始解压 Demo 文件至目标文件夹...")
    decompress_files = [os.path.join(tmp_folder, name) for name in downloader.files_name()]
    decompressor = AsyncBZ2Decompressor(
        bz2_files_list=decompress_files,  # 下载的文件列表
        extract_dir=traget_folder  # 解压目录
    )
    asyncio.run(decompressor.run())
    print(f"已解压 {len(decompressor.files_name())} 个 Demo 文件至 {traget_folder} 文件夹：")
    for file in decompressor.files_name():
        print("- ", file)
        
    os.remove(tmp_folder)  # 删除临时文件夹
    print(f"已删除 .bz2 缓存文件 {downloader.files_num()} 个")

if __name__ == "__main__":
    print("""欢迎使用 CS2 Demo 下载器
作者：TPAX_
版本：1.0.0
更新日期：2025-05-13
如有问题请联系作者：TPAX_
""")

    print("""请选择需要下载的比赛类型：
a. 官匹优先
b. 官匹竞技
c. 官匹其它类型
输入 a/b/c/ab/ac/bc/abc 选择需要下载的比赛类型""")
    match_group_select = input().strip().lower()
    
    dict_match_group = {
        "a": ["官匹优先"],
        "b": ["官匹竞技"],
        "c": ["官匹其它类型"],
        "ab": ["官匹优先", "官匹竞技"],
        "ac": ["官匹优先", "官匹其它类型"],
        "bc": ["官匹竞技", "官匹其它类型"],
        "abc": ["官匹优先", "官匹竞技", "官匹其它类型"]
    }
    print("已选择 "+"".join(dict_match_group.get(match_group_select, [])))
    dict_match_group_grade = {
        "a": "-官匹优先下载教程\n 3a.点击浏览器URL框，在后面添加 gcpd/730/?tab=matchhistorypremier\n 4a.右键页面，选择保存/另存为",
        "b": "-官匹竞技下载教程\n 3b.点击浏览器URL框，在后面添加 gcpd/730/?tab=matchhistorycompetitivepermap\n 4a.右键页面，选择保存/另存为",
        "c": "-官匹其它类型下载教程\n 3c.点击浏览器URL框，在后面添加 gcpd/730/\n 4a.打开你要下载的比赛类型（确保你能看到“下载回放”按钮），右键页面,目标保存/另存为",
    }
    
    print("1.使用浏览器（推荐 Edge）打开 Steam 官网 https://store.steampowered.com/login/ 并登录需要下载 Demo 的 Steam 账号") 
    print("2.点击上方的个人资料")
    def option_a():
        print(dict_match_group_grade.get("a"))

    def option_b():
        print(dict_match_group_grade.get("b"))

    def option_c():
        print(dict_match_group_grade.get("c"))
    actions = {
        'a': option_a,
        'b': option_b,
        'c': option_c
    }
    for choice in match_group_select:
        actions[choice]()
    print("5.选择所有保存的 CS2 比赛记录 (html 文件)")
    input("按回车键继续...")
    main()
    exit(1)
    
    
    
